"""Durable project upload/structure-scan adapter.

The HTTP request only stages and hashes original files. A worker subsequently
uses the existing SmartImportService; no separate parser, virtual environment,
or hidden large-file request is introduced. A deterministic import ID makes
reconciliation safe if a process exits between writing the manifest and marking
the job succeeded. All writes stay within TIMEPILOT_HOME.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

from timepilot.smart_import import MAX_UPLOAD_BYTES, MAX_MEMBER_BYTES, SUPPORTED, SmartImportError
from timepilot.import_checkpoints import WorksheetCheckpoints


class ProjectLeaseExpired(RuntimeError):
    """Abort scanning immediately after ownership is lost.

    This intentionally must not inherit SmartImportError, which the source
    scanner may treat as a recoverable malformed-file warning.
    """


class ProjectImportJobs:
    def __init__(self, catalog, *, lease_seconds: int = 600):
        self.catalog = catalog
        self.root = catalog.root / 'import_uploads'
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker_id = str(uuid4())
        self.lease_seconds = max(30, int(lease_seconds))
        with self.catalog._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS project_import_jobs (
                job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                status TEXT NOT NULL, files_json TEXT NOT NULL, created_at REAL NOT NULL,
                started_at REAL, finished_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT, lease_until REAL, import_id TEXT, error TEXT,
                CHECK(status IN ('queued','running','succeeded','failed','cancelled'))
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_project_import_jobs_project ON project_import_jobs(project_id,created_at)')
            # Additive migration; previous v0.14 jobs keep their IDs, retries and
            # state. A new database gets the same schema through ALTER TABLE.
            columns = {r['name'] for r in db.execute('PRAGMA table_info(project_import_jobs)')}
            additions = {
                'heartbeat_at': 'REAL',
                'phase': "TEXT NOT NULL DEFAULT 'queued'",
                'current_file': 'TEXT',
                'current_sheet': 'TEXT',
                'completed_sheets': 'INTEGER NOT NULL DEFAULT 0',
                'total_sheets': 'INTEGER NOT NULL DEFAULT 0',
                'reused_sheets': 'INTEGER NOT NULL DEFAULT 0',
            }
            for column, definition in additions.items():
                if column not in columns:
                    db.execute(f'ALTER TABLE project_import_jobs ADD COLUMN {column} {definition}')
        self.cleanup_finished()

    def cleanup_finished(self):
        """Discard redundant upload staging only after immutable ingestion succeeds.

        Failed tasks retain uploaded bytes for an explicit retry; a cancelled
        queued task is safe to purge. The original ingested file is retained.
        """
        with self.catalog._db() as db:
            ids=[r['job_id'] for r in db.execute(
                "SELECT job_id FROM project_import_jobs WHERE status IN ('succeeded','cancelled')")]
        for jid in ids:
            if str(jid) == self._id(jid):
                shutil.rmtree(self.root / jid, ignore_errors=True)

    @staticmethod
    def _id(value):
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError):
            raise SmartImportError('上传任务编号格式不正确') from None

    @staticmethod
    def _public(row):
        item = dict(row)
        item['files'] = [{k: entry[k] for k in ('name','bytes','sha256')} for entry in json.loads(item.pop('files_json'))]
        item['progress_percent'] = (round(min(100,100 * item['completed_sheets'] / item['total_sheets']),1)
                                    if item.get('total_sheets') else None)
        return item

    def submit(self, project_id, uploads):
        self.catalog.writable(project_id)
        if not uploads or len(uploads) > 100:
            raise SmartImportError('请提供 1—100 个上传文件')
        job_id = str(uuid4())
        folder = self.root / job_id
        folder.mkdir()
        members = []
        total = 0
        try:
            for index, (handle, filename) in enumerate(uploads):
                name = str(filename or '').replace('\\','/').rsplit('/',1)[-1]
                extension = Path(name).suffix.lower()
                if not name or extension not in SUPPORTED | {'.zip'}:
                    raise SmartImportError('文件格式不支持；请选择 ZIP、XLSX、CSV 或 Parquet')
                if len(uploads) > 1 and extension == '.zip':
                    raise SmartImportError('请单独上传 ZIP，或多选普通表格文件')
                stored = f'{index:04d}{extension}'
                digest = hashlib.sha256()
                written = 0
                with (folder / stored).open('xb') as out:
                    while True:
                        chunk = handle.read(1024*1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        total += len(chunk)
                        if written > MAX_MEMBER_BYTES and extension != '.zip':
                            raise SmartImportError('单文件超过安全大小上限')
                        if total > MAX_UPLOAD_BYTES:
                            raise SmartImportError('上传数据超过总大小上限')
                        digest.update(chunk)
                        out.write(chunk)
                if written == 0:
                    raise SmartImportError('上传文件为空')
                members.append({'name':name,'stored':stored,'bytes':written,'sha256':digest.hexdigest()})
            now = time.time()
            with self.catalog._db() as db:
                db.execute('INSERT INTO project_import_jobs(job_id,project_id,status,files_json,created_at) VALUES (?,?,?,?,?)',
                           (job_id, self.catalog._id(project_id), 'queued', json.dumps(members, ensure_ascii=False), now))
            return self.get(project_id, job_id)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def get(self, project_id, job_id):
        self.catalog.get(project_id)
        with self.catalog._db() as db:
            row = db.execute('SELECT * FROM project_import_jobs WHERE project_id=? AND job_id=?',
                             (self.catalog._id(project_id), self._id(job_id))).fetchone()
        if row is None:
            raise KeyError('该项目没有此上传任务')
        return self._public(row)

    def list(self, project_id):
        self.catalog.get(project_id)
        with self.catalog._db() as db:
            rows = db.execute('SELECT * FROM project_import_jobs WHERE project_id=? ORDER BY created_at DESC',
                              (self.catalog._id(project_id),)).fetchall()
        return [self._public(row) for row in rows]

    def checkpoints(self, project_id, job_id):
        job = self.get(project_id, job_id)
        records = WorksheetCheckpoints(self.root / job['job_id'] / 'checkpoints').summary()
        return {'job_id': job['job_id'], 'phase': job['phase'],
                'heartbeat_at': job['heartbeat_at'], 'completed_sheets':job['completed_sheets'],
                'total_sheets':job['total_sheets'], 'reused_sheets':job['reused_sheets'],
                'sheets':records}

    def cancel(self, project_id, job_id):
        self.catalog.writable(project_id)
        job = self.get(project_id, job_id)
        if job['status'] not in ('queued',):
            raise SmartImportError('只允许取消尚未开始的结构扫描；已开始的扫描将安全完成')
        with self.catalog._db() as db:
            result = db.execute('UPDATE project_import_jobs SET status=?,finished_at=? WHERE job_id=? AND status=?',
                                ('cancelled', time.time(), job_id, 'queued'))
            if result.rowcount != 1:
                raise SmartImportError('任务状态已更新，请刷新')
        self.cleanup_finished()
        return self.get(project_id, job_id)

    def retry(self, project_id, job_id):
        self.catalog.writable(project_id)
        job = self.get(project_id, job_id)
        if job['status'] != 'failed' or job['attempts'] >= 3:
            raise SmartImportError('任务未失败或已达到重试上限')
        with self.catalog._db() as db:
            db.execute('UPDATE project_import_jobs SET status=?,error=NULL,finished_at=NULL WHERE job_id=? AND status=?',
                       ('queued', job_id, 'failed'))
        return self.get(project_id, job_id)

    def _claim(self):
        conn = self.catalog._db()
        try:
            conn.execute('BEGIN IMMEDIATE')
            now = time.time()
            # A final crashed attempt must not remain 'running' indefinitely.
            conn.execute('''UPDATE project_import_jobs SET status='failed',phase='failed',
                error='连续扫描中断次数已达上限',finished_at=?,lease_owner=NULL,lease_until=NULL
                WHERE status='running' AND lease_until<? AND attempts>=3''',(now,now))
            row = conn.execute('''SELECT * FROM project_import_jobs
                WHERE (status='queued' OR (status='running' AND COALESCE(lease_until,0)<?)) AND attempts<3
                ORDER BY created_at LIMIT 1''', (now,)).fetchone()
            if row is None:
                # The transaction can already have transitioned an exhausted
                # expired lease to 'failed'; that recovery must be durable.
                conn.commit()
                return None
            owner=str(uuid4())
            conn.execute('''UPDATE project_import_jobs SET status='running',started_at=?,
                attempts=attempts+1,lease_owner=?,lease_until=?,heartbeat_at=?,
                phase='verifying',current_file=NULL,current_sheet=NULL,
                completed_sheets=0,total_sheets=0,reused_sheets=0 WHERE job_id=?''',
                (now,owner,now+self.lease_seconds,now,row['job_id']))
            conn.commit()
            return {**dict(row),'lease_owner':owner}
        finally:
            conn.close()

    def _finish(self, jid, owner, *, success, error=None):
        with self.catalog._db() as db:
            db.execute('''UPDATE project_import_jobs SET status=?,finished_at=?,
                error=?,lease_owner=NULL,lease_until=NULL,import_id=?,phase=?
                WHERE job_id=? AND lease_owner=? AND status='running' ''',
                ('succeeded' if success else 'failed',time.time(),str(error)[:600] if error else None,
                 jid if success else None,'complete' if success else 'failed',jid,owner))

    def _heartbeat(self, jid, owner):
        timestamp=time.time()
        with self.catalog._db() as db:
            updated=db.execute('''UPDATE project_import_jobs SET heartbeat_at=?,lease_until=?
                WHERE job_id=? AND lease_owner=? AND status='running' ''',
                (timestamp,timestamp+self.lease_seconds,jid,owner))
        return updated.rowcount == 1

    def _assert_owned(self, jid, owner):
        with self.catalog._db() as db:
            current=db.execute('''SELECT 1 FROM project_import_jobs WHERE job_id=? AND
                lease_owner=? AND status='running' AND lease_until>?''',
                (jid,owner,time.time())).fetchone()
        if current is None:
            raise ProjectLeaseExpired('当前后台工作者的租约已失效，停止提交扫描结果')

    def _progress(self, jid, owner, event, **info):
        self._assert_owned(jid, owner)
        phase = {'unpacking':'unpacking','file_started':'scanning',
                 'sheet_started':'scanning','sheet_completed':'scanning',
                 'assembling':'assembling'}.get(event)
        if phase is None:
            return
        now=time.time()
        total = int(info.get('total_sheets',0)) if event=='file_started' else 0
        done = 1 if event=='sheet_completed' else 0
        reused = 1 if done and info.get('reused') else 0
        with self.catalog._db() as db:
            db.execute('''UPDATE project_import_jobs SET phase=?,current_file=COALESCE(?,current_file),
                current_sheet=?,total_sheets=total_sheets+?,completed_sheets=completed_sheets+?,
                reused_sheets=reused_sheets+?,heartbeat_at=?,lease_until=?
                WHERE job_id=? AND lease_owner=? AND status='running' ''',
                (phase,info.get('filename') or info.get('file_id'),info.get('sheet'),
                 total,done,reused,now,now+self.lease_seconds,jid,owner))

    def work_once(self):
        job = self._claim()
        if not job:
            return None
        jid = job['job_id']
        owner = job['lease_owner']
        shutdown=threading.Event()
        lease_lost=threading.Event()
        def renew():
            while not shutdown.wait(min(10.0,max(1.0,self.lease_seconds / 4))):
                try:
                    if not self._heartbeat(jid,owner):
                        lease_lost.set()
                        return
                except sqlite3.Error:
                    # SQLite's own busy timeout and next heartbeat retry apply.
                    continue
        thread=threading.Thread(target=renew, name='timepilot-upload-heartbeat', daemon=True)
        thread.start()
        def notify(event,**info):
            if lease_lost.is_set():
                raise ProjectLeaseExpired('后台扫描租约丢失，结果未提交')
            self._progress(jid,owner,event,**info)
        try:
            members = json.loads(job['files_json'])
            files = []
            for member in members:
                path = self.root / jid / member['stored']
                if not path.is_file() or path.stat().st_size != member['bytes']:
                    raise SmartImportError('临时上传文件丢失或大小已改变')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        digest.update(block)
                if digest.hexdigest() != member['sha256']:
                    raise SmartImportError('临时上传文件哈希已改变，拒绝处理')
                files.append((path, member['name']))
            try:
                manifest = self.catalog.imports.manifest(jid)
            except (KeyError, FileNotFoundError):
                # Interrupted ingestion before manifest commit has no valid
                # immutable import. Only this job's deterministic directory
                # can be removed before retrying.
                raw_folder = self.catalog.imports.root / jid
                if raw_folder.exists():
                    shutil.rmtree(raw_folder)
                with open(files[0][0], 'rb') if len(files)==1 else _multi_handles(files) as handle:
                    if len(files)==1:
                        manifest = self.catalog.imports.ingest(handle, files[0][1], import_id=jid,
                            checkpoint_dir=self.root/jid/'checkpoints',progress_callback=notify)
                    else:
                        manifest = self.catalog.imports.ingest_many(handle, import_id=jid,
                            checkpoint_dir=self.root/jid/'checkpoints',progress_callback=notify)
            if manifest['import_id'] != jid:
                raise SmartImportError('任务与数据版本编号不一致')
            self._assert_owned(jid,owner)
            self.catalog.attach_import(job['project_id'], jid)
            self._finish(jid,owner,success=True)
            self.cleanup_finished()
            return {'job_id': jid,'status':'succeeded','import_id':jid}
        except Exception as exc:
            self._finish(jid,owner,success=False,error=exc)
            return {'job_id':jid,'status':'failed','error':str(exc)[:600]}
        finally:
            shutdown.set()
            thread.join(timeout=2)


class _multi_handles:
    def __init__(self, files):
        self.files=files
        self.handles=[]

    def __enter__(self):
        self.handles=[(open(path,'rb'),name) for path,name in self.files]
        return self.handles

    def __exit__(self, *args):
        for handle,_ in self.handles:
            handle.close()
