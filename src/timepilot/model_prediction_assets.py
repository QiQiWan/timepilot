"""v0.39.6 immutable, revision-bound validation prediction artifacts.

Data are private server-side outputs, never public static files. Rows preserve
source-row hashes and model/fold provenance. Every read re-verifies SHA256 and
project/task approval; the optional Parquet twin is also independently hashed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .model_task_spec import canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError
from .project_migrations import catalog_startup_lock

MAX_PREDICTIONS = 1_000_000
FIELDS = ('__tp_rowid','model','fold','source_row_sha256','source_row_index',
    'series_id','event_time_utc','decision_time_utc','label_mature_utc',
    'month','actual','prediction','residual','absolute_error','predicted_label','actual_label')
NUMERIC = frozenset(('__tp_rowid','fold','source_row_index','actual','prediction',
                     'residual','absolute_error','predicted_label','actual_label'))
ASSET_FORMAT = 'validation-predictions-v1'


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1048576),b''):
            digest.update(block)
    return digest.hexdigest()


def _clean_rows(records):
    if not isinstance(records,list) or not 1<=len(records)<=MAX_PREDICTIONS:
        raise ModelTaskError('Prediction asset has no rows or exceeds the 1,000,000-row export limit')
    output=[]
    for i,item in enumerate(records):
        if not isinstance(item,dict) or not re.fullmatch('[a-f0-9]{64}',str(item.get('source_row_sha256',''))):
            raise ModelTaskError('Prediction row provenance is incomplete')
        row={name:item.get(name) for name in FIELDS}
        row['__tp_rowid']=i
        if not isinstance(row['model'],str) or not 1<=len(row['model'])<=100:
            raise ModelTaskError('Prediction model identifier is invalid')
        if type(row['fold']) is not int or row['fold']<1 or row['fold']>1000:
            raise ModelTaskError('Prediction fold identifier is invalid')
        for field in NUMERIC:
            if row[field] is None:
                continue
            value=float(row[field])
            if not math.isfinite(value):raise ModelTaskError('Prediction contains a nonfinite value')
            if field in ('fold','__tp_rowid','source_row_index','actual_label','predicted_label'):
                if value!=int(value):raise ModelTaskError('Prediction index/label must be integral')
        if row['actual'] is None or row['prediction'] is None:
            raise ModelTaskError('Validation prediction must include actual and predicted values')
        if row['residual'] is None or abs(float(row['residual'])-(float(row['prediction'])-float(row['actual'])))>1e-6:
            raise ModelTaskError('Residual does not agree with prediction and actual')
        if row['absolute_error'] is None or abs(float(row['absolute_error'])-abs(float(row['residual'])))>1e-6:
            raise ModelTaskError('Absolute error does not agree with residual')
        output.append(row)
    return output


def write_parquet_blocks(path, rows, *, block_rows=32768):
    """Bound the additional Arrow allocation during immutable publication.

    The Parquet twin is sorted by filter dimensions for row-group statistics and
    dictionary pushdown. __tp_rowid is retained as the original stable ID.
    Readers MUST ORDER BY __tp_rowid; Parquet physical position has no identity.
    Requires the real PyArrow dependency; the caller handles its absence.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    if not rows:
        raise ModelTaskError('Parquet prediction publication cannot be empty')
    types={key:(pa.int64() if key in (
        '__tp_rowid','fold','source_row_index','predicted_label','actual_label')
        else pa.float64() if key in NUMERIC else pa.string()) for key in FIELDS}
    schema=pa.schema([pa.field(k,t) for k,t in types.items()])
    order=sorted(range(len(rows)),key=lambda i:(
        str(rows[i]['model']),int(rows[i]['fold']),str(rows[i]['month'] or ''),
        str(rows[i]['series_id'] or ''),int(rows[i]['__tp_rowid'])))
    with pq.ParquetWriter(str(path),schema,compression='zstd',
                          use_dictionary=['model','month','series_id'],
                          write_statistics=True) as writer:
        for begin in range(0,len(order),block_rows):
            part=(rows[idx] for idx in order[begin:begin+block_rows])
            columns={k:[] for k in FIELDS}
            for record in part:
                for key in FIELDS:columns[key].append(record.get(key))
            table=pa.Table.from_pydict(columns,schema=schema)
            writer.write_table(table,row_group_size=block_rows)
    return path


class ModelPredictionAssets:
    def __init__(self,tasks):
        self.tasks,self.catalog=tasks,tasks.catalog
        root=Path(getattr(self.catalog,'root',Path(self.catalog.db_file).parent))
        self.root=root/'model_prediction_assets'
        self.root.mkdir(parents=True,exist_ok=True)
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_prediction_assets (
                asset_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                revision INTEGER NOT NULL,run_id TEXT NOT NULL,executor TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,spec_sha256 TEXT NOT NULL,
                approval_sha256 TEXT NOT NULL,result_sha256 TEXT NOT NULL,
                csv_rel TEXT NOT NULL,csv_sha256 TEXT NOT NULL,
                parquet_rel TEXT,parquet_sha256 TEXT,
                rows INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,UNIQUE(project_id,task_id,revision,run_id))''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_prediction_asset_scope
                ON model_prediction_assets(project_id,task_id,revision)''')

    @staticmethod
    def _asset_id(pid,tid,rev,run_id):
        return str(uuid5(NAMESPACE_URL,f'timepilot-validation:{pid}:{tid}:{rev}:{run_id}'))

    def _verify_active(self,pid,tid,revision,source_sha,spec_sha,approval_sha):
        task=self.tasks.get(pid,tid)
        if (task['revision']!=revision or task['source_state']!='current' or
             task['spec_sha256']!=spec_sha or task['approval_sha256']!=approval_sha or
             task['status']!='approved' or task['spec']['asset']['source_sha256']!=source_sha):
            raise ModelTaskConflict('Prediction asset is from an outdated/unapproved task or data source')
        return task

    def _inside(self,rel):
        if not isinstance(rel,str) or not rel or Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ModelTaskError('Unsafe prediction asset path')
        resolved=(self.root/rel).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ModelTaskError('Prediction asset path escapes immutable store')
        return resolved

    def _read_metadata(self,pid,tid,revision,run_id):
        with self.catalog._db() as db:
            row=db.execute('''SELECT * FROM model_prediction_assets WHERE
                project_id=? AND task_id=? AND revision=? AND run_id=?''',
                (pid,tid,revision,run_id)).fetchone()
        return dict(row) if row else None

    def publish(self,pid,tid,task,run_id,executor,result_sha,records):
        """Serialize immutable publication across processes, then verify approval."""
        aid=self._asset_id(pid,tid,task['revision'],run_id)
        # Prevent concurrent requests for the same run from overwriting the
        # first signed CSV/Parquet before either SQLite INSERT has committed.
        try:
            with catalog_startup_lock(self.root/'.publication-locks'/(aid+'.lock'),
                                      timeout_seconds=30):
                return self._publish_locked(pid,tid,task,run_id,executor,result_sha,records)
        except TimeoutError as exc:
            raise ModelTaskConflict('Prediction artifact publication is busy; retry the verified read') from exc

    def _publish_locked(self,pid,tid,task,run_id,executor,result_sha,records):
        """Idempotent immutable publish; source approval is checked twice."""
        self.catalog.writable(pid)
        self._verify_active(pid,tid,task['revision'],task['spec']['asset']['source_sha256'],
                            task['spec_sha256'],task['approval_sha256'])
        aid=self._asset_id(pid,tid,task['revision'],run_id)
        existing=self._read_metadata(pid,tid,task['revision'],run_id)
        if existing:
            if existing['result_sha256']!=result_sha:
                raise ModelTaskConflict('Prediction artifact already references a different model result')
            return self.get(pid,tid,run_id)
        rows=_clean_rows(records)
        # No upload-name or client path enters filesystem construction.
        prefix=hashlib.sha256(pid.encode()).hexdigest()[:16]
        folder=self.root/prefix/aid
        folder.mkdir(parents=True,exist_ok=True)
        try:folder.chmod(0o700)
        except OSError:pass
        csv_path=folder/'validation.csv'
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='',dir=folder,
                                         prefix='.pending-',suffix='.csv',delete=False) as stream:
            stage=Path(stream.name)
            try:stage.chmod(0o600)
            except OSError:pass
            writer=csv.DictWriter(stream,fieldnames=FIELDS)
            writer.writeheader();writer.writerows(rows)
        stage.replace(csv_path)
        csv_sha=_sha(csv_path)
        parquet_path=folder/'validation.parquet'
        parquet_rel=parquet_sha=None
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            pass
        else:
            stage_parquet=folder/'.validation.pending.parquet'
            write_parquet_blocks(stage_parquet,rows)
            stage_parquet.replace(parquet_path)
            parquet_sha=_sha(parquet_path)
            parquet_rel=str(parquet_path.relative_to(self.root))
        csv_rel=str(csv_path.relative_to(self.root))
        manifest={'asset_id':aid,'project_id':pid,'task_id':tid,
          'revision':task['revision'],'run_id':run_id,'executor':executor,
          'source_sha256':task['spec']['asset']['source_sha256'],
          'spec_sha256':task['spec_sha256'],'approval_sha256':task['approval_sha256'],
          'result_sha256':result_sha,'csv_rel':csv_rel,'csv_sha256':csv_sha,
          'parquet_rel':parquet_rel,'parquet_sha256':parquet_sha,'rows':len(rows)}
        fingerprint=canonical_sha(manifest)
        self._verify_active(pid,tid,task['revision'],manifest['source_sha256'],
                            manifest['spec_sha256'],manifest['approval_sha256'])
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT OR IGNORE INTO model_prediction_assets
                (asset_id,project_id,task_id,revision,run_id,executor,source_sha256,
                 spec_sha256,approval_sha256,result_sha256,csv_rel,csv_sha256,
                 parquet_rel,parquet_sha256,rows,manifest_sha256,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (*manifest.values(),fingerprint,datetime.now(timezone.utc).isoformat()))
        return self.get(pid,tid,run_id)

    def get(self,pid,tid,run_id):
        self.catalog.get(pid)
        task=self.tasks.get(pid,tid)
        row=self._read_metadata(pid,tid,task['revision'],run_id)
        if not row:raise KeyError('No prediction asset for this task revision and run')
        expected={k:row[k] for k in ('asset_id','project_id','task_id','revision','run_id','executor',
            'source_sha256','spec_sha256','approval_sha256','result_sha256','csv_rel',
            'csv_sha256','parquet_rel','parquet_sha256','rows')}
        if canonical_sha(expected)!=row['manifest_sha256']:
            raise ModelTaskError('Prediction asset manifest SHA256 mismatch')
        self._verify_active(pid,tid,row['revision'],row['source_sha256'],
                            row['spec_sha256'],row['approval_sha256'])
        path=self._inside(row['csv_rel'])
        if not path.is_file() or _sha(path)!=row['csv_sha256']:
            raise ModelTaskError('Prediction CSV checksum verification failed')
        if row['parquet_rel']:
            path=self._inside(row['parquet_rel'])
            if not path.is_file() or _sha(path)!=row['parquet_sha256']:
                raise ModelTaskError('Prediction Parquet checksum verification failed')
        return {'asset_id':row['asset_id'],'task_id':tid,'project_id':pid,
             'run_id':run_id,'revision':row['revision'],'source_sha256':row['source_sha256'],
             'spec_sha256':row['spec_sha256'],'result_sha256':row['result_sha256'],
             'rows':row['rows'],'format':ASSET_FORMAT,'csv_sha256':row['csv_sha256'],
             'parquet_sha256':row['parquet_sha256'],'manifest_sha256':row['manifest_sha256'],
             'perspective_ready':True,
             'maximum_prediction_rows_supported':MAX_PREDICTIONS,
             'perspective_format':'parquet' if row['parquet_rel'] else 'verified_csv_fallback',
             'data_policy':'read_only_validation_predictions; immutable; source_and_revision_bound'}

    def source_file(self,pid,tid,run_id,asset_id):
        asset=self.get(pid,tid,run_id)
        if asset['asset_id']!=asset_id:raise KeyError('Prediction asset does not belong to this model run')
        row=self._read_metadata(pid,tid,asset['revision'],run_id)
        relative=row['parquet_rel'] or row['csv_rel']
        return self._inside(relative), bool(row['parquet_rel'])

    def rows(self,pid,tid,run_id,*,offset=0,limit=100,model=None,fold=None):
        if not 0<=offset<=2000000 or not 1<=limit<=500:
            raise ModelTaskError('Invalid bounded prediction pagination')
        asset=self.get(pid,tid,run_id)
        row=self._read_metadata(pid,tid,asset['revision'],run_id)
        with self._inside(row['csv_rel']).open(newline='',encoding='utf-8') as handle:
            reader=csv.DictReader(handle)
            from itertools import islice
            if model is not None or fold is not None:
                reader=(item for item in reader if (model is None or item['model']==model)
                        and (fold is None or int(item['fold'])==fold))
            # Read one extra row to determine whether the filtered page ends.
            values=list(islice(reader,offset,offset+limit+1))
        has_more=len(values)>limit
        values=values[:limit]
        return {'asset':asset,'offset':offset,'limit':limit,'model':model,'fold':fold,'items':values,
                'next_offset':offset+len(values) if has_more else None}
