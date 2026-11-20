"""v0.39.2: explicitly bind legacy PIT-aware temporal drafts to ModelTaskSpec.

The legacy model executor remains authoritative for temporal computation. A link
is evidence of checked source/field/protocol alignment, not an approval or a
run request. No automatic approval, execution, raw predictions, or model binary
is created by this adapter. Invalidated revisions fail closed.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import UUID

from pydantic import Field

from .model_task_spec import ModelTaskSpec, Strict, TEMPORAL_KINDS, canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError


class BindTemporalDraft(Strict):
    draft_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _same_sequence(left, right):
    return list(left or []) == list(right or [])


def _numbers_only(metrics):
    """Expose named scalar metrics only, never raw predictions or row arrays."""
    if not isinstance(metrics, dict):
        return {}
    permitted = ('MAE', 'RMSE', 'WAPE', 'MASE', 'Bias', 'ME', 'Count', 'R2',
                 'precision', 'recall', 'accuracy', 'specificity',
                 'false_positive_rate', 'tp', 'tn', 'fp', 'fn', 'selected_threshold')
    out = {}
    for key in permitted:
        value = metrics.get(key)
        if value is None:
            continue
        if type(value) in (int, float) and math.isfinite(float(value)):
            out[key] = value
    return out


class TemporalTaskBridge:
    """Project-scoped durable temporal result linking + unified read-only evidence."""

    def __init__(self, tasks, modeling, tabular_runs):
        self.tasks, self.modeling, self.runs = tasks, modeling, tabular_runs
        self.catalog = tasks.catalog
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_temporal_links (
                task_id TEXT PRIMARY KEY REFERENCES model_task_specs(task_id),
                project_id TEXT NOT NULL REFERENCES projects(id),
                revision INTEGER NOT NULL,
                spec_sha256 TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                draft_revision INTEGER NOT NULL,
                selection_sha256 TEXT NOT NULL,
                execution_config_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_task_temporal_links_project
                ON model_task_temporal_links(project_id,draft_id)''')

    def _verify(self, task, draft):
        """Return a frozen execution-config digest or raise a clear conflict."""
        spec = ModelTaskSpec.model_validate(task['spec'])
        if task.get('source_state') != 'current':
            raise ModelTaskConflict('Unified task source is stale or unavailable')
        if spec.task_kind not in TEMPORAL_KINDS:
            raise ModelTaskError('Only temporal ModelTaskSpec may bind a temporal draft')
        if task['project_id'] != draft['project_id']:
            raise ModelTaskConflict('Draft belongs to a different project')
        source = (draft.get('selection') or {}).get('source') or {}
        if (draft['asset_id'] != spec.asset_id or draft['version_id'] != spec.version_id or
                source.get('original_data_sha256') != spec.asset.source_sha256 or
                source.get('native_sha256') != spec.asset.native_sha256):
            raise ModelTaskConflict('Temporal draft data version or source SHA differs from ModelTaskSpec')
        selection = draft['selection']
        if selection.get('filters') or selection.get('view_id') or selection.get('selection_id'):
            raise ModelTaskConflict('Filtered BI training scope cannot bind a full-asset model contract')
        mapping = draft['mapping']
        plan = draft['plan']
        features = [feature.column for feature in spec.features]
        if (plan.get('task_kind') != spec.task_kind or
                mapping.get('target') != spec.target.column or
                mapping.get('time') != spec.time.event_time_column):
            raise ModelTaskConflict('Target, time clock or task kind differs between contracts')
        entities = [mapping['entity']] if mapping.get('entity') else []
        if not _same_sequence(spec.time.entity_columns, entities):
            raise ModelTaskConflict('Temporal entity grouping differs between contracts')
        if not _same_sequence(features, mapping.get('observed_features')):
            raise ModelTaskConflict('Temporal X feature set/order differs between contracts')
        # Legacy mapping only provides observed features with a common
        # availability policy. Do not silently coerce future-known features or
        # per-feature publication times into that weaker policy.
        if any(feature.role != 'observed_past' for feature in spec.features):
            raise ModelTaskConflict('Legacy temporal draft cannot represent future-known X roles')
        if any(feature.available_at_column and
               feature.available_at_column != spec.time.feature_available_at_column
               for feature in spec.features):
            raise ModelTaskConflict('Per-feature publication timestamps differ from legacy availability policy')
        if spec.time.decision_time_column and spec.time.decision_time_column != spec.time.event_time_column:
            raise ModelTaskConflict('Legacy temporal draft cannot prove a separate business decision clock')
        if spec.validation.gap:
            raise ModelTaskConflict('Legacy temporal draft cannot prove the requested validation embargo gap')
        if plan.get('frequency') != spec.time.frequency or mapping.get('timezone') != spec.time.timezone:
            raise ModelTaskConflict('Sampling frequency or time zone differs between contracts')
        if spec.time.feature_available_at_column:
            if (mapping.get('availability_policy') != 'source_column' or
                    mapping.get('available_at') != spec.time.feature_available_at_column):
                raise ModelTaskConflict('X publication timestamp differs between contracts')
        elif spec.time.publication_lag_days is not None:
            if (mapping.get('availability_policy') != 'declared_lag' or
                    mapping.get('publication_lag_days') != spec.time.publication_lag_days):
                raise ModelTaskConflict('X publication lag differs between contracts')
        else:
            raise ModelTaskConflict('Temporal link requires an explicit shared publication-time policy')
        if spec.task_kind == 'continuous_forecast':
            if (spec.validation.kind != 'rolling_origin' or
                    plan.get('horizon_steps') != spec.target.horizon_steps or
                    plan.get('validation_windows') != spec.validation.folds):
                raise ModelTaskConflict('Forecast horizon or rolling-origin protocol differs')
            policy = 'historical_observed' if features else 'target_history_only'
            if plan.get('feature_policy') != policy:
                raise ModelTaskConflict('Forecast input/history policy differs')
        else:
            expected=('leave_device_out' if plan.get('validation_strategy')=='leave_device_out'
                      else 'temporal_holdout')
            if (spec.validation.kind != expected or
                    plan.get('feature_policy') != 'snapshot_at_decision' or
                    plan.get('validation_windows') != spec.validation.folds):
                raise ModelTaskConflict('Early classification validation protocol or window count differs')
            actual = plan.get('classification') or {}
            if (not _same_sequence(actual.get('positive_values'), spec.target.positive_values) or
                    not _same_sequence(actual.get('negative_values'), spec.target.negative_values) or
                    actual.get('label_available_at') != spec.time.label_available_at_column or
                    actual.get('label_delay_seconds') != spec.time.label_maturity_lag_seconds):
                raise ModelTaskConflict('Classification label mapping or maturity differs')
        if not (draft.get('preflight') or {}).get('ready'):
            raise ModelTaskConflict('Legacy draft must pass its real PIT-aware preflight before linking')
        return canonical_sha({'mapping':mapping,'plan':plan,
                              'selection':selection.get('selection_hash'),
                              'legacy_revision':draft['revision']})

    def bind(self, pid: str, task_id: str, request: BindTemporalDraft) -> dict:
        pid = self.catalog._id(pid)
        self.catalog.writable(pid)
        task = self.tasks.get(pid, task_id)
        if task['revision'] != request.expected_revision:
            raise ModelTaskConflict('Unified task revision changed; refresh before binding')
        if task['status'] not in ('ready','approved'):
            raise ModelTaskConflict('Unified task must pass preflight before binding')
        try:
            UUID(request.draft_id)
        except (ValueError, TypeError):
            raise ModelTaskError('Invalid temporal draft ID') from None
        draft = self.modeling.draft(pid,request.draft_id)
        config_sha = self._verify(task,draft)
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.tasks._row(db,pid,task_id)
            if (row['revision'] != task['revision'] or row['spec_sha256'] != task['spec_sha256'] or
                    row['status'] not in ('ready','approved')):
                raise ModelTaskConflict('Model task changed during temporal binding')
            existing = db.execute('SELECT * FROM model_task_temporal_links WHERE task_id=? AND project_id=?',
                                  (task_id,pid)).fetchone()
            if existing:
                if (existing['draft_id'] != request.draft_id or existing['revision'] != task['revision'] or
                        existing['execution_config_sha256'] != config_sha or
                        existing['spec_sha256'] != task['spec_sha256']):
                    raise ModelTaskConflict('Existing temporal link is immutable; create a new model task')
            else:
                db.execute('''INSERT INTO model_task_temporal_links
                    (task_id,project_id,revision,spec_sha256,source_sha256,draft_id,
                     draft_revision,selection_sha256,execution_config_sha256,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (task_id,pid,task['revision'],task['spec_sha256'],
                     task['spec']['asset']['source_sha256'],request.draft_id,draft['revision'],
                     draft['selection']['selection_hash'],config_sha,_utcnow()))
        return self.get(pid,task_id)

    def get(self, pid: str, task_id: str) -> dict:
        pid = self.catalog._id(pid)
        task = self.tasks.get(pid,task_id)
        with self.catalog._db() as db:
            row = db.execute('SELECT * FROM model_task_temporal_links WHERE task_id=? AND project_id=?',
                             (task_id,pid)).fetchone()
        if row is None:
            return {'status':'unlinked','task_id':task_id,'task_revision':task['revision']}
        data = dict(row)
        try:
            draft = self.modeling.draft(pid,data['draft_id'])
            fingerprint = self._verify(task,draft)
            if (task['revision'] != data['revision'] or task['spec_sha256'] != data['spec_sha256'] or
                    draft['revision'] != data['draft_revision'] or
                    draft['selection']['selection_hash'] != data['selection_sha256'] or
                    fingerprint != data['execution_config_sha256']):
                raise ModelTaskConflict('Temporal binding changed after creation')
        except (ModelTaskConflict, ModelTaskError, KeyError, ValueError):
            return {'status':'stale','task_id':task_id,'draft_id':data['draft_id'],
                    'task_revision':task['revision'],'reason':'Source, revision or execution contract changed'}
        approved = task['status']=='approved' and bool(task['approval_sha256']) and bool(draft.get('approval'))
        return {'status':'linked' if approved else 'needs_approval',
                'task_id':task_id,'task_revision':task['revision'],
                'draft_id':data['draft_id'],'draft_revision':data['draft_revision'],
                'execution_config_sha256':data['execution_config_sha256'],
                'source_sha256':data['source_sha256'],
                'selection_sha256':data['selection_sha256'],
                'draft_status':draft['status'],'run_id':draft.get('run_id'),
                'approvals':{'unified':bool(task['approval_sha256']),
                             'temporal_executor':bool(draft.get('approval'))}}

    def evidence(self, pid: str, task_id: str) -> dict:
        task = self.tasks.get(pid,task_id)
        spec = ModelTaskSpec.model_validate(task['spec'])
        common = {'schema_version':'1.0', 'task_id':task_id, 'task_kind':spec.task_kind,
                  'source_sha256':spec.asset.source_sha256,'data_version':spec.version_id,
                  'spec_sha256':task['spec_sha256'],'task_revision':task['revision'],
                  'business_benefit_proven':False}
        if spec.task_kind not in TEMPORAL_KINDS:
            runs = self.runs.list(pid,task_id,limit=1)
            latest = runs[0] if runs else None
            status = latest['effective_state'] if latest else 'not_submitted'
            result = latest['result'] if latest and status == 'succeeded' else None
            out = {**common,'executor':'sklearn_tabular','status':status,
                   'run_id':latest['run_id'] if latest else None,
                   'validation_protocol':result['folds'][0]['split'] if result else spec.validation.kind,
                   'metrics':result['summary'] if result else None,
                   'folds':result['folds'] if result else None,
                   'primary_metric':result['primary_metric'] if result else None,
                   'evidence_level':'fold_comparison' if result else 'not_evaluated'}
        else:
            link = self.get(pid,task_id)
            status = link['status']
            out = {**common,'executor':'existing_temporal_pit', 'status':status,
                   'draft_id':link.get('draft_id'),'run_id':link.get('run_id'),
                   'validation_protocol':('rolling_origin' if spec.task_kind=='continuous_forecast'
                                          else 'forward_temporal_holdout'),
                   'metrics':None,'validation_metrics':None,'quality_status':None,
                   'evidence_level':'not_evaluated'}
            if status == 'linked' and not link.get('run_id'):
                out['status'] = 'not_submitted'
            if status == 'linked' and link.get('run_id'):
                evaluation = self.modeling.evaluation(pid,link['draft_id'],limit=1)
                out['status'] = str(evaluation['status'])
                if evaluation['status'] == 'succeeded':
                    quality = evaluation['quality']
                    out['metrics'] = _numbers_only(quality.get('metrics'))
                    out['validation_metrics'] = _numbers_only(quality.get('validation_metrics'))
                    out['quality_status'] = quality.get('status')
                    out['validation_protocol'] = quality.get('protocol_id')
                    out['evidence_level'] = 'independent_temporal_test'
                    out['selection_sha256'] = link['selection_sha256']
                    out['execution_config_sha256'] = link['execution_config_sha256']
        out['evidence_sha256'] = canonical_sha(out)
        return out
