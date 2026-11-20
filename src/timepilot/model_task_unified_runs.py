"""v0.39.6 source-pinned ModelRun projection with signed fold and row evidence.

Existing tabular/temporal trainers remain authoritative. This layer never
reconstructs scores or publishes a model: it verifies approvals, carries a
cross-process execution lock and records a tamper-evident *reference* to the
underlying run. Results are read live and revalidated before display.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from .model_task_spec import Strict, ModelTaskSpec, TEMPORAL_KINDS, canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError
from .modeling_workflow import ApproveModelingDraft
from .project_migrations import catalog_startup_lock
from .model_task_time_audit import audit_availability
from .model_task_diagnostics import metrics as validation_metrics,diagnostics as validation_diagnostics,aggregate_slices
from .model_prediction_assets import ModelPredictionAssets


class StartUnifiedRun(Strict):
    expected_revision: int = Field(ge=1)
    # Explicit confirmation is required before propagating approval to the
    # legacy temporal runner; it must never happen on a read or silent retry.
    confirm_temporal_execution: bool = False
    budget_profile: Literal['5m','15m','30m'] = '15m'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelTaskUnifiedRuns:
    """One public execution/evidence contract for all four model families."""

    SCHEMA = 'model-run-v1'

    def __init__(self, tasks, tabular, temporal):
        self.tasks, self.tabular, self.temporal = tasks, tabular, temporal
        self.modeling, self.catalog = temporal.modeling, tasks.catalog
        self.predictions = tabular.predictions
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_execution_receipts (
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                project_id TEXT NOT NULL REFERENCES projects(id),
                revision INTEGER NOT NULL,
                executor TEXT NOT NULL,
                run_id TEXT NOT NULL,
                spec_sha256 TEXT NOT NULL,
                approval_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                submission_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (task_id,revision,executor))''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_task_execution_history
                ON model_task_execution_receipts(project_id,task_id,created_at)''')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_time_audits (
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                project_id TEXT NOT NULL REFERENCES projects(id),
                revision INTEGER NOT NULL, spec_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, ledger_json TEXT NOT NULL,
                ledger_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(task_id,revision))''')

    @staticmethod
    def _uuid(value):
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ModelTaskError('Invalid model run identifier') from None

    def _lock_file(self, pid, tid) -> Path:
        root = getattr(self.catalog, 'root', None)
        if root is None:
            root = Path(self.catalog.db_file).parent
        key = hashlib.sha256(f'{pid}:{tid}'.encode('utf-8')).hexdigest()
        return Path(root) / '.model-task-run-locks' / (key + '.lock')

    @staticmethod
    def _is_approved(task):
        return (task['source_state'] == 'current' and task['status'] == 'approved'
                and bool(task.get('approval_sha256')))

    def _verify(self, pid, tid, expected_revision):
        task = self.tasks.get(pid, tid)
        if task['revision'] != expected_revision:
            raise ModelTaskConflict('Model task revision changed; refresh before running')
        if not self._is_approved(task):
            raise ModelTaskConflict('Approve the current source-pinned model task before running')
        return task

    def _record(self, pid, tid, task, executor, run_id):
        run_id = self._uuid(run_id)
        digest = canonical_sha({'task_id':tid,'revision':task['revision'],
                                'spec_sha256':task['spec_sha256'],
                                'approval_sha256':task['approval_sha256'],
                                'source_sha256':task['spec']['asset']['source_sha256'],
                                'executor':executor,'run_id':run_id})
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.tasks._row(db, pid, tid)
            if (row['revision'] != task['revision'] or row['status'] != 'approved'
                    or row['spec_sha256'] != task['spec_sha256']
                    or row['approval_sha256'] != task['approval_sha256']):
                raise ModelTaskConflict('Task changed before execution receipt was committed')
            existing = db.execute('''SELECT run_id,submission_sha256 FROM model_task_execution_receipts
                WHERE project_id=? AND task_id=? AND revision=? AND executor=?''',
                (pid,tid,task['revision'],executor)).fetchone()
            if existing and (existing['run_id'] != run_id or existing['submission_sha256'] != digest):
                raise ModelTaskConflict('Conflicting model run receipt; manual reconciliation required')
            if not existing:
                db.execute('''INSERT INTO model_task_execution_receipts
                    (task_id,project_id,revision,executor,run_id,spec_sha256,
                     approval_sha256,source_sha256,submission_sha256,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (tid,pid,task['revision'],executor,run_id,task['spec_sha256'],
                     task['approval_sha256'],task['spec']['asset']['source_sha256'],digest,_now()))

    def _temporal_source_audit(self, pid, tid, task, spec):
        """Audit the full source BEFORE the legacy executor materializes anything.

        This does not falsely certify the original runner's hidden fold indices.
        An unchanged ledger is immutable across retries and checked on every read.
        """
        old=self._time_ledger(pid,tid,task,optional=True)
        if old is not None:
            return old
        source=self.modeling._source(pid,spec.asset_id,spec.version_id)
        if (source['original_data_sha256']!=spec.asset.source_sha256 or
                source['native_sha256']!=spec.asset.native_sha256):
            raise ModelTaskConflict('Temporal PIT audit source/version SHA changed')
        frame=self.tabular._read_rows(source,spec,max_rows=200000)
        ledger,_,_,_=audit_availability(frame,spec,require_labels=(spec.task_kind=='classification'))
        ledger['fold_training_maturity_certified']=False
        ledger['fold_evidence_status']='legacy_executor_does_not_publish_fold_indices'
        ledger['source_sha256']=spec.asset.source_sha256
        ledger['spec_sha256']=task['spec_sha256']
        ledger['ledger_sha256']=canonical_sha({k:v for k,v in ledger.items() if k!='ledger_sha256'})
        # A disk version swap or task revision during a long read invalidates it.
        recent=self.tasks.get(pid,tid)
        if (recent['source_state']!='current' or recent['revision']!=task['revision'] or
                recent['spec_sha256']!=task['spec_sha256']):
            raise ModelTaskConflict('Source or model task changed during PIT source audit')
        payload=json.dumps(ledger,ensure_ascii=False,sort_keys=True,allow_nan=False)
        digest=hashlib.sha256(payload.encode('utf-8')).hexdigest()
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""INSERT OR IGNORE INTO model_task_time_audits
                (task_id,project_id,revision,spec_sha256,source_sha256,
                 ledger_json,ledger_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (tid,pid,task['revision'],task['spec_sha256'],spec.asset.source_sha256,
                 payload,digest,_now()))
        return self._time_ledger(pid,tid,task)

    def _time_ledger(self,pid,tid,task,*,optional=False):
        with self.catalog._db() as db:
            row=db.execute("""SELECT * FROM model_task_time_audits
                WHERE task_id=? AND project_id=? AND revision=?""",
                (tid,pid,task['revision'])).fetchone()
        if row is None:
            if optional:return None
            raise ModelTaskError('Temporal source availability audit record is missing')
        if (row['spec_sha256']!=task['spec_sha256'] or
                row['source_sha256']!=task['spec']['asset']['source_sha256'] or
                hashlib.sha256(row['ledger_json'].encode('utf-8')).hexdigest()!=row['ledger_sha256']):
            raise ModelTaskError('Temporal PIT audit version or SHA256 failed verification')
        obj=json.loads(row['ledger_json'])
        digest=canonical_sha({k:v for k,v in obj.items() if k!='ledger_sha256'})
        if obj.get('ledger_sha256')!=digest:
            raise ModelTaskError('Temporal PIT audit canonical evidence digest mismatch')
        return obj

    def start(self, pid: str, task_id: str, request: StartUnifiedRun):
        pid = self.catalog._id(pid)
        self.catalog.writable(pid)
        tid = self.tasks._task_id(task_id)
        # A real file/OS lock guards the legacy queue/materialization across
        # API processes; it is automatically released on SIGKILL.
        try:
            with catalog_startup_lock(self._lock_file(pid,tid),timeout_seconds=30):
                task = self._verify(pid,tid,request.expected_revision)
                spec = ModelTaskSpec.model_validate(task['spec'])
                receipt = self._receipt(pid,tid,task['revision'])
                if receipt is not None:
                    # Immutable receipt: a new engine version cannot silently
                    # replace the outcome of an already approved task revision.
                    if (receipt['spec_sha256']!=task['spec_sha256'] or
                            receipt['approval_sha256']!=task['approval_sha256'] or
                            receipt['source_sha256']!=spec.asset.source_sha256):
                        raise ModelTaskConflict('Existing execution receipt is inconsistent with current approval')
                    return self.summary(pid,tid)
                if spec.task_kind not in TEMPORAL_KINDS:
                    result = self.tabular.submit(pid,tid,budget_profile=request.budget_profile)
                    executor,run_id = 'sklearn_tabular',result['run_id']
                else:
                    link = self.temporal.get(pid,tid)
                    if link['status'] not in ('linked','needs_approval'):
                        raise ModelTaskConflict('Temporal execution requires a valid linked PIT-aware draft')
                    draft_id = link['draft_id']
                    draft = self.modeling.draft(pid,draft_id)
                    # A link alone is not enough: repeat the full compatibility
                    # and source checks immediately before every external write.
                    self.temporal._verify(task,draft)
                    if not draft.get('approval') and not request.confirm_temporal_execution:
                        raise ModelTaskConflict('Confirm temporal execution before propagating approval')
                    # No durable audit row is written before explicit approval.
                    self._temporal_source_audit(pid,tid,task,spec)
                    if not draft.get('approval'):
                        self.modeling.approve(pid,draft_id,ApproveModelingDraft(
                            expected_revision=draft['revision'],confirm=True,
                            acknowledge_single_target=True,acknowledge_availability=True))
                    draft = self.modeling.draft(pid,draft_id)
                    self.temporal._verify(task,draft)
                    if not draft.get('dataset_id'):
                        self.modeling.materialize(pid,draft_id)
                    # Fail closed if a unified revision changed while preparing
                    # the expensive frozen legacy snapshot.
                    self._verify(pid,tid,request.expected_revision)
                    self.temporal._verify(task,self.modeling.draft(pid,draft_id))
                    result = self.modeling.submit(pid,draft_id,budget_profile=request.budget_profile)
                    executor,run_id = 'existing_temporal_pit',result['run_id']
                self._record(pid,tid,task,executor,run_id)
            return self.summary(pid,tid)
        except TimeoutError as exc:
            raise ModelTaskConflict('Model execution already in progress; refresh its status') from exc

    def preview(self,pid,tid,profile='15m'):
        task=self.tasks.get(pid,tid)
        spec=ModelTaskSpec.model_validate(task['spec'])
        if spec.task_kind not in TEMPORAL_KINDS:
            return self.tabular.preview(pid,tid,profile)
        plan=self.modeling.catalog.engine.budgets.plan(profile)
        return {'task_id':tid,'revision':task['revision'],
                'source_sha256':spec.asset.source_sha256,
                'spec_sha256':task['spec_sha256'],
                'experiment_plan':plan,'plan_sha256':canonical_sha(plan),
                'candidate_models':None,'declared_folds':spec.validation.folds,
                'budget_scope':'entire_temporal_run','enforcement':'external_process_tree_watchdog',
                'warnings':['The forecast series count multiplies actual fold/plugin invocations.',
                            'A time budget cannot guarantee all folds will finish.']}


    def steps(self,pid,tid):
        task=self.tasks.get(pid,tid,check_source=False)
        receipt=self._receipt(self.catalog._id(pid),self.tasks._task_id(tid),task['revision'])
        if receipt is None:raise ModelTaskConflict('No submitted run for the current task revision')
        if receipt['executor']=='sklearn_tabular':
            return self.tabular.steps(pid,tid,receipt['run_id'])
        legacy=self.modeling.catalog.engine
        return {'run_id':receipt['run_id'],'status':'temporal_attempt_fenced_stages',
            'items':legacy.budgets.steps(receipt['run_id']),
            'experiment':legacy.budgets.detail(receipt['run_id']),
            'events':legacy.events(receipt['run_id'],tail=80),
            'worker_logs':legacy.worker_logs(receipt['run_id']),
            'message':'Stages are attempt-fenced; model/plugin fold usage is in the signed stage artifacts.'}

    def cancel(self,pid,tid,expected_revision,run_id=None):
        task=self.tasks.get(pid,tid,check_source=False)
        if task['revision']!=expected_revision:
            raise ModelTaskConflict('Task revision changed before cancellation')
        receipt=self._receipt(self.catalog._id(pid),self.tasks._task_id(tid),task['revision'])
        if receipt is None:raise ModelTaskConflict('No submitted run for the current task revision')
        if run_id is not None and receipt['run_id']!=run_id:
            raise ModelTaskConflict('Requested run ID does not match the project-owned execution receipt')
        if receipt['executor']=='sklearn_tabular':
            return self.tabular.cancel(pid,tid,receipt['run_id'])
        outcome=self.modeling.catalog.engine.cancel(receipt['run_id'])
        return {'run_id':receipt['run_id'],'state':str(outcome),
                'cancel_accepted':str(outcome) in ('cancel_requested','cancelled')}

    def _receipt(self,pid,tid,revision=None):
        with self.catalog._db() as db:
            if revision is None:
                row = db.execute('''SELECT * FROM model_task_execution_receipts
                    WHERE project_id=? AND task_id=? ORDER BY revision DESC LIMIT 1''',
                    (pid,tid)).fetchone()
            else:
                row = db.execute('''SELECT * FROM model_task_execution_receipts
                    WHERE project_id=? AND task_id=? AND revision=?''',
                    (pid,tid,revision)).fetchone()
        if row is None:
            return None
        receipt = dict(row)
        expected = canonical_sha({k:receipt[k] for k in (
            'task_id','revision','spec_sha256','approval_sha256','source_sha256','executor','run_id')})
        if expected != receipt['submission_sha256']:
            raise ModelTaskError('Model execution receipt SHA256 mismatch')
        return receipt

    def history(self,pid,task_id,limit=30):
        pid = self.catalog._id(pid)
        tid = self.tasks._task_id(task_id)
        doc = self.tasks.get(pid,tid)
        with self.catalog._db() as db:
            rows = db.execute('''SELECT revision FROM model_task_execution_receipts
                WHERE project_id=? AND task_id=? ORDER BY revision DESC LIMIT ?''',
                (self.catalog._id(pid),tid,max(1,min(int(limit),100)))).fetchall()
        items = []
        for r in rows:
            entry = self._receipt(pid,tid,r['revision'])
            entry['current'] = bool(doc.get('source_state')=='current'
                and doc['revision']==entry['revision']
                and doc['status']=='approved'
                and doc['spec_sha256']==entry['spec_sha256']
                and doc['approval_sha256']==entry['approval_sha256'])
            items.append(entry)
        return {'items':items}

    def _temporal_fold_result(self,pid,tid,task,run_id):
        """Read only an owned, hashed successful temporal stage and prove same folds.

        Version-0.39.5 runs lacking the stage-level contract remain aggregate-only.
        No performance claim or synthetic fold metric is inferred from test scores.
        """
        import collections
        from .storage import load_json,sha256_file
        engine=self.modeling.catalog.engine
        row=engine.store.run(run_id)
        if row is None or str(getattr(row.status,'value',row.status))!='succeeded':
            raise ModelTaskConflict('Temporal run is no longer successful')
        link=self.temporal.get(pid,tid)
        draft=self.modeling.draft(pid,link['draft_id'])
        if draft.get('run_id')!=run_id or draft.get('dataset_id')!=row.dataset_id:
            raise ModelTaskConflict('Temporal run dataset or draft link changed')
        root=engine.run_dir(run_id)
        stage='classification' if task['spec']['task_kind']=='classification' else 'backtest'
        receipt=root/'stages'/(stage+'.json')
        if not receipt.is_file():return None
        meta=load_json(receipt)
        if meta.get('stage')!=stage or meta.get('artifact')!='stages/'+stage+'_result.json':
            raise ModelTaskError('Unexpected temporal fold stage receipt')
        artifact=root/meta['artifact']
        if not artifact.is_file() or sha256_file(artifact)!=meta.get('sha256'):
            raise ModelTaskError('Temporal fold stage SHA256 mismatch')
        content=load_json(artifact)
        folds=content.get('folds');scores=content.get('fold_scores')
        records=content.get('validation_predictions')
        protocol=content.get('comparison_protocol')
        if not all(isinstance(x,(dict,list)) for x in (folds,scores,records,protocol)):
            return None
        if not folds or not scores or not records or len(records)>1_000_000:
            raise ModelTaskError('Temporal fold result is empty or above the prediction limit')
        names=list(scores)
        legacy_task=json.loads(row.task_json)
        if not names or sorted(names)!=sorted(legacy_task.get('model_candidates',[])):
            raise ModelTaskError('Temporal fold candidates differ from approved legacy run')
        declared=int(task['spec']['validation']['folds'])
        if set(x.get('fold') for x in folds)!=set(range(1,declared+1)):
            raise ModelTaskError('Temporal fold identities do not match the approved count')
        if any(not x.get('training_labels_mature_at_cutoff') or not x.get('shared_by_all_models')
               or not x.get('train_index_sha256') or not x.get('validation_index_sha256')
               for x in folds):
            raise ModelTaskError('Temporal folds lack row or maturity proof')
        expected_n=sum(int(x['validation_rows']) for x in folds)
        if any(len(scores[name])!=len(folds) for name in names):
            raise ModelTaskError('Temporal comparator did not evaluate every shared fold')
        counts=collections.Counter(str(x.get('model')) for x in records)
        if any(counts[name]!=expected_n for name in names) or sum(counts.values())!=expected_n*len(names):
            raise ModelTaskError('Temporal validation prediction count differs from fold evidence')
        # Rebuild each fold's validation row index digest directly from its
        # published sample-level predictions, once per candidate. A corrupted
        # row mapping cannot masquerade as the correct fold with a new CSV.
        for fold in folds:
            for name in names:
                selected=[x for x in records if x['model']==name and x['fold']==fold['fold']
                    and (task['spec']['task_kind']=='classification' or
                         x['series_id']==fold['series_id'])]
                if len(selected)!=fold['validation_rows']:
                    raise ModelTaskError('Fold prediction rows differ from signed validation size')
                ordered=sorted(selected,key=lambda x:int(x['source_row_index']))
                if canonical_sha([x['source_row_sha256'] for x in ordered])!=fold['validation_index_sha256']:
                    raise ModelTaskError('Predicted materialized rows do not match signed fold indexes')
        if protocol.get('same_validation_rows_and_fold_indices') is not True:
            raise ModelTaskError('Temporal pairwise fold contract was not certified')
        declared_split=task['spec']['validation']['kind']
        signed_split=protocol.get('split_kind')
        # v0.39.6 signed rolling-origin stages recorded the protocol in every
        # fold but omitted it from comparison_protocol. Honor those older
        # already-hashed folds only when ALL signed fold markers agree.
        if signed_split is None and folds and all(f.get('split')=='rolling_origin' for f in folds):
            signed_split='rolling_origin'
        if signed_split != declared_split or any(f.get('split')!=signed_split for f in folds):
            raise ModelTaskError('Temporal fold validation protocol differs from approved task')
        if declared_split=='leave_device_out':
            for fold in folds:
                samples=[x['series_id'] for x in records
                         if x['model']==names[0] and x['fold']==fold['fold']]
                hashed=canonical_sha(sorted(set(map(str,samples))))
                if (fold.get('device_disjoint') is not True or
                    fold.get('heldout_device_sha256')!=hashed or
                    not fold.get('train_device_sha256') or
                    fold.get('train_device_sha256')==hashed):
                    raise ModelTaskError('Device OOD held-out device evidence differs from prediction rows')
        return {'folds':folds,'fold_scores':scores,'validation_predictions':records,
                'comparison_protocol':protocol,'fit_seconds_per_fold':content.get('fit_seconds_per_fold',{}),
                'stage_sha256':meta['sha256'],'candidates':names}

    def _publish_temporal_asset(self,pid,tid,task,run_id,result_sha,content):
        """Idempotent recovery of a completed, stage-signed artifact.

        The older runner owns execution and checkpoint writes. The unified
        projection owns a separate immutable derived prediction asset.
        """
        return self.predictions.publish(pid,tid,task,run_id,'existing_temporal_pit',
                                        result_sha,content['validation_predictions'])

    def _temporal_diagnostics(self,pid,tid,out,common,model,dimension,value):
        content=self._temporal_fold_result(pid,tid,self.tasks.get(pid,tid),out['run_id'])
        if content is None:
            aggregate=out['metric_sets'].get('test') or {}
            confusion=({k:int(aggregate[k]) for k in ('tn','fp','fn','tp')}
                if out['task_kind']=='classification' and all(
                    k in aggregate and type(aggregate[k]) in (int,float) and
                    aggregate[k]>=0 and float(aggregate[k]).is_integer()
                    for k in ('tn','fp','fn','tp')) else None)
            return {**common,'status':'legacy_aggregate_only',
                'reason':'This historical temporal run predates the signed fold evidence contract',
                'aggregate_confusion_matrix':confusion,
                'aggregate_confusion_provenance':'existing_executor_test_metrics' if confusion else None,
                'fold_scores':[],'fold_diagnostics':[],'feature_contribution':None,
                'available_dimensions':[],'slices':[],'budget':None}
        if model not in content['candidates']:
            raise ModelTaskError('Unknown temporal comparator; available: '+', '.join(content['candidates']))
        import numpy as np
        classification=out['task_kind']=='classification'
        records=[x for x in content['validation_predictions'] if x['model']==model]
        folds=[]
        for fold in content['folds']:
            group=[x for x in records if x['fold']==fold['fold'] and
                (classification or x['series_id']==fold['series_id'])]
            if len(group)!=fold['validation_rows']:
                raise ModelTaskError('Temporal diagnostic rows do not match signed fold indices')
            y=np.asarray([x['actual'] for x in group],dtype=float)
            pred=np.asarray([x['prediction'] for x in group],dtype=float)
            folds.append({'fold':fold['fold'],'series_id':fold['series_id'],
                          **validation_diagnostics(y,pred,classification)})
        slices=[]
        for x in records:
            for dim,val in (('device',x.get('series_id')),('month',x.get('month'))):
                if val is not None:slices.append({'model':model,'dimension':dim,'value':str(val),
                    'fold':x['fold'],'y':x['actual'],'prediction':x['prediction']})
        grouped=aggregate_slices(slices,classification).get(model,{})
        possibilities=grouped.get(dimension,[]) if dimension else []
        selected=[x for x in possibilities if x['value']==value] if value is not None else possibilities
        return {**common,'status':'available','fold_scores':content['fold_scores'][model],
            'fold_diagnostics':folds,'feature_contribution':None,
            'feature_contribution_reason':'Temporal plugin contribution has not been estimated on held-out folds',
            'available_models':content['candidates'],'available_dimensions':sorted(grouped),
            'slices':selected,'slice_min_rows':15,
            'budget':{'enforcement':'kill_process_tree','scope':'per_plugin_per_fold',
                      'wall_seconds':min(180,self._legacy_budget_seconds(out['run_id'])),
                      'cpu_seconds':min(180,self._legacy_budget_seconds(out['run_id'])),'rss_bytes':2*1024**3},
            'fit_seconds':content.get('fit_seconds_per_fold',{}).get(model,[]),
            'comparison_protocol':content['comparison_protocol'],
            'slice_notes':'Only independent retrospective validation predictions; filters never refit models.'}

    def _legacy_budget_seconds(self,run_id):
        row=self.modeling.catalog.engine.store.run(run_id)
        return float(json.loads(row.task_json).get('training_budget_seconds',180))

    def summary(self,pid,task_id) -> dict:
        pid = self.catalog._id(pid)
        task = self.tasks.get(pid,task_id)
        tid = self.tasks._task_id(task_id)
        spec = ModelTaskSpec.model_validate(task['spec'])
        source_is_current = task.get('source_state') == 'current'
        approved = self._is_approved(task)
        executor = 'existing_temporal_pit' if spec.task_kind in TEMPORAL_KINDS else 'sklearn_tabular'
        state = ('stale' if not source_is_current else
                 'needs_review' if task['status']=='review_required' else
                 'needs_approval' if not approved else 'ready_to_run')
        out = {'schema_version':self.SCHEMA,'task_id':tid,'task_kind':spec.task_kind,
               'revision':task['revision'],'spec_sha256':task['spec_sha256'],
               'source_sha256':spec.asset.source_sha256,'native_sha256':spec.asset.native_sha256,
               'data_version':spec.version_id,'target':spec.target.column,
               'feature_count':len(spec.features),'executor':executor,'state':state,
               'run_id':None,'current':source_is_current,
               'validation_protocol':spec.validation.kind,'declared_folds':spec.validation.folds,
               'folds':[],'metric_sets':{},'result_sha256':None,
               'prediction_asset':None,'fold_label_maturity_certified':False,
               'availability_ledger':None,'comparison_protocol':None,
               'metric_provenance':None,'failure_code':None,'failure_reason':None,
               'diagnostics_available':False,
               'report_available':False,'business_benefit_proven':False}
        # Old runs can always be audited, but never resurrected as current.
        if not source_is_current:
            out['evidence_sha256'] = canonical_sha(out)
            return out
        evidence = self.temporal.evidence(pid,tid)
        if executor == 'sklearn_tabular':
            out['state'] = (state if not approved else 'stale') if evidence['status']=='superseded' else (
                'ready_to_run' if evidence['status']=='not_submitted' and approved else
                evidence['status'] if evidence['status']!='not_submitted' else state)
            out['run_id'] = evidence.get('run_id')
            if evidence['status']=='succeeded' and approved:
                out['metric_sets']={'comparison':evidence.get('metrics') or {}}
                out['folds'] = evidence.get('folds') or []
                raw = self.tabular.get(pid,out['run_id'])
                out['experiment']=raw.get('experiment')
                out['result_sha256'] = raw['result_sha256']
                out['validation_protocol'] = evidence.get('validation_protocol') or spec.validation.kind
                result=raw.get('result') or {}
                out['availability_ledger']=result.get('availability_ledger')
                out['comparison_protocol']=result.get('comparison_protocol')
                out['metric_provenance']={'run_id':raw['run_id'],
                    'source_sha256':spec.asset.source_sha256,
                    'result_sha256':raw['result_sha256'],
                    'metric_scope':result.get('metric_scope','unknown_legacy'),
                    'folds':len(out['folds']), 'engine_version':raw['engine_version']}
                out['diagnostics_available']=bool(result.get('fold_diagnostics'))
                out['prediction_asset']=raw.get('prediction_asset')
        else:
            if evidence['status']=='unlinked':
                out['state']='needs_link'
            elif evidence['status']=='needs_approval':
                out['state']='needs_approval'
            elif evidence['status']=='stale':
                out['state']='stale'
            elif evidence['status']=='not_submitted':
                out['state']='ready_to_run' if approved else 'needs_approval'
            else:
                out['state']=evidence['status']
            out['run_id'] = evidence.get('run_id')
            if out['state']=='succeeded' and approved:
                out['metric_sets']={'test':evidence.get('metrics') or {},
                                    'validation':evidence.get('validation_metrics') or {}}
                out['result_sha256'] = evidence.get('evidence_sha256')
                out['validation_protocol']=evidence.get('validation_protocol') or spec.validation.kind
                out['quality_status']=evidence.get('quality_status')
                out['metric_provenance']={'run_id':out['run_id'],
                    'source_sha256':spec.asset.source_sha256,
                    'result_sha256':out['result_sha256'],
                    'metric_scope':'existing_executor_independent_test_and_validation',
                    'folds':0, 'engine_version':'existing_temporal_pit'}
                checked=self._temporal_fold_result(pid,tid,task,out['run_id'])
                if checked is not None:
                    out['folds']=checked['folds']
                    out['fold_label_maturity_certified']=True
                    out['comparison_protocol']=checked['comparison_protocol']
                    out['validation_protocol']=checked['comparison_protocol']['split_kind']
                    if spec.validation.kind=='leave_device_out':
                        out['independent_test_scope']='chronological_mixed_devices_not_device_ood'
                    out['metric_provenance']['folds']=len(checked['folds'])
                    out['metric_provenance']['fold_stage_sha256']=checked['stage_sha256']
                    out['metric_provenance']['metric_scope']='same_fold_temporal_validation_and_independent_test'
                    out['diagnostics_available']=True
                    out['available_models']=checked['candidates']
                    out['prediction_asset']=self._publish_temporal_asset(pid,tid,task,
                        out['run_id'],out['result_sha256'],checked)
                    from collections import defaultdict
                    import numpy as np
                    grouped=defaultdict(list)
                    for row in checked['validation_predictions']:
                        grouped[row['model']].append(row)
                    is_classification=spec.task_kind=='classification'
                    out['metric_sets']['comparison']={
                        model:validation_metrics(
                            np.asarray([item['actual'] for item in rows],dtype=float),
                            np.asarray([item['prediction'] for item in rows],dtype=float),
                            is_classification)
                        for model,rows in grouped.items()}
        if executor=='existing_temporal_pit' and approved:
            out['availability_ledger']=self._time_ledger(pid,tid,task,optional=True)
            if out.get('run_id'):
                out['experiment']=self.modeling.catalog.engine.budgets.detail(out['run_id'])
        if executor=='sklearn_tabular' and out.get('run_id') and approved:
            controlled=self.tabular.get(pid,out['run_id'])
            out['experiment']=controlled.get('experiment')
            if controlled.get('effective_state') in ('cancelled','cancel_requested'):
                out['state']=controlled['effective_state']
        if executor=='sklearn_tabular' and evidence['status']=='failed' and out['run_id']:
            failed=self.tabular.get(pid,out['run_id'])
            out['failure_code']=failed.get('error_code')
            out['failure_reason']=failed.get('error_message')
        if executor=='existing_temporal_pit' and out['state']=='failed':
            run=self.modeling.catalog.engine.store.run(out['run_id']) if out['run_id'] else None
            out['failure_code']=getattr(run,'error_code',None) or 'ExistingTemporalExecutorFailed'
            out['failure_reason']=(getattr(run,'error_message',None) or
                'Temporal runner reported failure; inspect the linked run logs')[:1000]
        receipt = self._receipt(self.catalog._id(pid),tid,task['revision'])
        if receipt:
            if (receipt['spec_sha256'] != task['spec_sha256'] or
                receipt['approval_sha256'] != task['approval_sha256'] or
                receipt['source_sha256'] != spec.asset.source_sha256 or
                receipt['executor'] != executor or
                (out['run_id'] and out['run_id'] != receipt['run_id'])):
                if approved:
                    out['state']='stale'
                out['historical_receipt_stale']=True
                out['metric_sets']={}
                out['folds']=[]
                out['result_sha256']=None
                out['metric_provenance']=None
                out['diagnostics_available']=False
                out['prediction_asset']=None
            else:
                out['run_id'] = receipt['run_id']
                out['submission_sha256'] = receipt['submission_sha256']
        # Version/revision changes or absent approvals always hide results.
        if not approved or out['state'] in ('stale','needs_review','needs_approval','needs_link'):
            out['metric_sets']={}
            out['folds']=[]
            out['result_sha256']=None
            out['metric_provenance']=None
            out['diagnostics_available']=False
            out['availability_ledger']=None
            out['prediction_asset']=None
        out['report_available'] = out['state']=='succeeded' and bool(out['result_sha256'])
        if out.get('prediction_asset') and out['prediction_asset']['result_sha256']!=out['result_sha256']:
            raise ModelTaskError('Prediction dataset points to a different result checksum')
        # A report/evidence digest must be stable across repeated reads. The
        # live countdown is presentation state, not part of scientific evidence.
        # Frozen budget, deadlines, state and signed model results remain covered.
        immutable_evidence = dict(out)
        if isinstance(out.get('experiment'), dict):
            immutable_experiment = dict(out['experiment'])
            immutable_experiment.pop('remaining_wall_seconds', None)
            immutable_evidence['experiment'] = immutable_experiment
        out['evidence_sha256'] = canonical_sha(immutable_evidence)
        return out

    def prediction_rows(self,pid,task_id,*,model=None,fold=None,offset=0,limit=100):
        scope=self.summary(pid,task_id)
        if not scope.get('report_available') or not scope.get('prediction_asset'):
            raise ModelTaskConflict('The current approved model run has no signed prediction dataset')
        if model is not None and (not isinstance(model,str) or not model.replace('_','').isalnum() or len(model)>100):
            raise ModelTaskError('Invalid prediction model filter')
        if fold is not None and not 1<=int(fold)<=1000:
            raise ModelTaskError('Invalid prediction fold filter')
        return self.predictions.rows(pid,scope['task_id'],scope['run_id'],
            offset=int(offset),limit=int(limit),model=model,fold=int(fold) if fold else None)

    def prediction_query(self,pid,task_id,*,model=None,fold=None,month=None,
                         device=None,min_abs_error=None,offset=0,limit=100):
        from .model_prediction_query import query
        scope=self.summary(pid,task_id)
        if not scope.get('report_available') or not scope.get('prediction_asset'):
            raise ModelTaskConflict('Query requires a current approved, signed prediction asset')
        return query(self.predictions,pid,scope['task_id'],scope['run_id'],
            model=model,fold=fold,month=month,device=device,
            min_abs_error=min_abs_error,offset=offset,limit=limit)

    def prediction_lineage(self,pid,task_id,*,row_id,source_offset=0,source_limit=20):
        """Project-scoped prediction -> selected BI source rows (v0.40.2).

        Previous runs and tabular runners do not gain fabricated row lineage.
        A full content checksum, immutable source approval and the frozen
        temporal dataset snapshot must all agree before returning source ids.
        """
        from .model_source_lineage import read as read_lineage
        from .data import get_dataset
        from .storage import load_json
        if type(row_id) is not int or not 0<=row_id<1_000_000:
            raise ModelTaskError('Invalid bounded prediction row id')
        if (type(source_offset) is not int or type(source_limit) is not int or
                not 0<=source_offset<=200000 or not 1<=source_limit<=20):
            raise ModelTaskError('Invalid bounded source lineage page')
        scope=self.summary(pid,task_id)
        if not scope.get('report_available') or not scope.get('prediction_asset'):
            raise ModelTaskConflict('Lineage requires a current, signed prediction run')
        one=self.predictions.rows(pid,scope['task_id'],scope['run_id'],offset=row_id,limit=1)
        if len(one['items'])!=1 or int(one['items'][0]['__tp_rowid'])!=row_id:
            raise KeyError('Prediction row does not exist')
        prediction=one['items'][0]
        identity={'prediction_row_id':row_id,'model':prediction['model'],
                  'fold':int(prediction['fold']),'event_time_utc':prediction['event_time_utc'],
                  'prediction_source_row_sha256':prediction['source_row_sha256'],
                  'run_id':scope['run_id'],'prediction_asset_sha256':scope['prediction_asset']['manifest_sha256']}
        if scope.get('executor')!='existing_temporal_pit':
            from .model_tabular_lineage import resolve
            source=self.modeling._source(pid,self.tasks.get(pid,scope['task_id'])['spec']['asset']['asset_id'],
                scope['data_version'])
            # The project-approved task binds source/version; the signed run
            # additionally fixes the exact ordered frame seen by sklearn.
            tabular_run=self.tabular.get(pid,scope['run_id'])
            if tabular_run['effective_state']!='succeeded' or not tabular_run['result']:
                raise ModelTaskConflict('Tabular result is no longer current')
            spec=ModelTaskSpec.model_validate(self.tasks.get(pid,scope['task_id'])['spec'])
            frame=self.tabular._read_rows(source,spec)
            return {**identity,**resolve(frame=frame,prediction=prediction,
                    result=tabular_run['result'],source=source,spec=spec)}
        link=self.temporal.get(pid,scope['task_id'])
        doc=self.modeling.draft(pid,link['draft_id'])
        manifest_meta=doc.get('materialization') or {}
        if not manifest_meta.get('lineage_sha256'):
            return {**identity,'status':'legacy_lineage_not_recorded',
                    'reason':'The historical materialization did not publish row-level lineage'}
        legacy=self.modeling.catalog.engine.store.run(scope['run_id'])
        if (legacy is None or legacy.dataset_id!=doc.get('dataset_id') or
                doc.get('run_id')!=scope['run_id']):
            raise ModelTaskConflict('Temporal run and frozen dataset have diverged')
        dataset,normalized=get_dataset(self.modeling.catalog.engine.store,
            self.modeling.catalog.engine.settings,doc['dataset_id'])
        if (dataset.snapshot_sha256!=manifest_meta['dataset_snapshot_sha256'] or
                dataset.source_sha256!=manifest_meta['selected_input_sha256'] or
                doc['version_id']!=scope['data_version']):
            raise ModelTaskConflict('Prediction lineage no longer matches the frozen dataset')
        folder=self.modeling.catalog.engine.settings.home/'datasets'/doc['dataset_id']
        saved=load_json(folder/'modeling_provenance.json')
        if (saved.get('lineage_sha256')!=manifest_meta['lineage_sha256'] or
                saved.get('lineage_scope')!=manifest_meta['lineage_scope'] or
                saved.get('dataset_snapshot_sha256')!=dataset.snapshot_sha256):
            raise ModelTaskError('Lineage provenance and draft materialization disagree')
        try:
            matched=read_lineage(folder,expected_sha256=manifest_meta['lineage_sha256'],
                expected_scope=manifest_meta['lineage_scope'],expected_rows=len(normalized),
                source_fingerprint=prediction['source_row_sha256'])
        except (OSError,ValueError,KeyError) as exc:
            raise ModelTaskError('Prediction lineage verification failed: '+str(exc)[:180]) from exc
        if matched['status']=='traced_to_selected_snapshot':
            import hashlib
            import pandas as pd
            from .model_task_spec import canonical_sha
            # Resolve actual source columns only through the same approved
            # project/version/selection authorization used for materialization.
            selected,total=self.modeling._read_selected(doc)
            if selected is None or total>200000:
                raise ModelTaskError('Source selection exceeds the lineage lookup budget')
            raw_bytes=selected.to_csv(index=False).encode('utf-8')
            if hashlib.sha256(raw_bytes).hexdigest()!=manifest_meta['selected_input_sha256']:
                raise ModelTaskConflict('Selected source rowset changed since materialization')
            scope_rows=matched.pop('selected_source_rows')
            matched['selected_source_rows']=scope_rows[source_offset:source_offset+source_limit]
            preview=[]
            for ref in scope_rows[source_offset:source_offset+source_limit]:
                pos=ref['selected_row_position']
                if not 0<=pos<len(selected):
                    raise ModelTaskError('Lineage source index outside signed rowset')
                raw=selected.iloc[pos].to_dict()
                values={str(k):(None if pd.isna(v) else str(v)) for k,v in raw.items()}
                if canonical_sha(values)!=ref['selected_row_sha256']:
                    raise ModelTaskError('Lineage source content SHA256 mismatch')
                preview.append({'selected_row_position':pos,'selected_row_id':ref['selected_row_id'],
                                'values':values})
            matched['source_page']={'offset':source_offset,'limit':source_limit,
                                     'total':len(scope_rows),'items':preview,
                                     'next_offset':source_offset+len(preview)
                                       if source_offset+len(preview)<len(scope_rows) else None}
            # Source values are delivered only to an authorized project user;
            # they are never included in audit logs or model reports.
        return {**identity,**matched,'dataset_id':doc['dataset_id'],
                'dataset_snapshot_sha256':dataset.snapshot_sha256,
                'asset_id':doc['asset_id'],'source_version_id':doc['version_id']}

    def prediction_window_lineage(self,pid,task_id,*,row_id,offset=0,limit=100):
        """Certified forecast history membership, never inferred feature attribution.

        The candidate history is reconstructed from the frozen temporal dataset
        and MUST hash to the exact signed training fold. Classification plugin
        input frames are not uniquely indexed by its legacy fold contract.
        """
        from .model_source_lineage import read_window
        from .model_temporal_evidence import indices
        from .data import get_dataset
        from .storage import load_json
        import pandas as pd
        if type(row_id) is not int or not 0<=row_id<1_000_000:
            raise ModelTaskError('Invalid prediction row identity')
        if type(offset) is not int or type(limit) is not int or not 0<=offset<=200000 or not 1<=limit<=100:
            raise ModelTaskError('Invalid bounded history page')
        # Reuses the exact approval, source file SHA, signed prediction and
        # full selected source snapshot checks of the target-row lineage.
        target=self.prediction_lineage(pid,task_id,row_id=row_id,source_limit=1)
        if target.get('status')!='traced_to_selected_snapshot':
            return {'status':target.get('status'),'reason':target.get('reason'),
                    'prediction_row_id':row_id}
        scope=self.summary(pid,task_id)
        if scope['executor']!='existing_temporal_pit':
            return {'status':'not_a_temporal_forecast_window','prediction_row_id':row_id}
        if scope['task_kind']!='continuous_forecast':
            return {'status':'classification_history_membership_not_certified',
                    'reason':'Legacy classification folds do not publish unique historical input row indices',
                    'prediction_row_id':row_id}
        records=self.predictions.rows(pid,scope['task_id'],scope['run_id'],offset=row_id,limit=1)['items']
        record=records[0]
        task=self.tasks.get(pid,scope['task_id'])
        stages=self._temporal_fold_result(pid,scope['task_id'],task,scope['run_id'])
        if stages is None:
            return {'status':'legacy_fold_history_not_recorded','prediction_row_id':row_id}
        matching=[f for f in stages['folds'] if int(f['fold'])==int(record['fold'])
                  and str(f['series_id'])==str(record['series_id'])]
        if len(matching)!=1:
            raise ModelTaskError('Signed fold does not uniquely identify prediction series')
        fold=matching[0]
        link=self.temporal.get(pid,scope['task_id'])
        doc=self.modeling.draft(pid,link['draft_id'])
        meta=doc.get('materialization') or {}
        manifest,normalized=get_dataset(self.modeling.catalog.engine.store,
            self.modeling.catalog.engine.settings,doc['dataset_id'])
        if (not meta.get('lineage_sha256') or
                manifest.snapshot_sha256!=meta.get('dataset_snapshot_sha256') or
                manifest.source_sha256!=meta.get('selected_input_sha256')):
            raise ModelTaskConflict('Historical window lineage source version changed')
        validation_start=pd.Timestamp(fold['first_validation_event_utc'])
        group=normalized.loc[normalized['series_id'].astype(str)==str(record['series_id'])].copy()
        group=group.sort_values('timestamp')
        history=group.loc[pd.to_datetime(group['timestamp'],utc=True)<validation_start]
        expected_hash=canonical_sha(indices(history))
        if len(history)!=fold['train_rows'] or expected_hash!=fold['train_index_sha256']:
            raise ModelTaskError('Historical input rows differ from signed training fold')
        if record['source_row_sha256'] not in indices(group):
            raise ModelTaskError('Prediction target does not belong to the certified series')
        cutoff=pd.Timestamp(fold['cutoff_utc'])
        if (pd.to_datetime(history['available_at'],utc=True)>cutoff).any():
            raise ModelTaskError('Historical window includes a future-unavailable observation')
        folder=self.modeling.catalog.engine.settings.home/'datasets'/doc['dataset_id']
        saved=load_json(folder/'modeling_provenance.json')
        if (saved.get('lineage_sha256')!=meta['lineage_sha256'] or
                saved.get('lineage_scope')!=meta['lineage_scope'] or
                saved.get('dataset_snapshot_sha256')!=manifest.snapshot_sha256):
            raise ModelTaskError('Window source map is inconsistent with materialization proof')
        try:
            links=read_window(folder,expected_sha256=meta['lineage_sha256'],
                expected_scope=meta['lineage_scope'],expected_rows=len(normalized),
                normalized_frame=normalized,history_positions=[int(i) for i in history.index],
                offset=offset,limit=limit)
        except (OSError,ValueError,KeyError) as exc:
            raise ModelTaskError('History edge verification failed: '+str(exc)[:180]) from exc
        return {**links,'status':'certified_forecast_history_source_edges',
            'prediction_row_id':row_id,'run_id':scope['run_id'],
            'series_id':str(record['series_id']),'fold':int(record['fold']),
            'train_index_sha256':fold['train_index_sha256'],
            'history_normalized_rows':len(history),
            'cutoff_utc':fold['cutoff_utc'],
            'source_identity_semantics':'immutable_selected_BI_snapshot_ordinal_and_content',
            'original_file_row_ordinal_certified':False,
            'historical_observation_membership_certified':True,
            'per_feature_usage_or_attribution_certified':False,
            'reason':'Signed fold input observations; individual plugin feature usage is not asserted'}

    def diagnostics(self,pid,task_id,*,model=None,dimension=None,value=None):
        """Verified, project-scoped diagnostic aggregates; never training rows."""
        if dimension not in (None,'month','device'):
            raise ModelTaskError('Unknown evaluation slice dimension')
        if (value is not None and dimension is None) or (value is not None and len(value)>200):
            raise ModelTaskError('Slice value requires a valid dimension and bounded identifier')
        out=self.summary(pid,task_id)
        if not out['report_available']:
            raise ModelTaskConflict('Diagnostics require the current approved successful run')
        if model is None:
            model=(out.get('available_models') or ['forest'])[0]
        if not isinstance(model,str) or not 1<=len(model)<=100 or not model.replace('_','').isalnum():
            raise ModelTaskError('Unknown comparator family')
        if out['executor']=='sklearn_tabular' and model not in ('baseline','linear','forest'):
            raise ModelTaskError('Unknown tabular comparator; use baseline, linear or forest')
        if out['executor']!='sklearn_tabular' and out.get('available_models') and model not in out['available_models']:
            raise ModelTaskError('Unknown temporal comparator; use one of the evaluated model candidates')
        common={'schema_version':'model-diagnostics-v1','task_id':out['task_id'],
                'run_id':out['run_id'],'task_revision':out['revision'],
                'source_sha256':out['source_sha256'],
                'spec_sha256':out['spec_sha256'],
                'result_sha256':out['result_sha256'],
                'metric_provenance':out['metric_provenance'],
                'availability_ledger':out['availability_ledger'],
                'selected_model':model,'selection':{'dimension':dimension,'value':value}}
        if out['executor']!='sklearn_tabular':
            return self._temporal_diagnostics(pid,out['task_id'],out,common,model,dimension,value)
        run=self.tabular.get(pid,out['run_id'])
        if run['effective_state']!='succeeded' or run['result_sha256']!=out['result_sha256']:
            raise ModelTaskConflict('Model diagnostic result has changed')
        result=run.get('result') or {}
        if not result.get('fold_diagnostics'):
            return {**common,'status':'legacy_no_diagnostics','reason':
                    'Historical run predates diagnostics; create a revised task to evaluate again',
                    'fold_scores':[],'fold_diagnostics':[],'feature_contribution':None,
                    'available_dimensions':[],'slices':[],'budget':None}
        dims=result.get('slice_metrics',{}).get(model,{})
        candidates=dims.get(dimension,[]) if dimension else []
        slices=([x for x in candidates if x['value']==value] if value is not None else candidates)
        return {**common,'status':'available','fold_scores':result['fold_scores'][model],
                'fold_diagnostics':result['fold_diagnostics'][model],
                'feature_contribution':result['feature_contribution'] if
                  model==result.get('best_observed_model') else None,
                'best_observed_model':result.get('best_observed_model'),
                'budget':result.get('budget'),
                'fit_seconds':result.get('fit_seconds_per_fold',{}).get(model,[]),
                'comparison_protocol':result.get('comparison_protocol'),
                'available_dimensions':sorted(dims),
                'slices':slices,
                'slice_min_rows':15,
                'slice_notes':'Subset metrics use validation predictions only; no refitting or filtered training.'}

    def report(self,pid,task_id) -> str:
        """Read-only, source-verified report from exactly the current run evidence."""
        result=self.summary(pid,task_id)
        if not result['report_available']:
            raise ModelTaskConflict('Only current, successfully evaluated runs have a verifiable report')

        def safe(value):
            return str(value if value is not None else 'not_available').replace('|','\\|').replace('\n',' ').replace('\r',' ')

        lines=['# TimePilot 模型评价摘要','',
            f'- Task ID: `{result["task_id"]}`',
            f'- Task revision: `{result["revision"]}`',
            f'- Task kind: `{result["task_kind"]}`',
            f'- Run ID: `{result["run_id"]}`',
            f'- Executor: `{result["executor"]}`',
            f'- Data version: `{result["data_version"]}`',
            f'- Source SHA256: `{result["source_sha256"]}`',
            f'- ModelTaskSpec SHA256: `{result["spec_sha256"]}`',
            f'- Validation: `{result["validation_protocol"]}`',
            f'- Result SHA256: `{result["result_sha256"]}`',
            f'- Evidence SHA256: `{result["evidence_sha256"]}`',
            f'- Metric provenance: `{safe((result.get("metric_provenance") or {}).get("metric_scope"))}`',
            '', '## Evaluation metrics','']
        for group, measures in sorted(result['metric_sets'].items()):
            lines.extend([f'### {safe(group)}','',
                          '| Metric / model | Value |','|---|---|'])
            for metric, value in sorted(measures.items()):
                if isinstance(value,dict):
                    for key, measure in sorted(value.items()):
                        if type(measure) in (int,float,str) or measure is None:
                            lines.append(f'| {safe(metric)}.{safe(key)} | {safe(measure)} |')
                elif type(value) in (int,float,str) or value is None:
                    lines.append(f'| {safe(metric)} | {safe(value)} |')
            lines.append('')

        ledger=result.get('availability_ledger') or {}
        protocol=result.get('comparison_protocol') or {}
        lines.extend(['## Time availability and same-fold evidence','',
             f'- PIT ledger status: `{ledger.get("status","unavailable")}`',
             f'- PIT ledger SHA256: `{ledger.get("ledger_sha256","unavailable")}`',
             f'- Checked source rows: `{ledger.get("rows","unavailable")}`',
             f'- IANA timezone: `{safe(ledger.get("timezone"))}`',
             f'- Decision timestamp: `{safe(ledger.get("decision_column"))}`',
             f'- Label maturity provenance: `{safe((ledger.get("label_source") or {}).get("kind"))}`',
             f'- Publication-lag assumptions (including label lag): `{ledger.get("assumption_count",0)}`',
             f'- Same-fold protocol SHA256: `{protocol.get("protocol_sha256","unavailable")}`',''])
        if ledger.get('features'):
            lines.extend(['### Per-X availability ledger','',
                 '| Independent X | Availability evidence | Audited rows | Late rows | Availability timestamp digest |',
                 '|---|---|---:|---:|---|'])
            for feature in ledger['features']:
                origin=feature.get('source') or {}
                lines.append(f'| {safe(feature.get("feature"))} | '
                  f'{safe(origin.get("kind"))}: {safe(origin.get("column",origin.get("lag_days")))} | '
                  f'{feature.get("checked_rows")} | {feature.get("late_rows")} | '
                  f'`{safe(feature.get("available_times_sha256"))}` |')
            lines.append('')
        else:
            lines.extend(['No per-X publication ledger is available for this run.',''])

        if result['executor']=='sklearn_tabular':
            verified=self.tabular.get(pid,result['run_id'])
            if verified['result_sha256']!=result['result_sha256'] or verified['effective_state']!='succeeded':
                raise ModelTaskConflict('Result changed before report generation')
            content=verified.get('result') or {}
            if content.get('fold_scores'):
                lines.extend(['## Paired fold evaluation','',
                    f'- Score aggregation: `{safe(content.get("metric_scope"))}`',
                    f'- Best observed validation model: `{safe(content.get("best_observed_model"))}`',
                    f'- Primary score: `{safe(content.get("primary_metric"))}`',
                    f'- Same source and fold indices: `{protocol.get("same_validation_rows_and_fold_indices",False)}`',
                    f'- Same soft resource envelope: `{protocol.get("same_soft_resource_envelope",False)}`',
                    f'- Identical actual FLOPs/time: `{protocol.get("equal_realized_compute",False)}`',''])
                for family,folds in content['fold_scores'].items():
                    lines.extend([f'### {safe(family)} — validation fold scores',''])
                    if not folds:continue
                    columns=sorted(folds[0])
                    lines.extend(['| Fold | '+ ' | '.join(safe(c) for c in columns)+' |',
                                  '|---:|'+'|'.join('---:' for _ in columns)+'|'])
                    for index,row in enumerate(folds,1):
                        lines.append(f'| {index} | '+' | '.join(safe(row.get(c)) for c in columns)+' |')
                    lines.extend(['','#### Fold error / confusion diagnostics','',
                         '| Fold | Diagnostic type | Aggregated validation evidence |',
                         '|---:|---|---|'])
                    for index,diag in enumerate(content.get('fold_diagnostics',{}).get(family,[]),1):
                        info=', '.join(f'{k}={safe(v)}' for k,v in diag.items() if k not in ('kind','fold'))
                        lines.append(f'| {index} | {safe(diag.get("kind"))} | {info} |')
                    lines.append('')
                lines.extend(['## Comparison budget and environment','',
                    f'- Budget: `{safe(content.get("budget"))}`',
                    f'- Fit seconds by family/fold: `{safe(content.get("fit_seconds_per_fold"))}`',
                    f'- Library versions: `{safe(content.get("library_versions"))}`',''])
                importance=content.get('feature_contribution') or {}
                lines.extend(['## Feature contribution (held-out last fold)','',
                    f'- Method status: `{safe(importance.get("status"))}`',
                    f'- Primary metric: `{safe(importance.get("primary_metric"))}`',
                    f'- Permutation repeats: `{safe(importance.get("repeats"))}`','',
                    '| Feature | Metric deterioration after shuffle |',
                    '|---|---:|'])
                for item in importance.get('items',[]):
                    lines.append(f'| {safe(item.get("feature"))} | {safe(item.get("delta_metric"))} |')
                lines.extend(['','## Native BI validation slices','',
                    'Slices below cover validation predictions only, with >=15 records per published group. '
                    'They never alter the training data or create a fitted filtered model.',''])
                for family, dimensions in (content.get('slice_metrics') or {}).items():
                    for dimension,items in dimensions.items():
                        lines.extend([f'### {safe(family)} · {safe(dimension)}','',
                            '| Value | Validation rows | Fold IDs | Aggregated metrics |',
                            '|---|---:|---|---|'])
                        for item in items:
                            lines.append(f'| {safe(item.get("value"))} | {item.get("validation_rows")} | '
                                f'{safe(item.get("fold_ids"))} | {safe(item.get("metrics"))} |')
                        lines.append('')
            else:
                lines.extend(['Historical tabular run has no fold diagnostics. '
                              'A new model task revision is required to obtain them.',''])
        elif result.get('folds'):
            checked=self._temporal_fold_result(pid,result['task_id'],self.tasks.get(pid,result['task_id']),result['run_id'])
            lines.extend(['## Signed chronological fold evidence','',
                f'- Shared fold records: `{len(checked["folds"])}`',
                f'- Fold stage SHA256: `{checked["stage_sha256"]}`',
                f'- Per-fold label maturity audited: `{result.get("fold_label_maturity_certified")}`',
                f'- Equal realized compute: `{protocol.get("equal_realized_compute",False)}`',
                f'- Uniform wall/CPU cap per temporal plugin: `{min(180,self._legacy_budget_seconds(result["run_id"]))}`',
                '', '| Series | Fold | Training rows | Validation rows | Train index SHA256 | Validation index SHA256 |',
                '|---|---:|---:|---:|---|---|'])
            for fold in checked['folds']:
                lines.append('| '+' | '.join(safe(fold.get(x)) for x in (
                    'series_id','fold','train_rows','validation_rows','train_index_sha256',
                    'validation_index_sha256'))+' |')
            lines.append('')
            for name,scores in checked['fold_scores'].items():
                lines.extend([f'### {safe(name)}','',
                    '| Fold | Series | Paired validation scores |','|---:|---|---|'])
                for row in scores:
                    lines.append('| '+safe(row.get('fold'))+' | '+safe(row.get('series_id','all_devices'))+' | '+
                                 safe({k:v for k,v in row.items() if k not in ('fold','series_id')})+' |')
            lines.extend(['','## Derived prediction dataset','',
                f'- Asset ID: `{(result.get("prediction_asset") or {}).get("asset_id")}`',
                f'- Asset manifest SHA256: `{(result.get("prediction_asset") or {}).get("manifest_sha256")}`',
                f'- Immutable validation rows: `{(result.get("prediction_asset") or {}).get("rows")}`',
                '- Stored rows are accessible only through project-scoped, source-current read-only endpoints.',''])
        else:
            lines.extend(['## Legacy temporal executor evidence boundary','',
                 'This executor exposes test/validation aggregate metrics. '
                 'It does not publish verifiable per-fold indices, per-fold predictions, '
                 'feature contributions or per-device/month validation slices.','',
                 'An observed source availability ledger alone does not certify the '
                 "legacy executor's internal fold cutoffs or label maturity filters.",''])
            test=result.get('metric_sets',{}).get('test') or {}
            if result['task_kind']=='classification' and all(k in test for k in ('tp','fp','tn','fn')):
                lines.extend(['### Legacy test aggregate confusion counts','',
                    '| TP | FP | TN | FN |','|---:|---:|---:|---:|',
                    '| '+ ' | '.join(safe(test[k]) for k in ('tp','fp','tn','fn'))+' |',''])
        if result.get('prediction_asset'):
            lines.extend(['## Validation prediction asset provenance','',
                f'- Asset ID: `{result["prediction_asset"]["asset_id"]}`',
                f'- Validation rows: `{result["prediction_asset"]["rows"]}`',
                f'- CSV SHA256: `{result["prediction_asset"]["csv_sha256"]}`',
                f'- Parquet SHA256: `{result["prediction_asset"].get("parquet_sha256")}`',''])
        lines.extend(['## Evidence and limits','',
            f'- Confirmed fold records: {len(result["folds"])}',
            '- Retrospective evaluation only. No demonstrated production benefit.',
            '- No raw predictions, raw training rows or model weights are included in this report.',
            '- All metrics and slices are traced to this task revision, source version and verified result SHA256.',''])
        return '\n'.join(lines)
