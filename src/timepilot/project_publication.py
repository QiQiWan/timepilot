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


class PublicationLeaseLost(RuntimeError):
    pass


class ProjectPublication:
    def __init__(self, catalog, native=None, *, platform=None, generic=None):
        self.catalog=catalog
        self.native=native
        self.generic=generic or GenericAssetStore(catalog.imports)
        self.platform=platform or DataPlatform(catalog,native,self.generic)

    def publish(self,pid,iid,candidate_id,request:PublishGeneral,*,progress=None):
        self.catalog.writable(pid)
        self.catalog.require_import(pid,iid)
        def notify(phase):
            if progress:progress(phase)
        notify('publishing')
        meta=self.generic.publish(iid,candidate_id,request)
        notify('registering_bi')
        existing=next((item for item in self.catalog.generic_for(pid)
                       if item['import_id']==iid and item['candidate_id']==candidate_id
                       and item['version_id']==meta['version_id']),None)
        if existing is not None and self.native is not None:
            try:
                self.native.asset(existing['native_asset_id'])
                record=self.platform.register(pid,existing,meta)
                if record['name']!=request.name.strip():
                    self.platform.rename(pid,record['asset_id'],request.name)
                notify('checking_quality')
                self.platform.evaluate_quality(pid,record['asset_id'],meta['version_id'])
                notify('complete')
                return {'project_id':pid,'status':'ready','reused':True,
                        'native_asset_id':existing['native_asset_id'],'meta':meta}
            except (KeyError,FileNotFoundError):
                # An interrupted migration may leave a stale BI linkage. Keep
                # immutable CSV; retry registration without touching the source.
                pass
        if self.native is None:
            notify('prepared')
            return {'project_id':pid,'status':'prepared','native_bi_ready':False,
                    'message':'标准版本已保存；正式 BI 尚未准备好，可从发布任务重试', 'meta':meta}
        from timepilot.native_bi import NativeBIDependencyError
        import_path=self.generic.version_file(iid,candidate_id,meta['version_id'])
        lineage={'type':'project_generic_table_v016','project_id':pid,
                 'import_id':iid,'candidate_id':candidate_id,
                 'version_id':meta['version_id'],'source_sha256':meta['source_sha256'],
                 'data_sha256':meta['data_sha256'],
                 'confirmed_roles':meta['confirmed_roles']}
        try:
            result=self.native.import_from_path(import_path,meta['name'],lineage)
        except NativeBIDependencyError as exc:
            notify('prepared')
            return {'project_id':pid,'status':'prepared','native_bi_ready':False,
                    'message':'标准版本已保存；'+str(exc),'meta':meta}
        self.catalog.attach_generic(pid,iid,candidate_id,meta['version_id'],result['asset_id'])
        record=self.platform.register(pid,{'import_id':iid,'candidate_id':candidate_id,
                       'version_id':meta['version_id'],'native_asset_id':result['asset_id']},meta)
        if record['name']!=request.name.strip():
            self.platform.rename(pid,record['asset_id'],request.name)
        notify('checking_quality')
        self.platform.evaluate_quality(pid,record['asset_id'],meta['version_id'])
        notify('complete')
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
        obj.pop('lease_owner',None)
        obj.pop('lease_until',None)
        return obj

    def submit(self,pid,iid,candidate_id,request:PublishGeneral):
        self.catalog.writable(pid)
        self.catalog.require_import(pid,iid)
        self.service.generic._candidate(iid,candidate_id,request.header_row)
        if not request.confirm:raise SmartImportError('请先确认源数据字段和发布范围')
        # A second click while queued/running reuses the existing job rather
        # than introducing another writer for the same immutable asset.
        encoded=json.dumps(request.model_dump(mode='json'),ensure_ascii=False,sort_keys=True)
        with self.catalog._lock,self.catalog._db() as db:
            same=db.execute('''SELECT * FROM project_publication_jobs WHERE project_id=?
             AND import_id=? AND candidate_id=? AND request_json=?
             AND status IN ('queued','running','succeeded','prepared')
             ORDER BY created_at DESC LIMIT 1''',(pid,iid,candidate_id,encoded)).fetchone()
            if same:return self._public(same)
            jid=str(uuid4());t=time.time()
            db.execute('''INSERT INTO project_publication_jobs
                (job_id,project_id,import_id,candidate_id,request_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)''',(jid,pid,iid,candidate_id,encoded,t,t))
        return self.get(pid,jid)

    def get(self,pid,jid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('SELECT * FROM project_publication_jobs WHERE project_id=? AND job_id=?',
                           (pid,self._id(jid))).fetchone()
        if row is None:raise KeyError('项目中不存在该发布任务')
        return self._public(row)

    def list(self,pid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows=db.execute('SELECT * FROM project_publication_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 100',(pid,)).fetchall()
        return [self._public(row) for row in rows]

    def retry(self,pid,jid):
        self.catalog.writable(pid)
        job=self.get(pid,jid)
        if job['status'] not in ('failed','prepared') or job['attempts']>=5:
            raise SmartImportError('当前任务无法重试，请确认任务状态和重试次数')
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''UPDATE project_publication_jobs SET status='queued',stage='queued',
                 error=NULL,updated_at=? WHERE project_id=? AND job_id=? AND status IN ('failed','prepared')''',
                 (time.time(),pid,jid))
        return self.get(pid,jid)

    def checkpoint(self,pid,jid):
        job=self.get(pid,jid)
        request=PublishGeneral.model_validate(job['request'])
        result=self.service.generic.publish_checkpoint(job['import_id'],job['candidate_id'],request)
        return {'job_id':jid,'status':job['status'],'stage':job['stage'],
                'heartbeat_at':job['heartbeat_at'],'checkpoint':result}

    def _claim(self):
        with self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE');now=time.time()
            db.execute('''UPDATE project_publication_jobs SET status='failed',stage='failed',
                error='连续中断已达到发布重试上限',updated_at=? WHERE status='running'
                AND lease_until<? AND attempts>=5''',(now,now))
            row=db.execute('''SELECT * FROM project_publication_jobs WHERE
                (status='queued' OR (status='running' AND lease_until<?)) AND attempts<5
                ORDER BY created_at LIMIT 1''',(now,)).fetchone()
            if row is None:return None
            owner=str(uuid4())
            db.execute('''UPDATE project_publication_jobs SET status='running',stage='verifying',
                attempts=attempts+1,lease_owner=?,lease_until=?,heartbeat_at=?,updated_at=?
                WHERE job_id=?''',(owner,now+self.lease_seconds,now,now,row['job_id']))
            return {**dict(row),'lease_owner':owner}

    def _beat(self,jid,owner,stage=None):
        with self.catalog._db() as db:
            now=time.time()
            if stage is None:
                result=db.execute('''UPDATE project_publication_jobs SET heartbeat_at=?,lease_until=?,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running' ''',
                    (now,now+self.lease_seconds,now,jid,owner))
            else:
                result=db.execute('''UPDATE project_publication_jobs SET stage=?,heartbeat_at=?,lease_until=?,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running' ''',
                    (stage,now,now+self.lease_seconds,now,jid,owner))
        return result.rowcount==1

    def _owned(self,jid,owner):
        with self.catalog._db() as db:
            row=db.execute('''SELECT 1 FROM project_publication_jobs WHERE job_id=? AND lease_owner=?
                AND status='running' AND lease_until>?''',(jid,owner,time.time())).fetchone()
        if row is None:raise PublicationLeaseLost('发布任务租约已失效，停止提交任务结果')

    def work_once(self):
        item=self._claim()
        if item is None:return None
        jid=item['job_id'];owner=item['lease_owner'];stop=threading.Event();lost=threading.Event()
        def beat():
            while not stop.wait(min(10,max(1,self.lease_seconds/4))):
                try:
                    if not self._beat(jid,owner):lost.set();return
                except sqlite3.Error:continue
        thread=threading.Thread(target=beat,name='timepilot-publish-heartbeat',daemon=True)
        thread.start()
        def notify(phase):
            if lost.is_set():raise PublicationLeaseLost('发布任务租约已丢失')
            if not self._beat(jid,owner,phase):raise PublicationLeaseLost('发布任务租约已丢失')
        try:
            request=PublishGeneral.model_validate_json(item['request_json'])
            result=self.service.publish(item['project_id'],item['import_id'],item['candidate_id'],request,progress=notify)
            self._owned(jid,owner)
            state='succeeded' if result['status']=='ready' else 'prepared'
            stored={k:v for k,v in result.items() if k!='meta'}
            with self.catalog._db() as db:
                updated=db.execute('''UPDATE project_publication_jobs SET status=?,stage=?,result_json=?,
                    error=NULL,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?
                    AND lease_owner=? AND status='running' ''',
                    (state,'complete' if state=='succeeded' else 'prepared',
                     json.dumps(stored,ensure_ascii=False),time.time(),jid,owner))
            if updated.rowcount!=1:raise PublicationLeaseLost('发布任务完成时租约失效')
            return self.get(item['project_id'],jid)
        except Exception as exc:
            with self.catalog._db() as db:
                db.execute('''UPDATE project_publication_jobs SET status='failed',stage='failed',
                  error=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=? AND lease_owner=?''',
                  (str(exc)[:700],time.time(),jid,owner))
            return self.get(item['project_id'],jid)
        finally:
            stop.set();thread.join(timeout=2)
