"""Durable FeatureLab v2 jobs: leased SQLite queue, SHA-pinned Parquet output.

The embedded single worker is integrated with app lifespan. Expired jobs can
be claimed by another worker. Retrying recomputes from immutable source; no
incomplete checkpoint is treated as a successful artifact.
"""
from __future__ import annotations
import json
import os
import socket
import threading
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from timepilot.feature_contracts import FeatureSpecV2,canonical,digest
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_profiler import FeatureProfiler
from timepilot.projects import now
from timepilot.storage import sha256_file
from timepilot.enterprise_security import CURRENT_PRINCIPAL,SecurityError


def deadline(minutes=45):return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()


class FeatureJobEngine:
    def __init__(self,projects,native,semantic,registry):
        self.projects=projects;self.native=native;self.semantic=semantic;self.registry=registry
        self.profiler=FeatureProfiler(semantic,native)
        self.root=projects.home/'feature_lab'
        self.worker_id=socket.gethostname()+'-'+str(os.getpid())+'-'+str(uuid4())[:8]

    def _editor(self,pid):self.registry._editor(pid)
    def _actor(self):return self.registry._actor()

    def create_profile(self,pid,aid,vid,request_id):
        self._editor(pid);src=self.semantic.source(pid,aid,vid)
        if not request_id or len(request_id)>120:raise ValueError('请求 ID 无效')
        contract=self.semantic.latest(pid,aid,vid,source=src)
        payload={'asset_id':aid,'version_id':vid,'source_sha256':src['sha256'],'contract_revision':contract.revision}
        return self._create_job(pid,'profile',aid,request_id,payload)

    def create_run(self,pid,sid,request_id):
        self._editor(pid);record=self.registry.get(pid,sid)
        if record['state'] not in ('draft','pending_review','approved'):
            raise ValueError('该特征集当前不能发起新的计算')
        self.registry.pinned(pid,record)
        payload={'set_id':sid,'revision':record['revision'],'source_sha256':record['source_sha256'],
                 'spec_sha256':record['spec_sha256']}
        return self._create_job(pid,'materialize',sid,request_id,payload)

    def create_preview(self,pid,sid,request_id,max_rows=50,scan_budget=200000):
        self._editor(pid)
        if type(max_rows) is not int or not 1<=max_rows<=100:
            raise ValueError('预览最多返回100条')
        if type(scan_budget) is not int or not 100<=scan_budget<=500000:
            raise ValueError('预览扫描预算必须在100到500000行之间')
        rec=self.registry.get(pid,sid)
        self.registry.pinned(pid,rec)
        src=self.semantic.source(pid,rec['asset_id'],rec['version_id'])
        if int(src.get('row_count') or 0)>scan_budget:
            raise ValueError('数据规模超过预览扫描预算；请增大预算或另行创建明确的全量任务')
        return self._create_job(pid,'preview',sid,request_id,{
            'set_id':sid,'revision':rec['revision'],'source_sha256':rec['source_sha256'],
            'spec_sha256':rec['spec_sha256'],'max_rows':max_rows,'scan_budget':scan_budget})

    def create_candidate_preview(self,pid,sid,candidate_ids,request_id,max_rows=30,scan_budget=200000):
        """Queue a source-pinned, no-artifact read-only preview of up to 8 candidates."""
        from timepilot.feature_candidates import FeatureCandidateSessions,MAX_PREVIEW
        self._editor(pid)
        if not 1<=len(candidate_ids)<=MAX_PREVIEW or len(set(candidate_ids))!=len(candidate_ids):
            raise ValueError('一次最多预览 8 个不同候选')
        if not 1<=max_rows<=100 or not 100<=scan_budget<=500000:
            raise ValueError('候选预览的条数或扫描预算无效')
        sessions=FeatureCandidateSessions(self.projects,self.native,self.semantic,self.registry)
        doc=sessions.get(pid,sid)
        if doc['stale']:raise ValueError('候选会话来源或字段契约已变化')
        sessions.definitions(doc,candidate_ids)
        source=self.semantic.source(pid,doc['asset_id'],doc['version_id'])
        if int(source.get('row_count') or 0)>scan_budget:
            raise ValueError('数据规模超过预览扫描预算')
        return self._create_job(pid,'candidate_preview',sid,request_id,{
            'asset_id':doc['asset_id'],'version_id':doc['version_id'],
            'session_id':sid,'session_revision':doc['revision'],
            'source_sha256':doc['source_sha256'],'contract_revision':doc['contract_revision'],
            'candidate_ids':candidate_ids,'max_rows':max_rows,'scan_budget':scan_budget})

    def _create_job(self,pid,kind,target,request_id,payload):
        if not request_id or len(request_id)>120:raise ValueError('请求 ID 无效')
        if self.projects.security is not None:
            principal=CURRENT_PRINCIPAL.get()
            if principal is None:raise SecurityError('未登录，拒绝创建后台作业')
            payload=dict(payload, __owner_tenant=principal.tenant_id)
        with self.projects._lock,self.projects._db() as db:
            old=db.execute('''SELECT job_id,payload_json FROM feature_jobs WHERE project_id=? AND kind=? AND owner_id=? AND request_id=?''',
                (pid,kind,self._actor(),request_id)).fetchone()
            if old:
                if json.loads(old['payload_json'])!=payload:
                    raise ValueError('同一请求 ID 已用于不同的任务参数，禁止复用')
                return self.get_job(pid,old['job_id'])
            jid=str(uuid4());at=now()
            db.execute('''INSERT INTO feature_jobs
              (job_id,project_id,kind,target_id,request_id,owner_id,status,payload_json,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?)''',
              (jid,pid,kind,target,request_id,self._actor(),'queued',canonical(payload),at,at))
            if kind=='materialize':
                db.execute('''INSERT INTO feature_runs
                  (run_id,set_id,project_id,revision,job_id,source_sha256,spec_sha256,status,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (jid,target,pid,payload['revision'],jid,payload['source_sha256'],payload['spec_sha256'],
                   'queued',at,at))
        return self.get_job(pid,jid)

    def get_job(self,pid,jid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_jobs WHERE project_id=? AND job_id=?',(pid,jid)).fetchone()
        if row is None:raise KeyError('特征计算任务不存在或没有访问权限')
        if self.projects.security is not None and row['owner_id']!=self._actor():
            raise SecurityError('只允许查看本人创建的特征计算任务')
        data=dict(row);data['payload']=json.loads(data.pop('payload_json'))
        data['payload'].pop('__owner_tenant',None)
        data['result']=json.loads(data.pop('result_json')) if data['result_json'] else None
        data.pop('lease_owner',None)
        return data

    def list_jobs(self,pid,asset_id=None,limit=20):
        self.projects.get(pid)
        query=('SELECT job_id,payload_json FROM feature_jobs WHERE project_id=? AND owner_id=? '
               'ORDER BY created_at DESC LIMIT ?')
        with self.projects._db() as db:
            items=db.execute(query,(pid,self._actor(),max(1,min(int(limit),100)))).fetchall()
        results=[]
        for row in items:
            payload=json.loads(row['payload_json'])
            if asset_id and payload.get('asset_id') != asset_id:
                if 'set_id' not in payload:continue
                with self.projects._db() as db:
                    ref=db.execute('SELECT asset_id FROM feature_sets WHERE project_id=? AND set_id=?',
                                   (pid,payload['set_id'])).fetchone()
                if not ref or ref['asset_id'] != asset_id:continue
            results.append(self.get_job(pid,row['job_id']))
        return results

    def recent_profiles(self,pid,aid,vid='base',limit=20):
        self.projects.get(pid)
        with self.projects._db() as db:
            rows=db.execute('SELECT profile_id,source_sha256,contract_revision,scope,created_at '
                            'FROM feature_profiles WHERE project_id=? AND asset_id=? AND version_id=? '
                            'ORDER BY created_at DESC LIMIT ?',
                            (pid,aid,vid,max(1,min(int(limit),100)))).fetchall()
        return [dict(r) for r in rows]

    def cancel(self,pid,jid):
        self._editor(pid)
        data=self.get_job(pid,jid)
        if data['owner_id']!=self._actor():raise SecurityError('只能取消本人创建的特征任务')
        with self.projects._lock,self.projects._db() as db:
            db.execute('''UPDATE feature_jobs SET status=CASE WHEN status='queued' THEN 'cancelled'
                   WHEN status='running' THEN 'cancel_requested' ELSE status END,updated_at=?
                   WHERE job_id=? AND project_id=?''',(now(),jid,pid))
            if data['kind']=='materialize':
                db.execute('''UPDATE feature_runs SET status=(SELECT status FROM feature_jobs WHERE job_id=?),
                   updated_at=? WHERE run_id=?''',(jid,now(),jid))
        return self.get_job(pid,jid)

    def heartbeat(self,jid):
        with self.projects._lock,self.projects._db() as db:
            row=db.execute('''UPDATE feature_jobs SET lease_until=?,updated_at=? WHERE job_id=?
                AND lease_owner=? AND status IN ('running','cancel_requested')''',
                (deadline(),now(),jid,self.worker_id))
            return bool(row.rowcount)

    def reap_expired(self):
        'Bounded retries: never leave expired jobs permanently in running state.'
        expired=0
        with self.projects._lock,self.projects._db() as db:
            # Cancellation requested from a crashed worker must converge to
            # cancelled after lease expiry; never stay pending indefinitely.
            cancelled=db.execute('''SELECT job_id,kind FROM feature_jobs WHERE
                status='cancel_requested' AND lease_until < ?''',(now(),)).fetchall()
            for job in cancelled:
                db.execute('''UPDATE feature_jobs SET status='cancelled',lease_owner=NULL,
                    lease_until=NULL,updated_at=? WHERE job_id=?''',(now(),job['job_id']))
                if job['kind']=='materialize':
                    db.execute("UPDATE feature_runs SET status='cancelled',updated_at=? WHERE run_id=?",
                               (now(),job['job_id']))
                expired+=1
            rows=db.execute('''SELECT job_id,kind FROM feature_jobs WHERE
                status='running' AND lease_until < ? AND attempts>=3''',(now(),)).fetchall()
            for job in rows:
                db.execute('''UPDATE feature_jobs SET status='failed_terminal',lease_owner=NULL,
                    lease_until=NULL,error_code='lease_expired',error_text='重试次数耗尽，任务已终止',
                    updated_at=? WHERE job_id=? AND status='running' ''',(now(),job['job_id']))
                if job['kind']=='materialize':
                    db.execute('''UPDATE feature_runs SET status='failed_terminal',updated_at=?
                        WHERE run_id=?''',(now(),job['job_id']))
                expired+=1
        return expired

    def _claim(self):
        with self.projects._lock,self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_jobs WHERE
             status='queued' OR (status='running' AND lease_until<? AND attempts<3)
             ORDER BY created_at LIMIT 1''',(now(),)).fetchone()
            if row is None:return None
            updated=db.execute('''UPDATE feature_jobs SET status='running',lease_owner=?,lease_until=?,
                      attempts=attempts+1,progress=0.05,updated_at=? WHERE job_id=?
                      AND (status='queued' OR (status='running' AND lease_until<?))''',
                      (self.worker_id,deadline(),now(),row['job_id'],now()))
            if not updated.rowcount:return None
            if row['kind']=='materialize':
                db.execute("UPDATE feature_runs SET status='running',updated_at=? WHERE run_id=?",
                           (now(),row['job_id']))
            return dict(row)

    def _finish(self,row,status,result=None,error_code=None,error_text=None):
        jid=row['job_id'];pid=row['project_id']
        with self.projects._lock,self.projects._db() as db:
            cur=db.execute('SELECT status,lease_owner FROM feature_jobs WHERE job_id=? AND project_id=?',
                           (jid,pid)).fetchone()
            if cur is None or cur['lease_owner']!=self.worker_id:return
            if cur['status']=='cancel_requested':status='cancelled';result=None
            db.execute('''UPDATE feature_jobs SET status=?,progress=?,lease_owner=NULL,lease_until=NULL,
                  result_json=?,error_code=?,error_text=?,updated_at=? WHERE job_id=?''',
                  (status,1.0 if status=='succeeded' else 0.0,canonical(result) if result else None,
                   error_code,error_text,now(),jid))
            if row['kind']=='materialize':
                db.execute('''UPDATE feature_runs SET status=?,artifact_id=?,updated_at=? WHERE run_id=?''',
                    (status,result.get('artifact_id') if result and status=='succeeded' else None,now(),jid))

    def work_once(self):
        self.reap_expired()
        row=self._claim()
        if row is None:return False
        token=None
        pulse_stop=threading.Event()
        def pulse():
            while not pulse_stop.wait(10):
                if not self.heartbeat(row['job_id']):return
        pulse_thread=threading.Thread(target=pulse,daemon=True,name='featurelab-lease-heartbeat')
        pulse_thread.start()
        try:
            payload=json.loads(row['payload_json'])
            if self.projects.security is not None:
                from timepilot.enterprise_security import Principal
                tenant=payload.get('__owner_tenant')
                with self.projects.security._db() as authdb:
                    person=authdb.execute('SELECT user_id,tenant_id,username,tenant_role,active FROM users WHERE user_id=?',
                                          (row['owner_id'],)).fetchone()
                if not person or not person['active'] or person['tenant_id']!=tenant:
                    raise SecurityError('任务创建人已失去授权身份')
                principal=Principal(person['user_id'],person['tenant_id'],person['username'],
                                    person['tenant_role'],'internal-feature-worker','')
                self.projects.security.allowed_project(principal,row['project_id'],required='editor')
                token=CURRENT_PRINCIPAL.set(principal)
            if row['kind']=='profile':result=self._profile(row,payload)
            elif row['kind']=='materialize':result=self._materialize(row,payload)
            elif row['kind']=='preview':result=self._preview(row,payload)
            elif row['kind']=='candidate_preview':
                from timepilot.feature_candidates import FeatureCandidateSessions
                sessions=FeatureCandidateSessions(self.projects,self.native,self.semantic,self.registry)
                current=sessions.get(row['project_id'],payload['session_id'])
                if current['stale']:
                    raise ValueError('候选会话来源已失效，旧预览请求失效')
                result=sessions.preview(row['project_id'],payload['session_id'],payload['candidate_ids'],
                    max_rows=payload['max_rows'],scan_budget=payload['scan_budget'])
            elif row['kind'] in ('advanced_tsfresh','advanced_featuretools'):
                from timepilot.feature_advanced_service import AdvancedService
                result=AdvancedService(self.projects,self.native,self.semantic,self.registry,self).run_worker(row,payload)
            elif row['kind']=='advanced_join':
                from timepilot.feature_advanced_join import AdvancedJoinService
                result=AdvancedJoinService(self.projects,self.native,self.semantic,self.registry,self).run_worker(row,payload)
            elif row['kind']=='advanced_join_evaluation':
                from timepilot.feature_advanced_join import AdvancedJoinService
                result=AdvancedJoinService(self.projects,self.native,self.semantic,self.registry,self).evaluate_worker(row,payload)
            elif row['kind']=='evaluation':
                from timepilot.feature_evaluation import FeatureEvaluator
                result=FeatureEvaluator(self.projects,self.native,self.semantic,self.registry,self).run_plan(
                    row['project_id'],payload['plan_id'],evaluation_id=row['job_id'])
            else:raise ValueError('未知的后台计算任务类型')
            self._finish(row,'succeeded',result)
        except Exception as exc:
            # Retry is deliberate; no failed job is silently reported complete.
            from timepilot.native_bi import NativeBIDependencyError
            code='dependency_unavailable' if isinstance(exc,(ModuleNotFoundError,ImportError,NativeBIDependencyError)) else type(exc).__name__
            self._finish(row,'failed_retryable' if code=='dependency_unavailable' else 'failed_terminal',
                         error_code=code,error_text=str(exc)[:1500])
        finally:
            pulse_stop.set()
            pulse_thread.join(timeout=2)
            if token is not None:CURRENT_PRINCIPAL.reset(token)
        return True

    def retry(self,pid,jid):
        self._editor(pid);row=self.get_job(pid,jid)
        if row['owner_id']!=self._actor():raise SecurityError('只能重试本人任务')
        if row['status'] not in ('failed_retryable','failed_terminal') or row['attempts']>=3:
            raise ValueError('当前任务不可重试')
        with self.projects._lock,self.projects._db() as db:
            db.execute('''UPDATE feature_jobs SET status='queued',error_code=NULL,error_text=NULL,
                lease_owner=NULL,lease_until=NULL,progress=0,updated_at=? WHERE job_id=?''',(now(),jid))
            if row['kind']=='materialize':
                db.execute("UPDATE feature_runs SET status='queued' WHERE run_id=?",(jid,))
        return self.get_job(pid,jid)

    def _profile(self,row,payload):
        pid=row['project_id'];aid=payload['asset_id'];vid=payload['version_id']
        source=self.semantic.source(pid,aid,vid)
        if source['sha256']!=payload['source_sha256']:raise ValueError('画像数据源失效')
        contract=self.semantic.latest(pid,aid,vid,source=source)
        if contract.revision!=payload['contract_revision']:raise ValueError('画像契约版本已变化')
        result=self.profiler.compute(pid,aid,vid,contract)
        profile_id=str(uuid4())
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_profiles(profile_id,project_id,asset_id,version_id,
              source_sha256,contract_revision,scope,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
              (profile_id,pid,aid,vid,source['sha256'],contract.revision,'full',canonical(result),now()))
            for field in result['fields']:
                db.execute('''INSERT INTO feature_profile_evidence VALUES(?,?,?,?,?)''',
                    (str(uuid4()),profile_id,field['field_id'],'full',canonical(field)))
        return {'profile_id':profile_id,'scope':'full','rows_scanned':result['rows_scanned'],
                'source_sha256':source['sha256']}

    def get_profile(self,pid,profile_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_profiles WHERE project_id=? AND profile_id=?',
                           (pid,profile_id)).fetchone()
        if row is None:raise KeyError('画像不存在或没有权限')
        return json.loads(row['result_json'])

    def artifact_path(self,pid,artifact_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('''SELECT relpath FROM feature_artifacts WHERE project_id=? AND artifact_id=?''',
                           (pid,artifact_id)).fetchone()
        if row is None:raise KeyError('特征结果不存在或没有权限')
        path=(self.root/row['relpath']).resolve()
        if not path.is_relative_to(self.root.resolve()):raise ValueError('非法特征存储位置')
        return path

    def _preview(self,row,payload):
        'Read-only bounded view; never writes a feature artifact or Parquet file.'
        pid=row['project_id'];rec=self.registry.get(pid,payload['set_id'])
        if rec['revision']!=payload['revision'] or rec['spec_sha256']!=payload['spec_sha256']:
            raise ValueError('预览任务的配方版本已失效')
        spec=self.registry.pinned(pid,rec)
        src=self.semantic.source(pid,spec.source.asset_id,spec.source.version_id)
        if src['sha256']!=payload['source_sha256']:
            raise ValueError('预览数据源指纹变化')
        if int(src.get('row_count') or 0)>int(payload['scan_budget']):
            raise ValueError('预览范围超过当前扫描预算')
        contract=self.semantic.latest(pid,spec.source.asset_id,spec.source.version_id,source=src)
        if contract.revision!=spec.contract_revision:
            raise ValueError('预览引用的字段契约已失效')
        compiler=FeatureCompiler(contract,spec)
        from timepilot.native_bi import safe_json
        with self.native._connection() as con:
            self.native._install_effective_view(con,spec.source.asset_id,spec.source.version_id,src['columns'])
            pit=compiler.validate_point_in_time(con)
            table=compiler.compile_views(con)
            cur=con.execute(f'SELECT * FROM {table} ORDER BY "__tp_rowid" LIMIT ?',
                            [payload['max_rows']])
            columns=[c[0] for c in cur.description]
            rows=[dict(zip(columns,safe_json(r))) for r in cur.fetchall()]
        result={'scope':'sample','rows_returned':len(rows),
            'source_row_count':int(src.get('row_count') or 0),'rows':rows,
            'columns':columns,'source_sha256':src['sha256'],
            'spec_sha256':rec['spec_sha256'],'pit':pit,'materialized':False,
            'note':'仅返回前N条计算结果；计算可读取不超过扫描预算的原始数据，未生成正式物化资产'}
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_preview_receipts
                (preview_id,project_id,job_id,set_id,scope,rows_returned,source_sha256,result_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),pid,row['job_id'],rec['set_id'],'sample',len(rows),src['sha256'],digest(result),now()))
        return result

    def _materialize(self,row,payload):
        pid=row['project_id'];sid=payload['set_id'];rid=row['job_id']
        record=self.registry.get(pid,sid)
        if record['revision']!=payload['revision'] or record['spec_sha256']!=payload['spec_sha256']:
            raise ValueError('配方已修改，旧计算任务失效')
        spec=self.registry.pinned(pid,record)
        src=self.semantic.source(pid,spec.source.asset_id,spec.source.version_id)
        if self.native.verify(spec.source.asset_id).get('valid') is not True:
            raise ValueError('原始数据完整性校验失败')
        if src['sha256']!=payload['source_sha256']:raise ValueError('数据源版本指纹不一致')
        contract=self.semantic.latest(pid,spec.source.asset_id,spec.source.version_id,source=src)
        if contract.revision!=spec.contract_revision:raise ValueError('计算前契约已经变化')
        compiler=FeatureCompiler(contract,spec)
        artifact_id=str(uuid4())
        directory=self.root/pid/'artifacts'/artifact_id
        directory.mkdir(parents=True,exist_ok=True)
        tmp=directory/'data.building.parquet';out=directory/'data.parquet'
        tmp.unlink(missing_ok=True)
        try:
            with self.native._connection() as con:
                self.native._install_effective_view(con,spec.source.asset_id,spec.source.version_id,src['columns'])
                pit=compiler.validate_point_in_time(con)
                rel=compiler.compile_views(con)
                count=con.execute(f'SELECT COUNT(*) FROM {rel}').fetchone()[0]
                columns=con.execute(f'SELECT * FROM {rel} LIMIT 0').description
                names=[c[0] for c in columns if c[0]!='__tp_rowid']
                location=str(tmp).replace("'","''")
                con.execute(f'''COPY (SELECT * EXCLUDE ("__tp_rowid") FROM {rel}
                     ORDER BY "__tp_rowid") TO '{location}' (FORMAT PARQUET)''')
            with self.projects._db() as db:
                job=db.execute('SELECT status FROM feature_jobs WHERE job_id=?',(rid,)).fetchone()
                if not job or job['status']=='cancel_requested':raise ValueError('任务已取消，放弃结果物化')
            file_sha=sha256_file(tmp)
            os.replace(tmp,out)
            metadata={'artifact_id':artifact_id,'row_count':int(count),'columns':names,
                      'source_sha256':src['sha256'],'spec_sha256':record['spec_sha256'],
                      'pit':pit,'sha256':file_sha,'scope':'full'}
            with self.projects._lock,self.projects._db() as db:
                lease=db.execute('''SELECT status,lease_owner,lease_until FROM feature_jobs
                    WHERE project_id=? AND job_id=?''',(pid,rid)).fetchone()
                current_set=db.execute('''SELECT revision,spec_sha256,source_sha256 FROM feature_sets
                    WHERE project_id=? AND set_id=?''',(pid,sid)).fetchone()
                if (not lease or lease['status']!='running' or lease['lease_owner']!=self.worker_id
                        or not lease['lease_until'] or lease['lease_until']<now()):
                    raise ValueError('计算任务租约已过期或被取消，禁止提交物化产物')
                if (not current_set or current_set['revision']!=payload['revision']
                        or current_set['spec_sha256']!=payload['spec_sha256']
                        or current_set['source_sha256']!=payload['source_sha256']):
                    raise ValueError('计算期间特征集版本发生变化，拒绝提交旧结果')
                db.execute('''INSERT INTO feature_artifacts
                 (artifact_id,run_id,project_id,sha256,relpath,row_count,schema_json,source_sha256,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?)''',
                 (artifact_id,rid,pid,file_sha,str(out.relative_to(self.root)),int(count),
                  canonical(metadata),src['sha256'],now()))
                if pit.get('status')=='verified':
                    db.execute('''INSERT INTO feature_pit_audits (audit_id,project_id,artifact_id,task_sha256,pit_json,created_at)
                        VALUES (?,?,?,?,?,?)''',(str(uuid4()),pid,artifact_id,pit['task_sha256'],canonical(pit),now()))
                db.execute('''INSERT OR REPLACE INTO feature_run_checkpoints
                    (run_id,stage,source_sha256,checkpoint_json,updated_at) VALUES (?,?,?,?,?)''',
                    (rid,'materialized',src['sha256'],canonical(metadata),now()))
            return metadata
        finally:
            tmp.unlink(missing_ok=True)
