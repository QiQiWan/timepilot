"""Evidence-based, source-preserving import summaries.

Every count is computed over all non-empty records in the selected region;
expensive distinct cardinality is deliberately capped and explicitly labeled.
The source is SHA-verified before and after scanning. No model guesses units.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import math
from uuid import uuid4
from timepilot.generic_assets import GenericAssetStore
from timepilot.import_semantics import relationship_candidates
from timepilot.smart_import import SmartImportError, _hash_file

VERSION='region-summary-v2'

class SummaryIntegrityError(SmartImportError):
    """A source file no longer matches the immutable import manifest."""
    pass


def now():return datetime.now(timezone.utc).isoformat()

def fingerprint(source, candidate):
    identity={k:candidate.get(k) for k in ('candidate_id','bbox','header_row','data_start_row','row_end','start_col','end_col','columns','source_fields')}
    identity['file_sha256']=source['sha256']
    identity['algorithm']=VERSION
    return sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def _empty(v):return v is None or (isinstance(v,str) and not v.strip())


def _number(v):
    if v is None or isinstance(v,bool):return None
    if isinstance(v,(float,int)):
        n=float(v)
        return n if math.isfinite(n) else None
    if isinstance(v,str):
        try:
            # Treat date-like/identifier strings as strings, not numeric values.
            if len(v)>32 or not v.strip() or v.strip().startswith(('0x','0X')):return None
            n=float(v.strip())
            return n if math.isfinite(n) else None
        except ValueError:return None
    return None


class ImportSummaryService:
    def __init__(self,catalog):
        self.catalog=catalog
        self.generic=GenericAssetStore(catalog.imports)
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='tp-import-summary')
        self.worker_id=str(uuid4())
        self.recover_jobs()

    def _locate(self,pid,iid,rid):
        manifest=self.catalog.require_import(pid,iid)
        candidate=self.catalog.imports._candidate(manifest,rid)
        if candidate.get('superseded'):raise SmartImportError('区域已被修订，请重新读取区域清单')
        source=next((f for f in manifest['files'] if f['file_id']==candidate['file_id']),None)
        if not source:raise SmartImportError('来源文件不存在')
        path=self.catalog.imports._dir(iid)/source['stored']
        if not path.is_file() or _hash_file(path)!=source['sha256']:
            raise SummaryIntegrityError('源文件哈希不匹配，摘要缓存已禁止使用')
        return manifest,candidate,source,path

    def summarize_region(self,project_id,import_id,region_id,*,refresh=False):
        m,c,f,path=self._locate(project_id,import_id,region_id)
        key=fingerprint(f,c)
        with self.catalog._db() as db:
            old=db.execute('''SELECT summary_json,fingerprint FROM import_region_summaries
                 WHERE project_id=? AND import_id=? AND region_id=?''',
                 (project_id,import_id,region_id)).fetchone()
        if old and old['fingerprint']==key and not refresh:
            return json.loads(old['summary_json'])
        names=list(c['columns']);ncols=len(names)
        if ncols<1:raise SmartImportError('没有源字段，无法统计')
        sampled=self.generic.inspect(import_id,region_id,max_sample=120)
        types=[x.get('physical_type','VARCHAR') for x in sampled['columns']]
        missing=[0]*ncols;nonempty=[0]*ncols;errors=[0]*ncols
        low=[None]*ncols;high=[None]*ncols
        numeric=[0]*ncols;unique=[set() for _ in names];overflow=[False]*ncols
        total=0;first_row=None;last_row=None
        for lineno,row in self.catalog.imports._rows(self.catalog.imports._dir(import_id),m,c):
            if all(_empty(v) for v in row):continue
            total+=1
            if first_row is None:first_row=lineno
            last_row=lineno
            for i in range(ncols):
                v=row[i] if i<len(row) else None
                if _empty(v):missing[i]+=1;continue
                nonempty[i]+=1
                if not overflow[i]:
                    if len(unique[i])<2048:
                        unique[i].add(str(v)[:256])
                    else:overflow[i]=True;unique[i].clear()
                value=_number(v)
                if value is not None:
                    numeric[i]+=1
                    low[i]=value if low[i] is None else min(low[i],value)
                    high[i]=value if high[i] is None else max(high[i],value)
                elif types[i].upper() in ('DOUBLE','FLOAT','BIGINT','INTEGER','DECIMAL','SMALLINT','REAL'):
                    errors[i]+=1
        if _hash_file(path)!=f['sha256']:
            raise SummaryIntegrityError('源数据在统计期间发生变化，拒绝保存摘要')
        fields=[]
        for i,name in enumerate(names):
            src=(c.get('source_fields') or [])
            evidence=src[i] if i<len(src) else {}
            fields.append({'name':name,'ordinal':i,'source_name':evidence.get('source_name',name),
                'source_cell':evidence.get('source_cell'),'physical_type':types[i],
                'nonempty':nonempty[i],'missing':missing[i],
                'missing_fraction':round(missing[i]/total,8) if total else None,
                'numeric_parseable':numeric[i],'numeric_min':low[i],'numeric_max':high[i],
                'conversion_errors':errors[i],
                'numeric_parse_fraction':round(numeric[i]/nonempty[i],8) if nonempty[i] else None,
                'numeric_parse_scope':'full_nonempty_region', 'type_inference_scope':'sample',
                'distinct_count':None if overflow[i] else len(unique[i]),
                'distinct_scope':'upper_bound_2048' if overflow[i] else 'all_nonempty_values'})
        warnings=list(sampled.get('warnings',[]))
        if c.get('needs_review'):warnings.append('区域边界或表头待人工核实')
        result={'status':'needs_review' if c.get('needs_review') else 'ready',
            'project_id':project_id,'import_id':import_id,'region_id':region_id,
            'source':{'file_id':c['file_id'],'filename':f['filename'],'sheet':c['sheet'],
                      'source_sha256':f['sha256'],'header_row':c.get('header_row'),
                      'bbox':c.get('bbox'),'first_data_row':first_row,'last_data_row':last_row},
            'shape':{'row_count':total,'column_count':ncols,'count_scope':'full_nonempty_region'},
            'fields':fields,'quality':{'missing_cells':sum(missing),'conversion_errors':sum(errors),
                                      'warnings':warnings},
            'provenance':{'fingerprint':key,'algorithm_version':VERSION,'scan_scope':'full',
                          'source_verified':True,'sampled_type_rows':sampled['sampled_rows'],
                          'type_evidence':'sample_inference_with_full_numeric_parse_coverage'},
            'summary_text':f'已核实 {total:,} 行、{ncols} 列；源字段及顺序保留。'+
                    (f' 检测到 {sum(missing):,} 个空值。' if total else ' 当前区域无非空数据行。')}
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''INSERT INTO import_region_summaries
              (project_id,import_id,region_id,fingerprint,source_sha256,algorithm_version,summary_json,updated_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,import_id,region_id) DO UPDATE SET
              fingerprint=excluded.fingerprint,source_sha256=excluded.source_sha256,
              algorithm_version=excluded.algorithm_version,summary_json=excluded.summary_json,updated_at=excluded.updated_at''',
              (project_id,import_id,region_id,key,f['sha256'],VERSION,
               json.dumps(result,ensure_ascii=False,allow_nan=False),now()))
        return result

    def summarize_import(self,project_id,import_id,*,refresh=False):
        manifest=self.catalog.require_import(project_id,import_id)
        active=[x for x in manifest['candidates'] if not x.get('superseded')]
        summaries=[]
        for c in active:
            summaries.append(self.summarize_region(project_id,import_id,c['candidate_id'],refresh=refresh))
        return {'import_id':import_id,'file_count':len(manifest['files']),'region_count':len(active),
                'summaries':summaries,'relationships':relationship_candidates(manifest),
                'unassigned_regions':manifest.get('unassigned_regions',[])}

    def cache_status(self,project_id,import_id):
        manifest=self.catalog.require_import(project_id,import_id)
        with self.catalog._db() as db:
            stored={x['region_id']:dict(x) for x in db.execute('''SELECT region_id,fingerprint FROM import_region_summaries
                WHERE project_id=? AND import_id=?''',(project_id,import_id))}
        statuses=[]
        verified={}
        for c in (c for c in manifest['candidates'] if not c.get('superseded')):
            source=next(f for f in manifest['files'] if f['file_id']==c['file_id'])
            if source['file_id'] not in verified:
                path=self.catalog.imports._dir(import_id)/source['stored']
                verified[source['file_id']]=path.is_file() and _hash_file(path)==source['sha256']
            current=fingerprint(source,c)
            matching=stored.get(c['candidate_id'],{}).get('fingerprint')==current
            statuses.append({'region_id':c['candidate_id'],
                             'status':'mismatch' if not verified[source['file_id']] else
                                ('ready' if matching else 'pending')})
        return {'import_id':import_id,'region_count':len(statuses),'regions':statuses,
                'ready':sum(x['status']=='ready' for x in statuses)}

    @staticmethod
    def _lease_until():
        return (datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()

    def recover_jobs(self):
        '''Claim pending/reclaimed jobs at API startup; no duplicate claim by another worker.

        Running jobs with a live lease are left to their current process. Regions
        completed before a crash are skipped using the persisted cursor.
        '''
        with self.catalog._db() as db:
            rows=db.execute('''SELECT job_id,project_id,import_id FROM import_summary_jobs
                WHERE status='queued' OR (status='running' AND (lease_until IS NULL OR lease_until<?))
                ORDER BY created_at LIMIT 100''',(now(),)).fetchall()
        for row in rows:
            self._schedule(row['project_id'],row['import_id'],row['job_id'])
        return len(rows)

    def _schedule(self,pid,iid,jid):
        with self.catalog._lock,self.catalog._db() as db:
            claimed=db.execute('''UPDATE import_summary_jobs SET status='running',
                lease_owner=?,lease_until=?,attempts=attempts+1,updated_at=?
                WHERE job_id=? AND project_id=? AND import_id=? AND attempts<5
                AND (status='queued' OR (status='running' AND (lease_until IS NULL OR lease_until<?)))''',
                (self.worker_id,self._lease_until(),now(),jid,pid,iid,now())).rowcount
        if claimed:
            self.pool.submit(self._run_job,pid,iid,jid)
        return bool(claimed)

    def submit_job(self,pid,iid,*,region_ids=None,refresh=False):
        manifest=self.catalog.require_import(pid,iid)
        active={x['candidate_id'] for x in manifest['candidates'] if not x.get('superseded')}
        ids=list(dict.fromkeys(region_ids if region_ids is not None else sorted(active)))
        if not ids or len(ids)>250 or not set(ids)<=active:raise SmartImportError('区域编号为空、超限或不存在')
        job_id=str(uuid4());moment=now()
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''INSERT INTO import_summary_jobs
              (job_id,project_id,import_id,region_ids_json,status,completed,total,error,created_at,updated_at,refresh)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (job_id,pid,iid,json.dumps(ids),'queued',0,len(ids),None,moment,moment,int(refresh)))
        self._schedule(pid,iid,job_id)
        return self.job(pid,iid,job_id)

    def _heartbeat(self,jid,stop):
        while not stop.wait(15):
            with self.catalog._lock,self.catalog._db() as db:
                if db.execute("""UPDATE import_summary_jobs SET lease_until=?,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running'""",
                    (self._lease_until(),now(),jid,self.worker_id)).rowcount!=1:return

    def _run_job(self,pid,iid,jid):
        stopped=Event();heartbeat=Thread(target=self._heartbeat,args=(jid,stopped),daemon=True)
        heartbeat.start()
        try:
            with self.catalog._db() as db:
                row=db.execute('SELECT * FROM import_summary_jobs WHERE job_id=?',(jid,)).fetchone()
            if not row or row['lease_owner']!=self.worker_id:return
            ids=json.loads(row['region_ids_json'])
            for index in range(row['completed'],len(ids)):
                with self.catalog._db() as db:
                    current=db.execute('SELECT status,lease_owner FROM import_summary_jobs WHERE job_id=?',(jid,)).fetchone()
                if not current or current['status']!='running' or current['lease_owner']!=self.worker_id:return
                try:self.summarize_region(pid,iid,ids[index],refresh=bool(row['refresh']))
                except Exception as exc:
                    with self.catalog._lock,self.catalog._db() as db:
                        db.execute('''UPDATE import_summary_jobs SET status='failed_retryable',error=?,
                            lease_owner=NULL,lease_until=NULL,updated_at=?
                            WHERE job_id=? AND lease_owner=? AND status='running' ''',
                            (f'{type(exc).__name__}: {exc}'[:350],now(),jid,self.worker_id))
                    return
                with self.catalog._lock,self.catalog._db() as db:
                    db.execute('''UPDATE import_summary_jobs SET completed=?,lease_until=?,updated_at=?
                        WHERE job_id=? AND status='running' AND lease_owner=?''',
                        (index+1,self._lease_until(),now(),jid,self.worker_id))
            with self.catalog._lock,self.catalog._db() as db:
                db.execute('''UPDATE import_summary_jobs SET status='succeeded',
                    lease_owner=NULL,lease_until=NULL,error=NULL,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='running' ''',(now(),jid,self.worker_id))
        finally:
            stopped.set();heartbeat.join(timeout=0.5)

    def retry_job(self,pid,iid,jid):
        self.catalog.require_import(pid,iid)
        with self.catalog._lock,self.catalog._db() as db:
            changed=db.execute('''UPDATE import_summary_jobs SET status='queued',error=NULL,
              lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=? AND
              project_id=? AND import_id=? AND status='failed_retryable' AND attempts<5''',
              (now(),jid,pid,iid)).rowcount
        if not changed:raise SmartImportError('摘要任务当前不允许重试或已超过重试次数')
        self._schedule(pid,iid,jid)
        return self.job(pid,iid,jid)

    def cancel_job(self,pid,iid,jid):
        self.catalog.require_import(pid,iid)
        with self.catalog._lock,self.catalog._db() as db:
            changed=db.execute('''UPDATE import_summary_jobs SET status='cancelled',
              lease_owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=? AND
              project_id=? AND import_id=? AND status IN ('queued','running','failed_retryable')''',
              (now(),jid,pid,iid)).rowcount
        if not changed:raise SmartImportError('摘要任务已结束或无权取消')
        return self.job(pid,iid,jid)

    def job(self,pid,iid,jid):
        self.catalog.require_import(pid,iid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT * FROM import_summary_jobs WHERE project_id=? AND import_id=? AND job_id=?''',
                           (pid,iid,jid)).fetchone()
        if not row:raise KeyError('摘要任务不存在')
        out=dict(row);out['region_ids']=json.loads(out.pop('region_ids_json'))
        return out
