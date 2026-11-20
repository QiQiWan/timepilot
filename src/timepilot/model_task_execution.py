"""v0.39.1: revision-bound, durable supervised tabular model comparison.

Only approved ModelTaskSpec may enter the queue. Training uses verified full
versioned rows and train-fold-only transformations. No raw sample predictions,
serialised model objects, or user-supplied Python/SQL enter the result store.
A failed/expired worker may re-evaluate its pure computation: only the fenced
SQLite result commit is a side effect.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .model_task_spec import ModelTaskSpec, canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError

LOG = logging.getLogger(__name__)
ENGINE_VERSION = 'tabular-comparison-5-fenced-experiment'
MAX_ROWS = 20000
MAX_COLUMNS = 32
LEASE_SECONDS = 60
from .model_experiment_control import (ExperimentControls, DEFAULT_PROFILE,
    RunCancelled, RunLeaseLost, ExperimentBudgetExhausted)


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _sha_indices(indices):
    return canonical_sha([int(v) for v in indices])


def _optional_number(value):
    if value is None:
        return None
    v = float(value)
    return round(v, 8) if math.isfinite(v) else None


class ModelTaskRunService:
    def __init__(self, tasks, modeling):
        self.tasks, self.modeling, self.catalog = tasks, modeling, tasks.catalog
        self._setup()
        from .model_prediction_assets import ModelPredictionAssets
        self.predictions = ModelPredictionAssets(tasks)
        self.experiments = ExperimentControls(self.catalog)
        from .model_prediction_commit import FencedPredictionCommit
        self.prepared = FencedPredictionCommit(self.predictions)

    def _setup(self):
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                revision INTEGER NOT NULL, spec_sha256 TEXT NOT NULL,
                approval_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                run_key TEXT NOT NULL UNIQUE, engine_version TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed')),
                lease_owner TEXT, lease_epoch INTEGER NOT NULL DEFAULT 0,
                lease_deadline REAL, attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT, result_sha256 TEXT, error_code TEXT, error_message TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_task_runs_claim
                ON model_task_runs(state, lease_deadline, created_at)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_task_runs_task
                ON model_task_runs(project_id,task_id,created_at)''')

    @staticmethod
    def _id(value):
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError):
            raise ModelTaskError('Invalid model run identifier') from None

    def submit(self, pid: str, task_id: str, *, budget_profile=DEFAULT_PROFILE) -> dict:
        self.experiments.plan(budget_profile)  # validate before creating a run
        self.catalog.writable(pid)
        doc = self.tasks.get(pid, task_id)
        if doc['source_state'] != 'current':
            raise ModelTaskConflict('Source changed: create and approve a new task')
        if doc['status'] != 'approved' or not doc['approval_sha256']:
            raise ModelTaskConflict('Please preflight and approve the saved task before training')
        spec = ModelTaskSpec.model_validate(doc['spec'])
        if spec.task_kind not in ('tabular_regression', 'tabular_classification'):
            raise ModelTaskError('Temporal tasks continue to use their existing PIT-aware executor')
        if spec.task_kind == 'tabular_classification' and (
                not spec.target.positive_values or not spec.target.negative_values):
            raise ModelTaskError('Classification requires explicit positive and negative label values')
        key = canonical_sha({'task': self._id(task_id), 'revision': doc['revision'],
                             'spec': doc['spec_sha256'], 'approval': doc['approval_sha256'],
                             'engine': ENGINE_VERSION})
        ts = _iso()
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.tasks._row(db, pid, task_id)
            if (row['status'] != 'approved' or row['revision'] != doc['revision'] or
                    row['spec_sha256'] != doc['spec_sha256'] or
                    row['approval_sha256'] != doc['approval_sha256']):
                raise ModelTaskConflict('Task approval changed before the run was queued')
            # A task revision owns one durable run. Reopening a historical
            # revision with a newer engine never silently starts a second one.
            saved=db.execute('''SELECT run_id FROM model_task_runs WHERE project_id=?
                AND task_id=? AND revision=? AND spec_sha256=? AND approval_sha256=?
                ORDER BY created_at,run_id LIMIT 1''',
                (self.catalog._id(pid),self._id(task_id),doc['revision'],
                 doc['spec_sha256'],doc['approval_sha256'])).fetchone()
            if saved is None:
                db.execute('''INSERT OR IGNORE INTO model_task_runs
                    (run_id,project_id,task_id,revision,spec_sha256,approval_sha256,
                     source_sha256,run_key,engine_version,state,created_at,updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (str(uuid4()),self.catalog._id(pid),self._id(task_id),doc['revision'],
                     doc['spec_sha256'],doc['approval_sha256'],spec.asset.source_sha256,
                     key,ENGINE_VERSION,'queued',ts,ts))
                saved = db.execute('SELECT run_id FROM model_task_runs WHERE run_key=?', (key,)).fetchone()
            self.experiments.freeze(db,run_id=saved['run_id'],pid=self.catalog._id(pid),
                tid=self._id(task_id),revision=doc['revision'],profile=budget_profile)
        return self.get(pid, saved['run_id'])

    def get(self, pid: str, run_id: str) -> dict:
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row = db.execute('SELECT * FROM model_task_runs WHERE project_id=? AND run_id=?',
                (self.catalog._id(pid),self._id(run_id))).fetchone()
        if row is None:
            raise KeyError('Model run not found in this project')
        out = dict(row)
        raw_result = out.pop('result_json')
        if raw_result is not None:
            if not out['result_sha256'] or hashlib.sha256(raw_result.encode('utf-8')).hexdigest() != out['result_sha256']:
                raise ModelTaskError('Persisted model result failed SHA256 verification')
            out['result'] = json.loads(raw_result)
        else:
            out['result'] = None
        # The stored historical result remains visible, never silently presented
        # as an approved model for a superseded spec or changed source.
        try:
            doc = self.tasks.get(pid, out['task_id'])
            valid = (doc['source_state'] == 'current' and doc['revision'] == out['revision']
                     and doc['spec_sha256'] == out['spec_sha256']
                     and doc['approval_sha256'] == out['approval_sha256'])
        except Exception:
            valid = False
        out['effective_state'] = out['state'] if valid else 'superseded'
        out['prediction_asset'] = None
        try:
            out['experiment'] = self.experiments.detail(pid,out['task_id'],out['run_id'])
        except KeyError:
            out['experiment'] = None  # unmodified runs from older releases
        if out['state']=='failed' and out['error_code']=='RUN_CANCELLED':
            out['effective_state']='cancelled' if valid else 'superseded'
        elif out['state']=='running' and out['experiment'] and out['experiment']['cancel_requested']:
            out['effective_state']='cancel_requested' if valid else 'superseded'
        if valid and out['state'] == 'succeeded':
            try:
                asset = self.predictions.get(pid, out['task_id'], out['run_id'])
                if asset['result_sha256'] != out['result_sha256']:
                    raise ModelTaskError('Prediction asset references a different evaluation digest')
                out['prediction_asset'] = asset
            except KeyError:
                pass  # Earlier run predates versioned predictions.
        return out

    def list(self, pid: str, task_id: str, limit: int = 50) -> list[dict]:
        self.catalog.get(pid)
        self.tasks.get(pid,task_id,check_source=False)
        with self.catalog._db() as db:
            rows = db.execute('''SELECT run_id FROM model_task_runs WHERE project_id=? AND task_id=?
                ORDER BY created_at DESC LIMIT ?''',
                (self.catalog._id(pid),self._id(task_id),max(1,min(limit,100)))).fetchall()
        return [self.get(pid, x['run_id']) for x in rows]

    def cancel(self, pid: str, task_id: str, run_id: str):
        self.tasks.get(pid,task_id,check_source=False)
        return self.experiments.cancel(pid,self._id(task_id),self._id(run_id))

    def steps(self, pid: str, task_id: str, run_id: str):
        self.tasks.get(pid,task_id,check_source=False)
        return {'run_id':self._id(run_id),'items':self.experiments.steps(pid,self._id(task_id),self._id(run_id))}

    def preview(self,pid,task_id,profile=DEFAULT_PROFILE):
        task=self.tasks.get(pid,task_id)
        spec=ModelTaskSpec.model_validate(task['spec'])
        plan=self.experiments.plan(profile)
        return {'task_id':self._id(task_id),'task_revision':task['revision'],
                'source_sha256':spec.asset.source_sha256,'spec_sha256':task['spec_sha256'],
                'status':task['status'],'source_state':task['source_state'],
                'executor':'sklearn_tabular' if spec.task_kind.startswith('tabular_') else 'existing_temporal_pit',
                'available_for_total_budget':spec.task_kind.startswith('tabular_'),
                'validation_kind':spec.validation.kind,
                'requested_folds':spec.validation.folds,
                'candidate_models':['baseline','linear','forest'] if spec.task_kind.startswith('tabular_') else None,
                'plan':plan,'plan_sha256':canonical_sha(plan),
                'note':'Preview counts are contractual; actual splits may fail source/time/label checks.'}

    def _claim(self):
        token = str(uuid4())
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            while True:
                now=time.time()
                row = db.execute('''SELECT * FROM model_task_runs
                    WHERE state='queued' OR (state='running' AND lease_deadline < ?)
                    ORDER BY created_at,run_id LIMIT 1''', (now,)).fetchone()
                if row is None:return None
                rejection=self.experiments.on_claim(db,row['run_id'],now)
                if rejection:
                    db.execute('''UPDATE model_task_runs SET state='failed',error_code=?,
                        error_message=?,lease_owner=NULL,lease_deadline=NULL,updated_at=?
                        WHERE run_id=? AND state IN ('queued','running')''',
                        (rejection,'Experiment cancelled or its total budget expired',_iso(),row['run_id']))
                    # A time-expired preparation is never published by a later worker.
                    # Keep its signed bytes for private incident inspection only.
                    db.execute('''UPDATE model_prediction_preparations SET state='aborted',updated_at=?
                        WHERE run_id=? AND state='prepared' ''',(_iso(),row['run_id']))
                    continue
                epoch=row['lease_epoch']+1
                updated=db.execute('''UPDATE model_task_runs SET state='running',lease_owner=?,
                    lease_epoch=?,lease_deadline=?,attempts=attempts+1,updated_at=?
                    WHERE run_id=? AND (state='queued' OR (state='running' AND lease_deadline < ?))''',
                    (token,epoch,now+LEASE_SECONDS,_iso(),row['run_id'],now))
                if updated.rowcount!=1:continue
                self.prepared.transfer(db,row['run_id'],token,epoch)
                return dict(row),token,epoch

    def work_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:return False
        row,token,epoch=claimed
        result,error,message=None,None,None
        try:
            with self.experiments.guard(row,token,epoch) as guard:
                # Restart after a crash between PREPARED and COMMITTED without
                # refitting or changing any signed fold result.
                if self.prepared.prepared(row['run_id']):
                    guard.ensure()
                    self.prepared.commit(row['project_id'],row['task_id'],row['run_id'],token,epoch)
                    return True
                result=self._execute(row,guard)
                prediction_rows=result.pop('_prediction_rows',[])
                guard.ensure()
                if prediction_rows:
                    task=self.tasks.get(row['project_id'],row['task_id'])
                    self.prepared.prepare(row['project_id'],row['task_id'],task,
                        row['run_id'],token,epoch,result,prediction_rows)
                    guard.ensure()
                    self.prepared.commit(row['project_id'],row['task_id'],row['run_id'],token,epoch)
                    return True
        except Exception as exc:
            LOG.exception('Model comparison failed: run_id=%s',row['run_id'])
            if isinstance(exc,RunCancelled):error='RUN_CANCELLED'
            elif isinstance(exc,ExperimentBudgetExhausted):error='EXPERIMENT_BUDGET_EXHAUSTED'
            elif isinstance(exc,RunLeaseLost):error='RUN_LEASE_LOST'
            else:error=type(exc).__name__
            message=(str(exc)[:240] if isinstance(exc,(ModelTaskError,ModelTaskConflict))
                else 'Internal model evaluation error; inspect server diagnostics')
        # On lost ownership a new worker alone may finish the prepared asset.
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('''SELECT revision,spec_sha256,approval_sha256,status
               FROM model_task_specs WHERE task_id=?''',(row['task_id'],)).fetchone()
            valid=(current is not None and current['revision']==row['revision']
                    and current['spec_sha256']==row['spec_sha256']
                    and current['approval_sha256']==row['approval_sha256']
                    and current['status']=='approved')
            if not valid:
                error,result,message='TaskRevisionChanged',None,'Task approval changed during training'
            control=db.execute('SELECT cancel_requested_at,deadline_epoch FROM model_experiment_controls WHERE run_id=?',
                               (row['run_id'],)).fetchone()
            if control and control['cancel_requested_at']:
                error,result,message='RUN_CANCELLED',None,'User cancelled the model experiment'
            elif control and control['deadline_epoch']<=time.time():
                error,result,message='EXPERIMENT_BUDGET_EXHAUSTED',None,'Experiment total wall budget expired'
            blob=json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False) if result is not None else None
            cursor=db.execute('''UPDATE model_task_runs SET state=?,lease_owner=NULL,
                lease_deadline=NULL,result_json=?,result_sha256=?,error_code=?,error_message=?,updated_at=?
                WHERE run_id=? AND state='running' AND lease_owner=? AND lease_epoch=?
                AND lease_deadline>=?''',
                ('succeeded' if blob else 'failed',blob,
                 hashlib.sha256(blob.encode()).hexdigest() if blob else None,
                 error,message,_iso(),row['run_id'],token,epoch,time.time()))
            if cursor.rowcount:
                # Failed owner cannot later recover these staged files.
                db.execute('''UPDATE model_prediction_preparations SET state='aborted',updated_at=?
                  WHERE run_id=? AND state='prepared' AND lease_owner=? AND lease_epoch=?''',
                  (_iso(),row['run_id'],token,epoch))
        return True

    def _read_rows(self, source, spec, *, max_rows=MAX_ROWS):
        import pandas as pd
        if int(source['row_count']) > max_rows:
            raise ModelTaskError('Training data exceeds the bounded 20,000-row direct comparison budget')
        if spec.version_id == 'base':
            path = Path(source['path'])
            with path.open('r',encoding='utf-8',newline='') as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != source['source_columns']:
                    raise ModelTaskError('Published CSV column contract changed')
                records = list(reader)
            if len(records) != source['row_count']:
                raise ModelTaskError('Published CSV row count changed')
            return pd.DataFrame(records,columns=source['source_columns'])
        # Real version overlay, no cached BI preview and no user SQL.
        with self.modeling.native._connection() as con:
            native = self.modeling.native.asset(spec.asset_id)
            self.modeling.native._install_effective_view(con,spec.asset_id,spec.version_id,native['columns'])
            count = int(con.execute('SELECT COUNT(*) FROM __tp_effective').fetchone()[0])
            if count > max_rows:
                raise ModelTaskError('Versioned training data exceeds direct comparison budget')
            frame = con.execute('SELECT * FROM __tp_effective ORDER BY "__tp_rowid"').df()
        if len(frame) != count:
            raise ModelTaskError('Incomplete versioned training data')
        return frame

    def _execute(self, row, guard=None):
        """Train every comparator on identical, audited, immutable fold indices."""
        import numpy as np
        import pandas as pd
        import sklearn
        from sklearn.model_selection import KFold, StratifiedKFold, GroupKFold
        from .model_fit_isolation import fit_fold,FIT_SECONDS,RSS_BYTES
        from .model_task_time_audit import audit_availability, make_temporal_folds
        from .model_task_diagnostics import (finite, metrics, diagnostics, aggregate_slices,
                                             permutation_importance)

        task = self.tasks.get(row['project_id'], row['task_id'])
        if (task['revision'] != row['revision'] or task['spec_sha256'] != row['spec_sha256']
                or task['approval_sha256'] != row['approval_sha256']
                or task['source_state'] != 'current'):
            raise ModelTaskConflict('Task/source changed after enqueue')
        spec = ModelTaskSpec.model_validate(task['spec'])
        if spec.task_kind not in ('tabular_regression', 'tabular_classification'):
            raise ModelTaskError('Temporal tasks require the existing PIT-aware executor')
        source = self.modeling._source(row['project_id'], spec.asset_id, spec.version_id)
        if (source['original_data_sha256'] != row['source_sha256']
                or source['native_sha256'] != spec.asset.native_sha256):
            raise ModelTaskConflict('Source SHA changed after enqueue')
        frame = self._read_rows(source, spec)
        cols = [x.column for x in spec.features]
        required = [spec.target.column, *cols]
        if spec.validation.kind in ('group_kfold', 'leave_device_out'):
            required += spec.time.entity_columns
        if spec.validation.kind in ('temporal_holdout', 'rolling_origin', 'leave_month_out', 'leave_device_out'):
            required.append(spec.time.event_time_column)
        required += [v for v in (spec.time.decision_time_column,
                                  spec.time.label_available_at_column,
                                  spec.time.feature_available_at_column) if v]
        required += [f.available_at_column for f in spec.features if f.available_at_column]
        missing = sorted(set(required) - set(frame.columns))
        if missing:
            raise ModelTaskError('Missing declared input columns: ' + ', '.join(missing))
        if len(frame) < 80 or len(cols) > MAX_COLUMNS:
            raise ModelTaskError('Need at least 80 rows and at most 32 feature columns')
        raw = frame[cols].copy()
        converted = raw.apply(pd.to_numeric, errors='coerce').astype(float)
        invalid = {c:int((raw[c].notna() & raw[c].astype(str).str.strip().ne('') &
                          converted[c].isna()).sum()) for c in cols}
        if any(invalid.values()):
            raise ModelTaskError('X contains non-numeric values: materialize encoded fields first')
        x = converted.to_numpy(dtype=float)
        if np.isinf(x).any():
            raise ModelTaskError('X contains non-finite numeric values')
        if np.isnan(x).all(axis=0).any():
            raise ModelTaskError('A declared X column contains no finite values')
        labels = frame[spec.target.column]
        classification = spec.task_kind == 'tabular_classification'
        if classification:
            if labels.isna().any():
                raise ModelTaskError('Classification label contains missing values')
            positive,negative = set(spec.target.positive_values),set(spec.target.negative_values)
            if not positive or not negative:
                raise ModelTaskError('Explicit positive and negative label values are mandatory')
            actual=labels.astype(str).str.strip()
            if not actual.isin(positive | negative).all():
                raise ModelTaskError('Label values outside the approved positive/negative mapping')
            y=actual.isin(positive).astype(int).to_numpy()
            if len(np.unique(y)) != 2:
                raise ModelTaskError('Binary training needs both positive and negative examples')
            kind='binary_classification'
        else:
            target=pd.to_numeric(labels,errors='coerce')
            if target.isna().any() or not np.isfinite(target.to_numpy(dtype=float)).all():
                raise ModelTaskError('Regression target has missing or non-numeric values')
            y=target.to_numpy(dtype=float)
            kind='regression'

        split=spec.validation.kind
        time_based = split in ('temporal_holdout', 'rolling_origin', 'leave_month_out', 'leave_device_out')
        ledger=None
        dates=decision=label_available=None
        if spec.time.event_time_column:
            ledger,dates,decision,label_available=audit_availability(
                frame,spec,require_labels=time_based)
        elif time_based:
            raise ModelTaskError('Forward-only validation requires an event clock')
        if split=='leave_device_out':
            keys=frame[spec.time.entity_columns].astype(str).agg('\x1f'.join,axis=1)
            if keys.nunique()<spec.validation.folds:
                raise ModelTaskError('Not enough distinct devices for transfer holdout')
            unique_dates=pd.DatetimeIndex(dates.unique()).sort_values()
            if len(unique_dates)<10:
                raise ModelTaskError('Not enough unique timestamps for device transfer holdout')
            cutoff=unique_dates[max(1,int(len(unique_dates)*.8))]
            iterator=[]
            for tr,va in GroupKFold(n_splits=spec.validation.folds).split(x,y,keys):
                tr=tr[(dates.iloc[tr]<cutoff).to_numpy()]
                if spec.validation.gap:
                    before=pd.DatetimeIndex(dates.iloc[tr].unique()).sort_values()
                    if len(before)<=spec.validation.gap:
                        raise ModelTaskError('Embargo gap removes all device transfer training timestamps')
                    tr=tr[(dates.iloc[tr]<before[-spec.validation.gap]).to_numpy()]
                tr=tr[(label_available.iloc[tr]<=cutoff).to_numpy()]
                va=va[(dates.iloc[va]>=cutoff).to_numpy()]
                iterator.append((tr,va,cutoff,{'gap_unit':'unique_event_timestamps'}))
        elif time_based:
            iterator=make_temporal_folds(frame,spec,event=dates,label_available=label_available)
        elif split=='group_kfold':
            keys=frame[spec.time.entity_columns].astype(str).agg('\x1f'.join,axis=1)
            if keys.nunique() < spec.validation.folds:
                raise ModelTaskError('Not enough distinct devices/groups for held-out folds')
            iterator=[(tr,va,None,{'gap_unit':'not_applicable'}) for tr,va in
                GroupKFold(n_splits=spec.validation.folds).split(x,y,keys)]
        elif split=='kfold':
            iterator=[(tr,va,None,{'gap_unit':'not_applicable'}) for tr,va in
                KFold(n_splits=spec.validation.folds,shuffle=True,random_state=42).split(x)]
        elif split=='stratified_kfold':
            if not classification: raise ModelTaskError('Stratified folds require classification')
            if min(np.bincount(y)) < spec.validation.folds:
                raise ModelTaskError('Not enough minority samples for stratified folds')
            iterator=[(tr,va,None,{'gap_unit':'not_applicable'}) for tr,va in
                StratifiedKFold(n_splits=spec.validation.folds,shuffle=True,
                                random_state=42).split(x,y)]
        else:
            raise ModelTaskError('This validation protocol has no tabular executor')

        # Each fitted candidate/fold executes in a killable bounded child.
        # Equal quotas do not imply equal realized compute or model capacity.
        budget={'policy':'fixed_per_candidate_per_fold_hard_process_cap_v2',
                'max_source_rows':MAX_ROWS,'max_features':MAX_COLUMNS,
                'max_fit_seconds_per_model_fold':FIT_SECONDS,
                'run_wall_seconds_total':(self.experiments.detail(row['project_id'],row['task_id'],
                                                row['run_id'])['budget']['run_wall_seconds_total'] if guard else None),
                'total_budget_enforced':bool(guard),
                'max_cpu_seconds_per_model_fold':FIT_SECONDS,
                'max_peak_rss_bytes_per_model_fold':RSS_BYTES,
                'termination':'kill_process_tree_on_wall_cpu_or_rss_limit',
                'cpu_threads':1,'seed':42,
                'models':{'baseline':{'estimator':'train_mean','fit':False},
                          'linear':{'regression':'Ridge(alpha=10)',
                                    'classification':'LogisticRegression(liblinear,max_iter=350)'},
                          'forest':{'trees':75,'max_depth':10,'min_samples_leaf':4}}}
        scores={name:[] for name in ('baseline','linear','forest')}
        manifests=[]
        fold_diagnostics={name:[] for name in scores}
        fit_cost={name:[] for name in scores}
        slice_buffer=[]
        last_contributions={}
        prediction_rows=[]
        for fold_number,(tr,va,cutoff,extra) in enumerate(iterator,1):
            if guard:guard.ensure()
            tr,va=np.asarray(tr,dtype=int),np.asarray(va,dtype=int)
            if len(tr)<40 or len(va)<15 or bool(set(tr)&set(va)):
                raise ModelTaskError(f'Unsafe split or insufficient train/validation rows (40/15), fold {fold_number}')
            if classification and (len(np.unique(y[tr]))!=2 or len(np.unique(y[va]))!=2):
                raise ModelTaskError(f'Both classes are required in train and validation fold {fold_number}')
            if split in ('group_kfold','leave_device_out'):
                if set(keys.iloc[tr]) & set(keys.iloc[va]):
                    raise ModelTaskError('Device/group leakage detected')
            if time_based and not (dates.iloc[tr].max()<dates.iloc[va].min()):
                raise ModelTaskError('Temporal leakage detected')
            evidence={'fold':fold_number,'split':split,'train_rows':int(len(tr)),
                      'validation_rows':int(len(va)),
                      'train_index_sha256':_sha_indices(tr),
                      'validation_index_sha256':_sha_indices(va),
                      'disjoint':True,'gap_unit':extra['gap_unit'],
                      'shared_by_all_models':True}
            if time_based:
                if not (label_available.iloc[tr]<=cutoff).all():
                    raise ModelTaskError('Unmatured training labels included')
                evidence.update({'cutoff_utc':cutoff.isoformat(),
                     'latest_train_event_utc':dates.iloc[tr].max().isoformat(),
                     'first_validation_event_utc':dates.iloc[va].min().isoformat(),
                     'training_labels_mature_at_cutoff':True,
                     'feature_publication_checked_at_decision':True,
                     'train_label_matured_rows':int(len(tr)),
                     'last_validation_label_maturity_utc':label_available.iloc[va].max().isoformat()})
            if split in ('group_kfold','leave_device_out'):
                evidence['train_groups_sha256']=canonical_sha(sorted(set(keys.iloc[tr])))
                evidence['validation_groups_sha256']=canonical_sha(sorted(set(keys.iloc[va])))
                evidence['heldout_groups']=len(set(keys.iloc[va]))
            manifests.append(evidence)
            predictions={'baseline':np.full(len(va),float(np.mean(y[tr])))}
            importance_by_family={}
            fit_cost['baseline'].append(0.0)
            if guard:
                with guard.step('baseline',fold_number) as step:
                    step.observed({'cpu_seconds':0.,'peak_rss_bytes':0})
            for family in ('linear','forest'):
                if guard:
                    with guard.step(family,fold_number) as step:
                        prediction,importance,cost=fit_fold(x[tr],y[tr],x[va],y[va],
                          kind=kind,family=family,features=cols,seed=42,
                          importance=(fold_number==len(iterator)),
                          seconds=guard.limit(FIT_SECONDS),stop_check=guard.ensure)
                        guard.ensure()
                        step.observed(cost)
                else:
                    prediction,importance,cost=fit_fold(x[tr],y[tr],x[va],y[va],
                          kind=kind,family=family,features=cols,seed=42,
                          importance=(fold_number==len(iterator)))
                fit_cost[family].append(cost)
                predictions[family]=np.asarray(prediction,dtype=float)
                importance_by_family[family]=importance
            for name,prediction in predictions.items():
                if not np.isfinite(prediction).all():
                    raise ModelTaskError('Non-finite model prediction')
                scores[name].append(metrics(y[va],prediction,classification))
                fold_diagnostics[name].append({'fold':fold_number,
                                               **diagnostics(y[va],prediction,classification)})
                for j,source_idx in enumerate(va):
                    truth=float(y[source_idx]); predicted=float(prediction[j]); residual=predicted-truth
                    when=dates.iloc[source_idx] if dates is not None else None
                    entity=('|'.join(map(str,frame[spec.time.entity_columns].iloc[source_idx].tolist()))
                        if spec.time.entity_columns else None)
                    prediction_rows.append({
                        'model':name,'fold':fold_number,'source_row_index':int(source_idx),
                        'source_row_sha256':canonical_sha({'source_sha256':row['source_sha256'],
                                                          'row_position':int(source_idx)}),
                        'series_id':entity,'event_time_utc':when.isoformat() if when is not None else None,
                        'decision_time_utc':decision.iloc[source_idx].isoformat() if decision is not None else None,
                        'label_mature_utc':label_available.iloc[source_idx].isoformat() if label_available is not None else None,
                        'month':when.strftime('%Y-%m') if when is not None else None,
                        'actual':truth,'prediction':predicted,'residual':residual,'absolute_error':abs(residual),
                        'predicted_label':int(predicted>=.5) if classification else None,
                        'actual_label':int(truth) if classification else None})
                if len(prediction_rows)>1_000_000:
                    raise ModelTaskError('Validation prediction asset exceeds the 1,000,000-row bound')
                if dates is not None:
                    months=dates.iloc[va].dt.strftime('%Y-%m').tolist()
                    for idx,value in enumerate(months):
                        slice_buffer.append({'model':name,'dimension':'month','value':value,
                             'fold':fold_number,'y':float(y[va[idx]]),'prediction':float(prediction[idx])})
                if spec.time.entity_columns:
                    group_names=frame[spec.time.entity_columns].iloc[va].astype(str).agg('|'.join,axis=1).tolist()
                    for idx,value in enumerate(group_names):
                        slice_buffer.append({'model':name,'dimension':'device','value':value,
                             'fold':fold_number,'y':float(y[va[idx]]),'prediction':float(prediction[idx])})
            last_contributions=importance_by_family
        if not manifests:
            raise ModelTaskError('No valid validation folds produced')
        summary={name:{metric:finite(sum(v[metric] for v in rows if v[metric] is not None)
                                   /len([v for v in rows if v[metric] is not None]))
                        if any(v[metric] is not None for v in rows) else None
                       for metric in rows[0]} for name,rows in scores.items()}
        primary='average_precision' if classification else 'mae'
        candidates=[(name,score[primary]) for name,score in summary.items()
                    if score[primary] is not None]
        if not candidates:
            raise ModelTaskError('Primary metric undefined for all model candidates')
        selected=(max(candidates,key=lambda p:p[1])[0] if classification else
                  min(candidates,key=lambda p:p[1])[0])
        if selected=='baseline':
            contributions={'status':'not_available_for_constant_baseline',
                           'primary_metric':primary,'items':[]}
        else:
            contributions=last_contributions[selected]
        slices=aggregate_slices(slice_buffer,classification)
        protocol={'source_sha256':row['source_sha256'],'spec_sha256':row['spec_sha256'],
                  'split_kind':split,'folds':[{'train_index_sha256':f['train_index_sha256'],
                     'validation_index_sha256':f['validation_index_sha256']} for f in manifests],
                  'budget_sha256':canonical_sha(budget),'seed':42}
        fair={'same_source':True,'same_validation_rows_and_fold_indices':True,
              'same_soft_resource_envelope':True,'same_enforced_hard_limits':True,
              'equal_realized_compute':False,
              'protocol_sha256':canonical_sha(protocol),
              'scope':'paired same-split retrospective comparison; not a compute-normalized leaderboard'}
        from .model_tabular_lineage import SCHEMA as TABULAR_LINEAGE_SCHEMA, rowset_sha256
        result={'result_schema':'2.0','engine_version':ENGINE_VERSION,
                'source_lineage_schema':TABULAR_LINEAGE_SCHEMA,
                'source_rowset_sha256':rowset_sha256(frame),
                'task_id':row['task_id'],'run_id':row['run_id'],'task_revision':row['revision'],
                'spec_sha256':row['spec_sha256'],'source_sha256':row['source_sha256'],
                'native_sha256':spec.asset.native_sha256,'data_version':spec.version_id,
                'task_kind':spec.task_kind,'target':spec.target.column,'features':cols,
                'rows':len(frame),'folds':manifests,'fold_scores':scores,'summary':summary,
                'library_versions':{'numpy':np.__version__, 'pandas':pd.__version__,
                                    'scikit_learn':sklearn.__version__},
                'primary_metric':primary,'best_observed_model':selected,
                'metric_scope':'unweighted_mean_of_validation_folds',
                'point_in_time_check':('decision_time_publication_and_label_maturity' if time_based
                                       else 'feature_availability_only_no_fold_cutoff' if ledger
                                       else 'not_time_based'),
                'availability_ledger':ledger,
                'comparison_protocol':fair,'budget':budget,'fit_seconds_per_fold':fit_cost,
                'fold_diagnostics':fold_diagnostics,'slice_metrics':slices,
                'feature_contribution':contributions,
                'comparison_scope':'baseline_and_two_fixed_sklearn_families_on_identical_splits',
                'fold_evidence_contract':'model-fold-evidence-v2',
                'experiment_plan_sha256':(self.experiments.detail(
                     row['project_id'],row['task_id'],row['run_id'])['plan_sha256'] if guard else None),
                'prediction_asset_format':'validation-predictions-v1',
                'business_benefit_proven':False,
                'warnings':['Validation scores are retrospective estimates, not deployed business impact.',
                    'Hard wall/CPU/RSS caps do not imply equal actual CPU time or FLOPs.',
                    'Validation predictions are privately versioned; no serialized model object is exposed.']}
        result['_prediction_rows']=prediction_rows
        later=self.tasks.get(row['project_id'],row['task_id'])
        if (later['source_state']!='current' or later['revision']!=row['revision']
                or later['approval_sha256']!=row['approval_sha256']):
            raise ModelTaskConflict('Source or task changed during training')
        return result
