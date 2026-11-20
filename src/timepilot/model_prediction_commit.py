"""v0.40.0: fenced two-stage publication for new tabular model runs.

Prepared files are private and invisible to readers. The final transaction
atomically commits the model result, prediction metadata and publication
receipt. A crash after directory rename is recovered by the next fenced owner.
SQLite/FS cannot share an ACID transaction: signed prepared metadata and
idempotent renames provide the recoverable boundary.
"""
from __future__ import annotations

import csv
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .model_prediction_assets import _clean_rows, _sha, FIELDS, NUMERIC, write_parquet_blocks
from .model_task_spec import canonical_sha
from .model_task_store import ModelTaskError, ModelTaskConflict
from .project_migrations import catalog_startup_lock


def _now():
    return datetime.now(timezone.utc).isoformat()


class FencedPredictionCommit:
    def __init__(self, assets):
        self.assets=assets
        self.catalog=assets.catalog
        self.root=assets.root
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_prediction_preparations (
                run_id TEXT PRIMARY KEY REFERENCES model_task_runs(run_id),
                project_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
                lease_owner TEXT NOT NULL,lease_epoch INTEGER NOT NULL,
                executor TEXT NOT NULL,result_json TEXT NOT NULL,result_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
                staged_rel TEXT NOT NULL,final_rel TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('prepared','committed','aborted')),
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_prepared_assets
                ON model_prediction_preparations(state,created_at)''')

    def _row(self,run_id):
        with self.catalog._db() as db:
            x=db.execute('SELECT * FROM model_prediction_preparations WHERE run_id=?',
                         (run_id,)).fetchone()
        return dict(x) if x else None

    def prepared(self,run_id):
        value=self._row(run_id)
        return value is not None and value['state']=='prepared'

    @staticmethod
    def _valid_owner(db,run_id,token,epoch):
        row=db.execute('''SELECT r.*,c.cancel_requested_at,c.deadline_epoch
          FROM model_task_runs r JOIN model_experiment_controls c ON c.run_id=r.run_id
          WHERE r.run_id=?''',(run_id,)).fetchone()
        return (row is not None and row['state']=='running' and
                row['lease_owner']==token and row['lease_epoch']==epoch and
                row['lease_deadline'] is not None and row['lease_deadline']>time.time() and
                not row['cancel_requested_at'] and row['deadline_epoch']>time.time())

    def _manifest(self,pid,tid,task,run_id,executor,result_sha,rows,files):
        aid=self.assets._asset_id(pid,tid,task['revision'],run_id)
        prefix=self._prefix(pid)
        rel=str(Path(prefix)/aid)
        payload={'asset_id':aid,'project_id':pid,'task_id':tid,
          'revision':task['revision'],'run_id':run_id,'executor':executor,
          'source_sha256':task['spec']['asset']['source_sha256'],
          'spec_sha256':task['spec_sha256'],'approval_sha256':task['approval_sha256'],
          'result_sha256':result_sha,'csv_rel':str(Path(rel)/'validation.csv'),
          'csv_sha256':files['csv_sha256'],
          'parquet_rel':str(Path(rel)/'validation.parquet') if files['parquet_sha256'] else None,
          'parquet_sha256':files['parquet_sha256'],'rows':len(rows)}
        return payload

    @staticmethod
    def _prefix(pid):
        import hashlib
        return hashlib.sha256(pid.encode()).hexdigest()[:16]

    def prepare(self,pid,tid,task,run_id,token,epoch,result,records,executor='sklearn_tabular'):
        """Persist private, signed candidate result; do not make it public."""
        self.catalog.writable(pid)
        result_blob=json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False)
        import hashlib
        result_sha=hashlib.sha256(result_blob.encode('utf-8')).hexdigest()
        self.assets._verify_active(pid,tid,task['revision'],task['spec']['asset']['source_sha256'],
                                   task['spec_sha256'],task['approval_sha256'])
        with self.catalog._db() as db:
            if not self._valid_owner(db,run_id,token,epoch):
                raise ModelTaskConflict('RUN_LEASE_LOST: cannot prepare model predictions')
        rows=_clean_rows(records)
        aid=self.assets._asset_id(pid,tid,task['revision'],run_id)
        base=self.root/'_prepared'/aid
        base.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='attempt-',dir=base))
        try:
            stage.chmod(0o700)
        except OSError:
            pass
        try:
            with (stage/'validation.csv').open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=FIELDS)
                writer.writeheader();writer.writerows(rows)
            files={'csv_sha256':_sha(stage/'validation.csv'),'parquet_sha256':None}
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError:
                pass
            else:
                write_parquet_blocks(stage/'validation.parquet',rows)
                files['parquet_sha256']=_sha(stage/'validation.parquet')
            manifest=self._manifest(pid,tid,task,run_id,executor,result_sha,rows,files)
            manifest_blob=json.dumps(manifest,sort_keys=True,ensure_ascii=False,allow_nan=False)
            manifest_sha=canonical_sha(manifest)
            self.assets._verify_active(pid,tid,task['revision'],manifest['source_sha256'],
                                       manifest['spec_sha256'],manifest['approval_sha256'])
            with self.catalog._lock,self.catalog._db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._valid_owner(db,run_id,token,epoch):
                    raise ModelTaskConflict('RUN_LEASE_LOST: cannot record prepared predictions')
                existing=db.execute('SELECT state FROM model_prediction_preparations WHERE run_id=?',
                                    (run_id,)).fetchone()
                if existing:
                    raise ModelTaskConflict('Prediction preparation already exists; recover instead of overwriting')
                db.execute('''INSERT INTO model_prediction_preparations
                    (run_id,project_id,task_id,revision,lease_owner,lease_epoch,executor,
                     result_json,result_sha256,manifest_json,manifest_sha256,staged_rel,
                     final_rel,state,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (run_id,pid,tid,task['revision'],token,epoch,executor,result_blob,result_sha,
                     manifest_blob,manifest_sha,str(stage.relative_to(self.root)),
                     str(Path(self._prefix(pid))/aid),'prepared',_now(),_now()))
        except BaseException:
            shutil.rmtree(stage,ignore_errors=True)
            raise
        return {'state':'prepared','run_id':run_id,'result_sha256':result_sha,
                'manifest_sha256':manifest_sha}

    def transfer(self,db,run_id,token,epoch):
        """Under the caller's claim transaction, re-fence an interrupted prepare."""
        db.execute('''UPDATE model_prediction_preparations SET lease_owner=?,lease_epoch=?,updated_at=?
            WHERE run_id=? AND state='prepared' ''',(token,epoch,_now(),run_id))

    def commit(self,pid,tid,run_id,token,epoch):
        """Finalize prepared bytes and model result under the same fenced DB commit."""
        prep=self._row(run_id)
        if not prep or prep['state']!='prepared':
            raise ModelTaskError('No prepared prediction asset available for commit')
        if prep['project_id']!=pid or prep['task_id']!=tid:
            raise ModelTaskConflict('Project/task does not own the prepared predictions')
        if prep['lease_owner']!=token or prep['lease_epoch']!=epoch:
            raise ModelTaskConflict('RUN_LEASE_LOST: prepared asset belongs to a newer worker')
        manifest=json.loads(prep['manifest_json'])
        if canonical_sha(manifest)!=prep['manifest_sha256']:
            raise ModelTaskError('Prepared prediction manifest SHA256 mismatch')
        import hashlib
        if hashlib.sha256(prep['result_json'].encode('utf-8')).hexdigest()!=prep['result_sha256']:
            raise ModelTaskError('Prepared model result SHA256 mismatch')
        if manifest['result_sha256']!=prep['result_sha256']:
            raise ModelTaskError('Prepared model result/asset digest mismatch')
        self.assets._verify_active(pid,tid,prep['revision'],manifest['source_sha256'],
                                   manifest['spec_sha256'],manifest['approval_sha256'])
        aid=manifest['asset_id']
        try:
            with catalog_startup_lock(self.root/'.publication-locks'/(aid+'.lock'),timeout_seconds=30):
                with self.catalog._db() as db:
                    if not self._valid_owner(db,run_id,token,epoch):
                        raise ModelTaskConflict('RUN_LEASE_LOST: cannot commit prepared asset')
                stage=self.assets._inside(prep['staged_rel'])
                final=self.assets._inside(prep['final_rel'])
                # An interrupted rename leaves final in place, but nothing may overwrite it.
                if not final.is_dir():
                    if not stage.is_dir():
                        raise ModelTaskError('Prepared prediction folder was lost before commit')
                    final.parent.mkdir(parents=True,exist_ok=True)
                    stage.replace(final)
                self._verify_files(final,manifest)
                with self.catalog._lock,self.catalog._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not self._valid_owner(db,run_id,token,epoch):
                        raise ModelTaskConflict('RUN_LEASE_LOST: final publication ownership changed')
                    task=db.execute('''SELECT revision,status,spec_sha256,approval_sha256
                        FROM model_task_specs WHERE task_id=? AND project_id=?''',(tid,pid)).fetchone()
                    if not task or task['revision']!=prep['revision'] or task['status']!='approved' or \
                       task['spec_sha256']!=manifest['spec_sha256'] or task['approval_sha256']!=manifest['approval_sha256']:
                        raise ModelTaskConflict('Task revision changed before final prediction commit')
                    found=db.execute('SELECT manifest_sha256 FROM model_prediction_assets WHERE asset_id=?',
                                     (aid,)).fetchone()
                    if found and found['manifest_sha256']!=prep['manifest_sha256']:
                        raise ModelTaskConflict('A different signed prediction asset was already committed')
                    if not found:
                        db.execute('''INSERT INTO model_prediction_assets
                            (asset_id,project_id,task_id,revision,run_id,executor,source_sha256,
                             spec_sha256,approval_sha256,result_sha256,csv_rel,csv_sha256,
                             parquet_rel,parquet_sha256,rows,manifest_sha256,created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (*(manifest[k] for k in ('asset_id','project_id','task_id','revision',
                                'run_id','executor','source_sha256','spec_sha256',
                                'approval_sha256','result_sha256','csv_rel','csv_sha256',
                                'parquet_rel','parquet_sha256','rows')),
                             prep['manifest_sha256'],_now()))
                    updated=db.execute('''UPDATE model_task_runs SET state='succeeded',
                        lease_owner=NULL,lease_deadline=NULL,result_json=?,result_sha256=?,
                        error_code=NULL,error_message=NULL,updated_at=?
                        WHERE run_id=? AND state='running' AND lease_owner=? AND lease_epoch=?
                        AND lease_deadline>?''',
                        (prep['result_json'],prep['result_sha256'],_now(),run_id,token,epoch,time.time()))
                    if updated.rowcount!=1:
                        raise ModelTaskConflict('RUN_LEASE_LOST: failed fenced run commit')
                    db.execute('''UPDATE model_prediction_preparations SET state='committed',updated_at=?
                        WHERE run_id=? AND state='prepared' AND lease_owner=? AND lease_epoch=?''',
                        (_now(),run_id,token,epoch))
        except TimeoutError as exc:
            raise ModelTaskConflict('Prepared asset commit lock is busy') from exc
        return self.assets.get(pid,tid,run_id)

    @staticmethod
    def _verify_files(final,manifest):
        if not final.is_dir() or _sha(final/'validation.csv')!=manifest['csv_sha256']:
            raise ModelTaskError('Staged/final CSV was lost or modified')
        if manifest['parquet_rel']:
            if _sha(final/'validation.parquet')!=manifest['parquet_sha256']:
                raise ModelTaskError('Staged/final Parquet was lost or modified')

    def abort(self,run_id,token,epoch):
        # Keep signed files for restricted forensic inspection, never serve them.
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE model_prediction_preparations SET state='aborted',updated_at=?
              WHERE run_id=? AND lease_owner=? AND lease_epoch=? AND state='prepared' ''',
              (_now(),run_id,token,epoch))
