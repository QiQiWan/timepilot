"""One-click, resumable multi-region publication coordinator.

Child jobs reuse the v0.20+ persistent publisher, heartbeat and per-chunk
checkpoints. A batch is a durable *group of independently committed assets*;
partial completion is visible and retryable, never misrepresented as an
all-or-nothing SQLite + filesystem + BI transaction.
"""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4,UUID

from pydantic import BaseModel,ConfigDict,Field
from timepilot.generic_assets import PublishGeneral
from timepilot.smart_import import SmartImportError


class BatchPublishRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    region_ids:list[str]=Field(min_length=1,max_length=300)
    confirm:bool=False
    confirm_unassigned:bool=False


class ProjectPublishBatches:
    VERSION=1
    def __init__(self,catalog,publication_jobs):
        self.catalog=catalog
        self.jobs=publication_jobs
        with catalog._lock,catalog._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS import_publication_batches(
                batch_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                import_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
                request_hash TEXT NOT NULL,created_at REAL NOT NULL,
                UNIQUE(project_id,import_id,request_hash));
            CREATE TABLE IF NOT EXISTS import_publication_batch_items(
                batch_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,job_id TEXT NOT NULL,
                PRIMARY KEY(batch_id,candidate_id),
                UNIQUE(batch_id,ordinal));
            CREATE TABLE IF NOT EXISTS import_publication_migrations(
                version INTEGER PRIMARY KEY,description TEXT NOT NULL,created_at REAL NOT NULL);
            INSERT OR IGNORE INTO import_publication_migrations VALUES
                (1,'Multi-region publish batches preserving all old project/native links',0);
            ''')

    @staticmethod
    def _id(value):
        try:return str(UUID(str(value)))
        except (TypeError,ValueError):raise SmartImportError('发布批次编号无效') from None

    def submit(self,pid,iid,request:BatchPublishRequest):
        self.catalog.writable(pid)
        manifest=self.catalog.require_import(pid,iid)
        if not request.confirm:raise SmartImportError('请确认将选中的全部表格发布到正式 BI')
        if len(set(request.region_ids))!=len(request.region_ids):
            raise SmartImportError('发布批次包含重复数据区域')
        from timepilot.import_data_contract import ImportDataContracts
        # Review decision must match the current parser/source fingerprint.
        ImportDataContracts(self.catalog,None).assert_publishable(
            pid,iid,request.region_ids,confirm_unassigned=request.confirm_unassigned)
        selected=[]
        for region_id in request.region_ids:
            candidate=self.catalog.imports._candidate(manifest,region_id)
            if candidate.get('superseded'):
                raise SmartImportError('区域已被修订，请刷新解析目录后重新提交')
            selected.append(candidate)
        # Canonical ordering makes both batch identity and child job requests
        # stable even when a client retries with a different checkbox order.
        selected=sorted(selected,key=lambda c:(c['file_id'],c['sheet'],
                     c.get('row_start') or 0,c.get('start_col') or 0,c['candidate_id']))
        canonical={'source_sha256':manifest['source_sha256'],
                   'region_ids':[c['candidate_id'] for c in selected],
                   'schema_version':self.VERSION}
        signature=hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        # Persist the batch identity BEFORE starting any child job. A process
        # can exit at any point while scheduling children; the next submit
        # repairs missing links using the same deterministic request payload.
        with self.catalog._lock:
            with self.catalog._db() as db:
                batch_id=str(uuid4())
                db.execute('''INSERT OR IGNORE INTO import_publication_batches
                    (batch_id,project_id,import_id,source_sha256,request_hash,created_at)
                    VALUES(?,?,?,?,?,?)''',(batch_id,pid,iid,manifest['source_sha256'],signature,time.time()))
                row=db.execute('''SELECT batch_id FROM import_publication_batches
                    WHERE project_id=? AND import_id=? AND request_hash=?''',(pid,iid,signature)).fetchone()
                actual=row['batch_id']
            for ordinal,candidate in enumerate(selected,1):
                with self.catalog._db() as db:
                    existing=db.execute('''SELECT job_id FROM import_publication_batch_items
                        WHERE batch_id=? AND candidate_id=?''',(actual,candidate['candidate_id'])).fetchone()
                if existing:continue
                short_file=str(candidate['logical_file']).rsplit('/',1)[-1]
                name=f'{short_file} · {candidate["sheet"]} · 表 {ordinal}'[:100]
                job=self.jobs.submit(pid,iid,candidate['candidate_id'],
                                     PublishGeneral(name=name,confirm=True))
                with self.catalog._db() as db:
                    db.execute('''INSERT OR IGNORE INTO import_publication_batch_items
                                   (batch_id,ordinal,candidate_id,job_id) VALUES(?,?,?,?)''',
                               (actual,ordinal,candidate['candidate_id'],job['job_id']))
        return self.get(pid,actual)

    def get(self,pid,batch_id):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            batch=db.execute('SELECT * FROM import_publication_batches WHERE project_id=? AND batch_id=?',
                             (pid,self._id(batch_id))).fetchone()
            if batch is None:raise KeyError('该项目不存在此发布批次')
            links=db.execute('SELECT * FROM import_publication_batch_items WHERE batch_id=? ORDER BY ordinal',
                             (batch_id,)).fetchall()
        # Each child is read through the project-authorized job API.
        items=[];count={'queued':0,'running':0,'succeeded':0,'prepared':0,'failed':0,'cancelled':0}
        for link in links:
            job=self.jobs.get(pid,link['job_id'])
            count[job['status']]=count.get(job['status'],0)+1
            items.append({'candidate_id':link['candidate_id'],'ordinal':link['ordinal'],
                          'job_id':link['job_id'],'status':job['status'],'stage':job['stage'],
                          'attempts':job['attempts'],'error':job['error'],
                          'native_asset_id':(job.get('result') or {}).get('native_asset_id')})
        total=len(items)
        if total and count['succeeded']==total:status='succeeded'
        elif count['failed']:status='partial_failed'
        elif count['prepared'] and count['prepared']+count['succeeded']==total:status='prepared'
        elif count['running']:status='running'
        else:status='queued'
        return {'batch_id':batch_id,'project_id':pid,'import_id':batch['import_id'],
                'source_sha256':batch['source_sha256'],'status':status,'count':total,
                'succeeded':count['succeeded'],'failed':count['failed'],
                'prepared':count['prepared'],'pending':count['queued']+count['running'],
                'created_at':batch['created_at'],'items':items,
                'message':'每个子表独立提交，失败可恢复已完成内容；不执行跨表自动合并'}

    def list(self,pid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows=db.execute('SELECT batch_id FROM import_publication_batches WHERE project_id=? ORDER BY created_at DESC LIMIT 30',
                            (pid,)).fetchall()
        return [self.get(pid,r['batch_id']) for r in rows]

    def retry(self,pid,batch_id):
        self.catalog.writable(pid)
        batch=self.get(pid,batch_id)
        errors=[]
        for item in batch['items']:
            if item['status'] in ('failed','prepared'):
                try:self.jobs.retry(pid,item['job_id'])
                except Exception as exc:errors.append({'candidate_id':item['candidate_id'],'reason':str(exc)})
        result=self.get(pid,batch_id)
        result['retry_errors']=errors
        return result
