"""Idempotent project publication + persisted local job leases.

The original upload, prepared generic CSV, Parquet BI copy and project link are
separate durable stages. A worker may resume safely after a process restart;
never announce an asset ready before its project link was committed.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from uuid import UUID, uuid4

from timepilot.generic_assets import GenericAssetStore, PublishGeneral
from timepilot.data_platform import DataPlatform
from timepilot.smart_import import SmartImportError
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.publication_reconciliation import PublicationReconciliation
from timepilot.publication_identity import publication_key, region_publication_lock
from timepilot.publication_journal import PublicationJournal


class PublicationLeaseLost(RuntimeError):
    pass


class ProjectPublication:
    def __init__(self, catalog, native=None, *, platform=None, generic=None):
        self.catalog=catalog
        self.native=native
        self.generic=generic or GenericAssetStore(catalog.imports)
        self.platform=platform or DataPlatform(catalog,native,self.generic)

    def publish(self,pid,iid,candidate_id,request:PublishGeneral,*,progress=None):
        # Includes legacy synchronous entry, queued jobs and batch children.
        # A crash releases the OS lock; the inventory gate below still refuses
        # uncertain/partial external writes after restart.
        with region_publication_lock(self.catalog,pid,iid,candidate_id):
            return self._publish_locked(pid,iid,candidate_id,request,progress=progress)

    def _publish_locked(self,pid,iid,candidate_id,request:PublishGeneral,*,progress=None):
        self.catalog.writable(pid)
        self.catalog.require_import(pid,iid)
        # Recheck *inside* the region lock. A sync API pre-check might have
        # raced with another worker completing an uncertain external write.
        with self.catalog._db() as db:
            blocked=db.execute('''SELECT job_id FROM project_publication_jobs
                WHERE project_id=? AND import_id=? AND candidate_id=? AND
                ((status='failed' AND stage='needs_reconciliation') OR
                 (status='cancelled' AND stage='reconciled_closed')) LIMIT 1''',
                (pid,iid,candidate_id)).fetchone()
        if blocked:
            raise SmartImportError('该区域已有需核对或人工关闭的发布，禁止重新写入：'+blocked['job_id'])
        def notify(phase, receipt=None):
            if progress:
                if receipt is None:
                    progress(phase)
                else:
                    progress(phase, receipt=receipt)
        notify('publishing')
        meta=self.generic.publish(iid,candidate_id,request)
        notify('csv_prepared', receipt={
            'version_id':meta['version_id'], 'csv_sha256':meta['data_sha256'],
            'source_sha256':meta['source_sha256'], 'row_count':meta['row_count']})
        notify('registering_bi')
        existing=next((item for item in self.catalog.generic_for(pid)
                       if item['import_id']==iid and item['candidate_id']==candidate_id
                       and item['version_id']==meta['version_id']),None)
        if existing is not None and self.native is not None:
            # A link alone cannot establish data integrity. Always force a
            # checksum read before adopting the existing Parquet in a new
            # request (notably a rename or retry after a worker crash).
            aid=existing['native_asset_id']
            try:
                stored=self.native.asset(aid)
            except (KeyError,FileNotFoundError):
                raise SmartImportError('既有关联的原生 BI 资产缺失，禁止自动重建，请进行发布对账') from None
            expected={'project_id':pid,'import_id':iid,'candidate_id':candidate_id,
                      'version_id':meta['version_id'],
                      'source_sha256':meta['source_sha256'],
                      'data_sha256':meta['data_sha256']}
            verifier=getattr(self.native,'verify',None)
            if (not callable(verifier) or
                any((stored.get('lineage') or {}).get(k)!=v for k,v in expected.items()) or
                stored.get('row_count')!=meta['row_count'] or
                not verifier(aid,force=True).get('valid')):
                raise SmartImportError('已关联的原生 BI 资产来源或物理 SHA256 校验失败，请人工核对')
            try:
                record=self.platform.register(pid,existing,meta)
                if record['name']!=request.name.strip():
                    self.platform.rename(pid,record['asset_id'],request.name)
                notify('project_linked', receipt={'native_asset_id':aid})
                notify('checking_quality')
                self.platform.evaluate_quality(pid,record['asset_id'],meta['version_id'])
                notify('ready_to_commit')
                return {'project_id':pid,'status':'ready','reused':True,
                        'native_asset_id':existing['native_asset_id'],'meta':meta}
            except (KeyError,FileNotFoundError):
                # The unified catalogue is inconsistent; do not rewrite it
                # or manufacture another physical snapshot automatically.
                raise SmartImportError('既有资产目录关联缺失，请先复核数据库一致性') from None
        if self.native is None:
            notify('prepared')
            return {'project_id':pid,'status':'prepared','native_bi_ready':False,
                    'message':'标准版本已保存；正式 BI 尚未准备好，可从发布任务重试', 'meta':meta}
        # A previous process may have committed native Parquet metadata, then
        # crashed before associating the asset with its project. Adopt exactly
        # one independently verified match, never publish a duplicate copy.
        lookup=getattr(self.native, 'find_published_candidates', None)
        if callable(lookup):
            inventory=lookup(pid,iid,candidate_id,meta['version_id'])
            if (not inventory.get('complete')
                    or inventory.get('unregistered_snapshots') is None
                    or inventory.get('unregistered_snapshots') != 0):
                raise SmartImportError('原生 BI 元数据或孤立快照清点未完成；为避免重复写入，拒绝再次发布')
            candidates=inventory.get('asset_ids') or []
            if len(candidates)>1:
                raise SmartImportError('存在多个相同来源的原生 BI 产物，请先核对重复版本')
            if candidates:
                aid=candidates[0]
                stored=self.native.asset(aid)
                expected={'project_id':pid,'import_id':iid,'candidate_id':candidate_id,
                          'version_id':meta['version_id'],
                          'source_sha256':meta['source_sha256'],
                          'data_sha256':meta['data_sha256']}
                if (not all((stored.get('lineage') or {}).get(k)==v for k,v in expected.items())
                    or stored.get('row_count')!=meta['row_count']
                    or not self.native.verify(aid,force=True).get('valid')):
                    raise SmartImportError('发现来源相似但完整性不一致的原生 BI 产物，需人工核对')
                self.catalog.attach_generic(pid,iid,candidate_id,meta['version_id'],aid)
                link={'import_id':iid,'candidate_id':candidate_id,
                      'version_id':meta['version_id'],'native_asset_id':aid}
                record=self.platform.register(pid,link,meta)
                if record['name']!=request.name.strip():
                    self.platform.rename(pid,record['asset_id'],request.name)
                notify('project_linked', receipt={'native_asset_id':aid})
                notify('checking_quality')
                self.platform.evaluate_quality(pid,record['asset_id'],meta['version_id'])
                notify('ready_to_commit')
                return {'project_id':pid,'status':'ready','reused':True,
                        'native_asset_id':aid,'meta':meta}
        from timepilot.native_bi import NativeBIDependencyError
        import_path=self.generic.version_file(iid,candidate_id,meta['version_id'])
        key=publication_key(pid,iid,candidate_id,meta['lineage']['recipe_sha256'])
        lineage={'type':'project_generic_table_v016','project_id':pid,
                 'import_id':iid,'candidate_id':candidate_id,
                 'publication_key':key,
                 'version_id':meta['version_id'],'source_sha256':meta['source_sha256'],
                 'data_sha256':meta['data_sha256'],
                 'confirmed_roles':meta['confirmed_roles']}
        try:
            result=self.native.import_from_path(import_path,meta['name'],lineage)
        except NativeBIDependencyError as exc:
            notify('prepared')
            return {'project_id':pid,'status':'prepared','native_bi_ready':False,
                    'message':'标准版本已保存；'+str(exc),'meta':meta}
        notify('native_registered', receipt={'native_asset_id':result['asset_id']})
        self.catalog.attach_generic(pid,iid,candidate_id,meta['version_id'],result['asset_id'])
        record=self.platform.register(pid,{'import_id':iid,'candidate_id':candidate_id,
                       'version_id':meta['version_id'],'native_asset_id':result['asset_id']},meta)
        if record['name']!=request.name.strip():
            self.platform.rename(pid,record['asset_id'],request.name)
        notify('project_linked', receipt={'native_asset_id':result['asset_id']})
        notify('checking_quality')
        self.platform.evaluate_quality(pid,record['asset_id'],meta['version_id'])
        notify('ready_to_commit')
        return {'project_id':pid,'status':'ready','reused':False,
                'native_asset_id':result['asset_id'],'meta':meta}


class ProjectPublicationJobs:
    """SQLite queue with bounded leases, heartbeat, safe staged retries."""
    def __init__(self,catalog,native=None,*,lease_seconds=300):
        self.catalog=catalog
        self.service=ProjectPublication(catalog,native)
        self.lease_seconds=max(30,int(lease_seconds))
        with self.catalog._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS project_publication_jobs(
                job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                import_id TEXT NOT NULL,candidate_id TEXT NOT NULL,
                request_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,lease_until REAL,heartbeat_at REAL,
                error TEXT,result_json TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,
                CHECK(status IN ('queued','running','succeeded','prepared','failed','cancelled')));
            CREATE INDEX IF NOT EXISTS idx_publication_project ON project_publication_jobs(project_id,created_at);
            ''')
            # Legacy catalog upgrades can be initiated by different processes.
            # SQLite's writer transaction serializes column inspections/DDL.
            db.execute('BEGIN IMMEDIATE')
            columns={r[1] for r in db.execute('PRAGMA table_info(project_publication_jobs)')}
            for column in ('owner_id','source_version_id','publication_key'):
                if column not in columns:
                    db.execute(f'ALTER TABLE project_publication_jobs ADD COLUMN {column} TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS project_publication_identities(
                publication_key TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,import_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,source_version_id TEXT NOT NULL,
                job_id TEXT NOT NULL,updated_at REAL NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_publication_source_version
                ON project_publication_identities(project_id,import_id,candidate_id,source_version_id)''')
            PublicationJournal.initialize(db)
        self.reconciliation = PublicationReconciliation(self)

    @staticmethod
    def _id(value):
        try:return str(UUID(str(value)))
        except (TypeError,ValueError):raise SmartImportError('发布任务编号无效') from None

    @staticmethod
    def _public(row):
        obj=dict(row)
        obj['request']={k:v for k,v in json.loads(obj.pop('request_json')).items()
                        if k in ('name','header_row','roles')}
        obj['result']=json.loads(obj.pop('result_json')) if obj.get('result_json') else None
        obj['reconciliation_required'] = obj.get('stage') == 'needs_reconciliation'
        obj['reconciliation_reviewed'] = bool(obj.pop('reviewed',False))
        obj.pop('lease_owner',None)
        obj.pop('lease_until',None)
        return obj

    def assert_no_unresolved(self,pid,iid,candidate_id):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT job_id FROM project_publication_jobs
                WHERE project_id=? AND import_id=? AND candidate_id=?
                  AND ((status='failed' AND stage='needs_reconciliation')
                       OR (status='cancelled' AND stage='reconciled_closed'))
                LIMIT 1''',(pid,iid,candidate_id)).fetchone()
        if row is not None:
            raise SmartImportError('该区域仍有未核对的发布任务，请先完成产物对账：'+row['job_id'])

    def submit(self,pid,iid,candidate_id,request:PublishGeneral):
        self.catalog.writable(pid)
        self.catalog.require_import(pid,iid)
        if not request.confirm:raise SmartImportError('请先确认源数据字段和发布范围')
        # The full recipe/source SHA is the authoritative identity. User-facing
        # names can change without creating a second physical Parquet version.
        identity=self.service.generic.expected_version(iid,candidate_id,request)
        key=publication_key(pid,iid,candidate_id,identity['identity_sha256'])
        principal=CURRENT_PRINCIPAL.get()
        if self.catalog.security is not None and principal is None:
            raise SecurityError('发布请求需要登录用户身份')
        owner_id=principal.user_id if principal else 'local'
        encoded=json.dumps(request.model_dump(mode='json'),ensure_ascii=False,sort_keys=True)
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Legacy queued/running tasks must also be fenced, even when they
            # predate the v0.38.4 identity ledger.
            active=db.execute('''SELECT * FROM project_publication_jobs
                WHERE project_id=? AND import_id=? AND candidate_id=?
                  AND status IN ('queued','running')
                ORDER BY created_at LIMIT 1''',(pid,iid,candidate_id)).fetchone()
            if active:
                if active['request_json']==encoded:
                    return self._public(active)
                raise SmartImportError('该区域已有发布任务执行或排队，请查看任务：'+active['job_id'])
            unresolved=db.execute('''SELECT * FROM project_publication_jobs WHERE project_id=?
                AND import_id=? AND candidate_id=? AND (
                  (status='failed' AND stage='needs_reconciliation') OR
                  (status='cancelled' AND stage='reconciled_closed'))
                ORDER BY created_at DESC LIMIT 1''',
                (pid,iid,candidate_id)).fetchone()
            if unresolved:
                if unresolved['status']=='failed' and unresolved['request_json']==encoded:
                    return self._public(unresolved)
                raise SmartImportError('该数据区域存在待核对或已人工关闭的发布记录，禁止绕过审核重新发布')
            registered=db.execute('''SELECT i.job_id,j.* FROM project_publication_identities i
                LEFT JOIN project_publication_jobs j ON j.job_id=i.job_id
                WHERE i.publication_key=?''',(key,)).fetchone()
            if registered and registered['status'] is None:
                raise SmartImportError('来源版本登记存在损坏的任务引用，拒绝重复写入')
            if registered and registered['status'] in ('failed','queued','running'):
                if registered['request_json']==encoded:
                    return self._public(registered)
                raise SmartImportError('来源版本存在未处理的发布记录，请先处理原任务')
            if registered and registered['status'] in ('succeeded','prepared') and registered['request_json']==encoded:
                return self._public(registered)
            # Old jobs without the identity column can still be referenced.
            same=db.execute('''SELECT * FROM project_publication_jobs WHERE project_id=?
             AND import_id=? AND candidate_id=? AND request_json=?
             AND status IN ('succeeded','prepared')
             ORDER BY created_at DESC LIMIT 1''',(pid,iid,candidate_id,encoded)).fetchone()
            if same:
                db.execute('''INSERT INTO project_publication_identities
                    (publication_key,project_id,import_id,candidate_id,source_version_id,job_id,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(publication_key) DO UPDATE SET
                    job_id=excluded.job_id,updated_at=excluded.updated_at''',
                    (key,pid,iid,candidate_id,identity['version_id'],same['job_id'],time.time()))
                return self._public(same)
            jid=str(uuid4());t=time.time()
            db.execute('''INSERT INTO project_publication_jobs
                (job_id,project_id,import_id,candidate_id,request_json,owner_id,
                 source_version_id,publication_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (jid,pid,iid,candidate_id,encoded,owner_id,identity['version_id'],key,t,t))
            db.execute('''INSERT INTO project_publication_identities
                (publication_key,project_id,import_id,candidate_id,source_version_id,job_id,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(publication_key) DO UPDATE SET
                job_id=excluded.job_id,updated_at=excluded.updated_at''',
                (key,pid,iid,candidate_id,identity['version_id'],jid,t))
            PublicationJournal.append(db, job_id=jid, project_id=pid,
                phase='queued', status='queued', receipt={
                    'source_version_id':identity['version_id'], 'publication_key':key})
        return self.get(pid,jid)

    def get(self,pid,jid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT j.*,
                EXISTS(SELECT 1 FROM project_publication_reviews r WHERE r.job_id=j.job_id
                       AND r.project_id=j.project_id) AS reviewed
                FROM project_publication_jobs j WHERE j.project_id=? AND j.job_id=?''',
                           (pid,self._id(jid))).fetchone()
        if row is None:raise KeyError('项目中不存在该发布任务')
        return self._public(row)

    def list(self,pid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows=db.execute('''SELECT j.*,
                EXISTS(SELECT 1 FROM project_publication_reviews r WHERE r.job_id=j.job_id
                       AND r.project_id=j.project_id) AS reviewed
                FROM project_publication_jobs j WHERE j.project_id=?
                ORDER BY j.created_at DESC LIMIT 100''',(pid,)).fetchall()
        return [self._public(row) for row in rows]

    def journal(self,pid,jid):
        """Project-scoped, read-only receipt timeline for crash recovery."""
        self.get(pid,jid)
        with self.catalog._db() as db:
            return PublicationJournal.read(db, job_id=self._id(jid), project_id=pid)

    def publish_sync(self,pid,iid,candidate_id,request:PublishGeneral):
        """Existing immediate API semantics backed by the same durable job.

        If a background worker has already claimed the intent, return a
        truthful pending receipt. Never race the running worker for a write.
        """
        job=self.submit(pid,iid,candidate_id,request)
        was_complete=job['status']=='succeeded'
        if job['status']=='queued':
            self.work_once(only_job_id=job['job_id'])
            job=self.get(pid,job['job_id'])
        if job['status'] in ('succeeded','prepared'):
            response={**(job['result'] or {}), 'job_id':job['job_id'],
                      'job_status':job['status']}
            if was_complete:
                response['reused']=True
            # Preserve the historical immediate API response contract. The
            # durable queue intentionally omits large metadata from SQLite.
            version_id=job.get('source_version_id')
            if version_id:
                response['meta']=self.service.generic.version(iid,candidate_id,version_id)
            return response
        if job['status']=='failed':
            raise SmartImportError('持久发布任务执行失败，请核对任务与阶段回执：'+job['job_id'])
        if job['status']=='cancelled':
            raise SmartImportError('发布任务已关闭，禁止重复执行：'+job['job_id'])
        return {'project_id':pid, 'status':job['status'], 'job_id':job['job_id'],
                'stage':job['stage'], 'message':'已有后台 Worker 正在处理该发布任务'}

    def retry(self,pid,jid):
        self.catalog.writable(pid)
        job=self.get(pid,jid)
        if job.get('reconciliation_required'):
            raise SmartImportError('发布中断期间可能已产生外部写入；需要人工核对资产后才可重新发布')
        if job['status'] not in ('failed','prepared') or job['attempts']>=5:
            raise SmartImportError('当前任务无法重试，请确认任务状态和重试次数')
        with self.catalog._lock,self.catalog._db() as db:
            updated=db.execute('''UPDATE project_publication_jobs SET status='queued',stage='queued',
                 error=NULL,updated_at=? WHERE project_id=? AND job_id=? AND status IN ('failed','prepared')''',
                 (time.time(),pid,jid))
            if updated.rowcount!=1:
                raise SmartImportError('发布状态已改变，请刷新后重试')
            PublicationJournal.append(db,job_id=jid,project_id=pid,
                phase='queued',status='queued',receipt={'reason_code':'manual_retry'})
        return self.get(pid,jid)

    def checkpoint(self,pid,jid):
        job=self.get(pid,jid)
        request=PublishGeneral.model_validate(job['request'])
        result=self.service.generic.publish_checkpoint(job['import_id'],job['candidate_id'],request)
        return {'job_id':jid,'status':job['status'],'stage':job['stage'],
                'heartbeat_at':job['heartbeat_at'],'checkpoint':result}

    def _claim(self, only_job_id=None):
        with self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE');now=time.time()
            # Stages after verification may already have generated native BI
            # artifacts. Never automatically replay an uncertain external write.
            expired=db.execute('''SELECT job_id,project_id,stage FROM project_publication_jobs
                WHERE status='running' AND lease_until<=? AND stage NOT IN ('queued','verifying')''',
                (now,)).fetchall()
            db.execute('''UPDATE project_publication_jobs SET status='failed',
                stage='needs_reconciliation',error=?,updated_at=?,
                lease_owner=NULL,lease_until=NULL WHERE status='running' AND lease_until<=?
                AND stage NOT IN ('queued','verifying')''',
                ('发布中断期间外部产物状态未知；需人工核对，禁止自动重试',now,now))
            for item in expired:
                PublicationJournal.append(db,job_id=item['job_id'],project_id=item['project_id'],
                    phase='needs_reconciliation',status='failed',
                    receipt={'reason_code':'expired_lease_after_write_boundary'})
            exceeded=db.execute('''SELECT job_id,project_id FROM project_publication_jobs
                WHERE status='running' AND lease_until<=? AND attempts>=5''',(now,)).fetchall()
            db.execute('''UPDATE project_publication_jobs SET status='failed',stage='failed',
                error='连续中断已达到发布重试上限',updated_at=?,lease_owner=NULL,lease_until=NULL
                WHERE status='running' AND lease_until<=? AND attempts>=5''',(now,now))
            for item in exceeded:
                PublicationJournal.append(db,job_id=item['job_id'],project_id=item['project_id'],
                    phase='failed',status='failed',receipt={'reason_code':'retry_limit'})
            query='''SELECT * FROM project_publication_jobs WHERE
                (status='queued' OR (status='running' AND lease_until<=?)) AND attempts<5'''
            params=[now]
            if only_job_id is not None:
                query+=' AND job_id=?'
                params.append(self._id(only_job_id))
            row=db.execute(query+' ORDER BY created_at LIMIT 1',params).fetchone()
            if row is None:return None
            owner=str(uuid4())
            db.execute('''UPDATE project_publication_jobs SET status='running',stage='verifying',
                attempts=attempts+1,lease_owner=?,lease_until=?,heartbeat_at=?,updated_at=?
                WHERE job_id=?''',(owner,now+self.lease_seconds,now,now,row['job_id']))
            PublicationJournal.append(db,job_id=row['job_id'],project_id=row['project_id'],
                phase='verifying',status='running',receipt={
                    'source_version_id':row['source_version_id'],
                    'publication_key':row['publication_key']})
            return {**dict(row),'lease_owner':owner}

    def _beat(self,jid,owner,stage=None,receipt=None):
        with self.catalog._db() as db:
            now=time.time()
            if stage is None:
                result=db.execute('''UPDATE project_publication_jobs SET heartbeat_at=?,lease_until=?,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running' AND lease_until>?''',
                    (now,now+self.lease_seconds,now,jid,owner,now))
            else:
                result=db.execute('''UPDATE project_publication_jobs SET stage=?,heartbeat_at=?,lease_until=?,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running' AND lease_until>?''',
                    (stage,now,now+self.lease_seconds,now,jid,owner,now))
                if result.rowcount==1:
                    current=db.execute('SELECT project_id FROM project_publication_jobs WHERE job_id=?',(jid,)).fetchone()
                    PublicationJournal.append(db,job_id=jid,project_id=current['project_id'],
                        phase=stage,status='running',receipt=receipt)
        return result.rowcount==1

    def _owned(self,jid,owner):
        with self.catalog._db() as db:
            row=db.execute('''SELECT 1 FROM project_publication_jobs WHERE job_id=? AND lease_owner=?
                AND status='running' AND lease_until>?''',(jid,owner,time.time())).fetchone()
        if row is None:raise PublicationLeaseLost('发布任务租约已失效，停止提交任务结果')

    def work_once(self, only_job_id=None):
        item=self._claim(only_job_id=only_job_id)
        if item is None:return None
        jid=item['job_id'];owner=item['lease_owner'];stop=threading.Event();lost=threading.Event()
        def beat():
            while not stop.wait(min(10,max(1,self.lease_seconds/4))):
                try:
                    if not self._beat(jid,owner):lost.set();return
                except sqlite3.Error:continue
        thread=threading.Thread(target=beat,name='timepilot-publish-heartbeat',daemon=True)
        thread.start()
        def notify(phase,receipt=None):
            if lost.is_set():raise PublicationLeaseLost('发布任务租约已丢失')
            if not self._beat(jid,owner,phase,receipt=receipt):
                raise PublicationLeaseLost('发布任务租约已丢失')
        try:
            request=PublishGeneral.model_validate_json(item['request_json'])
            # The queued recipe must never silently switch to a modified source
            # before the first physical publication side effect.
            if item.get('source_version_id'):
                version=self.service.generic.expected_version(
                    item['import_id'],item['candidate_id'],request)['version_id']
                if version!=item['source_version_id']:
                    raise SmartImportError('来源数据或发布配方已变化；禁止用旧任务发布新版本')
            result=self.service.publish(item['project_id'],item['import_id'],item['candidate_id'],request,progress=notify)
            self._owned(jid,owner)
            state='succeeded' if result['status']=='ready' else 'prepared'
            stored={k:v for k,v in result.items() if k!='meta'}
            with self.catalog._db() as db:
                updated=db.execute('''UPDATE project_publication_jobs SET status=?,stage=?,result_json=?,
                    error=NULL,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?
                    AND lease_owner=? AND status='running' AND lease_until>?''',
                    (state,'complete' if state=='succeeded' else 'prepared',
                     json.dumps(stored,ensure_ascii=False),time.time(),jid,owner,time.time()))
                if updated.rowcount==1:
                    PublicationJournal.append(db,job_id=jid,project_id=item['project_id'],
                        phase='complete' if state=='succeeded' else 'prepared',status=state,
                        receipt={'native_asset_id':result.get('native_asset_id')})
            if updated.rowcount!=1:raise PublicationLeaseLost('发布任务完成时租约失效')
            return self.get(item['project_id'],jid)
        except Exception as exc:
            with self.catalog._db() as db:
                updated=db.execute('''UPDATE project_publication_jobs SET status='failed',
                  stage=CASE WHEN stage IN ('queued','verifying','publishing') THEN 'failed'
                             ELSE 'needs_reconciliation' END,
                  error=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?
                  AND lease_owner=? AND status='running' AND lease_until>?''',
                  (str(exc)[:700],time.time(),jid,owner,time.time()))
                if updated.rowcount==1:
                    failed=db.execute('SELECT stage FROM project_publication_jobs WHERE job_id=?',(jid,)).fetchone()
                    PublicationJournal.append(db,job_id=jid,project_id=item['project_id'],
                        phase=failed['stage'],status='failed',receipt={'error_type':type(exc).__name__})
            if updated.rowcount!=1:
                # Preserve an expired write for reconciliation on the next scan.
                return {'job_id':jid,'status':'lease_lost'}
            return self.get(item['project_id'],jid)
        finally:
            stop.set();thread.join(timeout=2)
