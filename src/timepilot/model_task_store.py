"""SQLite revisioned ModelTaskSpec persistence and server-bound preflight.

No source row data is stored. Approval is invalid after any spec edit.
A read/preflight on an asset whose signed source/schema changed is fail-closed.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .model_task_spec import (AssetRef, ModelTaskInput, ModelTaskSpec, ReplaceModelTask,
                              ApproveModelTask, canonical_sha, TEMPORAL_KINDS,
                              CLASSIFICATION_KINDS)
from .model_task_designer import describe_fields


class ModelTaskError(ValueError):
    pass


class ModelTaskConflict(ModelTaskError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class ModelTaskStore:
    def __init__(self, catalog, modeling):
        self.catalog, self.modeling = catalog, modeling
        self._lock = threading.RLock()
        self._setup()

    def _setup(self):
        with self.catalog._lock, self.catalog._db() as db:
            # The existing catalog startup lock creates the projects table.
            # Concurrent processes serialize migrations through BEGIN IMMEDIATE.
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_specs(
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id),
                asset_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision>=1),
                status TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                spec_sha256 TEXT NOT NULL,
                preflight_sha256 TEXT,
                approval_sha256 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_task_project
                ON model_task_specs(project_id,updated_at)''')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_spec_revisions(
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                revision INTEGER NOT NULL,
                spec_sha256 TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(task_id,revision))''')
            db.execute('''CREATE TABLE IF NOT EXISTS model_task_events(
                task_id TEXT NOT NULL REFERENCES model_task_specs(task_id),
                event_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL)''')

    def _task_id(self, task_id):
        try:
            return str(UUID(str(task_id)))
        except (ValueError, TypeError):
            raise ModelTaskError('Invalid model task identifier') from None

    def _source(self, project_id, asset_id, version_id):
        return self.modeling._source(project_id, asset_id, version_id)

    def _bind(self, pid: str, request: ModelTaskInput) -> ModelTaskSpec:
        source = self._source(pid, request.asset_id, request.version_id)
        asset = AssetRef(asset_id=request.asset_id, version_id=request.version_id,
                         source_sha256=source['original_data_sha256'],
                         native_sha256=source['native_sha256'],
                         columns_sha256=canonical_sha(source['source_columns']))
        return ModelTaskSpec(**request.model_dump(mode='python'), project_id=pid, asset=asset)

    @staticmethod
    def _unpack(row: sqlite3.Row) -> dict:
        out = dict(row)
        out['spec'] = json.loads(out.pop('spec_json'))
        return out

    def _row(self, db, pid: str, task_id: str):
        row = db.execute('''SELECT * FROM model_task_specs
            WHERE task_id=? AND project_id=?''', (self._task_id(task_id), self.catalog._id(pid))).fetchone()
        if row is None:
            raise KeyError('Model task not found in this project')
        return row

    def create(self, pid: str, request: ModelTaskInput) -> dict:
        pid = self.catalog._id(pid)
        self.catalog.writable(pid)
        spec = self._bind(pid, request)
        spec_hash = canonical_sha(spec)
        task_id, ts = str(uuid4()), utcnow()
        payload = json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT INTO model_task_specs
                (task_id,project_id,asset_id,version_id,revision,status,spec_json,spec_sha256,
                 preflight_sha256,approval_sha256,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (task_id,pid,spec.asset_id,spec.version_id,1,'review_required',payload,spec_hash,None,None,ts,ts))
            db.execute('INSERT INTO model_task_spec_revisions VALUES (?,?,?,?,?)',
                       (task_id,1,spec_hash,payload,ts))
            self._event(db,task_id,1,'created',spec_hash,ts)
        return self.get(pid, task_id, check_source=False)

    @staticmethod
    def _event(db, task_id, revision, kind, payload_hash, ts):
        db.execute('INSERT INTO model_task_events VALUES (?,?,?,?,?,?)',
                   (task_id,str(uuid4()),revision,kind,payload_hash,ts))

    def get(self, pid: str, task_id: str, *, check_source=True) -> dict:
        self.catalog.get(pid)
        with self.catalog._db() as db:
            result = self._unpack(self._row(db,pid,task_id))
        if check_source:
            try:
                spec = ModelTaskSpec.model_validate(result['spec'])
                current = self._source(pid, spec.asset_id, spec.version_id)
                changed = self._mismatch(spec, current)
            except Exception:
                changed = ['source_unavailable']
            result['source_state'] = 'stale_source' if changed else 'current'
            result['source_problems'] = changed
            if changed:
                result['effective_status'] = 'stale_source'
            else:
                result['effective_status'] = result['status']
        return result

    @staticmethod
    def _mismatch(spec: ModelTaskSpec, source: dict):
        errors = []
        if source['original_data_sha256'] != spec.asset.source_sha256:
            errors.append('source_sha256_changed')
        if source['native_sha256'] != spec.asset.native_sha256:
            errors.append('native_sha256_changed')
        if canonical_sha(source['source_columns']) != spec.asset.columns_sha256:
            errors.append('column_schema_changed')
        return errors

    def list(self, pid: str, *, limit=100) -> list[dict]:
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows = db.execute('''SELECT task_id,project_id,asset_id,version_id,revision,status,
                spec_sha256,spec_json,created_at,updated_at FROM model_task_specs
                WHERE project_id=? ORDER BY updated_at DESC LIMIT ?''',
                (self.catalog._id(pid),min(max(1,int(limit)),200))).fetchall()
        items=[]
        for row in rows:
            item=dict(row)
            task=json.loads(item.pop('spec_json'))
            item['name']=task.get('name','')
            item['task_kind']=task.get('task_kind','')
            item['target']=task.get('target',{}).get('column','')
            items.append(item)
        return items

    def replace(self, pid: str, task_id: str, request: ReplaceModelTask) -> dict:
        self.catalog.writable(pid)
        # Reject cross-asset requests before looking up a different source;
        # retain conflict semantics for already-saved tasks.
        with self.catalog._db() as db:
            current = self._row(db, pid, task_id)
            if request.spec.asset_id != current['asset_id'] or request.spec.version_id != current['version_id']:
                raise ModelTaskConflict('Create a new task for a different asset or version')
        spec = self._bind(self.catalog._id(pid), request.spec)
        digest = canonical_sha(spec)
        payload = json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)
        ts = utcnow()
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, pid, task_id)
            if row['revision'] != request.expected_revision:
                raise ModelTaskConflict('Stale revision: refresh task before saving')
            old = ModelTaskSpec.model_validate_json(row['spec_json'])
            if spec.asset != old.asset or spec.asset_id != old.asset_id or spec.version_id != old.version_id:
                raise ModelTaskConflict('Asset, schema or source SHA changed: create a new task')
            # Identical resubmission preserves approval + temporal linking.
            if digest != row['spec_sha256']:
                revision = row['revision'] + 1
                db.execute("""UPDATE model_task_specs SET revision=?, status='review_required',
                    spec_json=?,spec_sha256=?,preflight_sha256=NULL,approval_sha256=NULL,updated_at=?
                    WHERE task_id=? AND project_id=? AND revision=?""",
                    (revision,payload,digest,ts,self._task_id(task_id),self.catalog._id(pid),row['revision']))
                db.execute('INSERT INTO model_task_spec_revisions VALUES (?,?,?,?,?)',
                           (self._task_id(task_id),revision,digest,payload,ts))
                self._event(db,self._task_id(task_id),revision,'replaced',digest,ts)
        return self.get(pid,task_id)

    def preflight(self, pid: str, task_id: str) -> dict:
        self.catalog.writable(pid)
        current=self.get(pid,task_id,check_source=False)
        spec=ModelTaskSpec.model_validate(current['spec'])
        blockers = []
        warnings = []
        try:
            source=self._source(pid,spec.asset_id,spec.version_id)
        except Exception:
            source=None
            blockers.append('Source asset unavailable or integrity check failed')
        if source is not None:
            blockers.extend(self._mismatch(spec,source))
            columns=set(source['source_columns'])
            used=[spec.target.column]+[x.column for x in spec.features]
            used += [x for x in (spec.time.event_time_column,spec.time.decision_time_column,
                                   spec.time.label_available_at_column,
                                   spec.time.feature_available_at_column) if x]
            used += spec.time.entity_columns
            used += [x.available_at_column for x in spec.features if x.available_at_column]
            missing=sorted(set(used)-columns)
            if missing:
                blockers.append('Missing columns: '+', '.join(missing))
            else:
                profile=self.modeling.profile(pid,spec.asset_id,spec.version_id)
                by_name={field['name']:field for field in profile['fields']}
                missing_profile=sorted(set(used)-set(by_name))
                if missing_profile:
                    blockers.append('Profile missing required columns: '+', '.join(missing_profile))
                    # Do not index stale/malformed profile fields below.
                    return self._finish_preflight(pid,task_id,current,spec,blockers,warnings)
                if spec.task_kind not in CLASSIFICATION_KINDS and \
                        float(by_name[spec.target.column].get('sample_numeric_rate') or 0)<.95:
                    blockers.append('Numeric target cannot be parsed reliably')
                if spec.task_kind in CLASSIFICATION_KINDS and \
                        int(by_name[spec.target.column].get('sample_distinct') or 0)<2:
                    blockers.append('Target needs at least two observed labels')
                roles={f['name']:f for f in describe_fields(profile)['role_candidates']}
                for feature in spec.features:
                    suggestion=roles[feature.column]
                    if suggestion['role'] in ('entity','label','available_at','time'):
                        blockers.append('Unsupported direct X role: '+feature.column)
                    if suggestion.get('warnings') and any('post' in w.lower() or 'future' in w.lower() or
                                                          'after' in w.lower() or 'result' in w.lower()
                                                          or '\u4e8b\u540e' in w for w in suggestion['warnings']):
                        blockers.append('Potential post-outcome X: '+feature.column)
                    if float(by_name[feature.column].get('sample_numeric_rate') or 0)<.95:
                        blockers.append('Categorical encoding required before X is usable: '+feature.column)
                    if feature.role=='known_future' and not (feature.available_at_column or
                                                              spec.time.feature_available_at_column):
                        blockers.append('Future-known X must include published availability time: '+feature.column)
                if spec.task_kind in TEMPORAL_KINDS:
                    if spec.features and not (spec.time.feature_available_at_column or spec.time.publication_lag_days is not None or
                            all(x.available_at_column for x in spec.features)):
                        blockers.append('Feature availability must be explicitly declared for temporal tasks')
                    if spec.task_kind=='classification' and not (spec.time.label_available_at_column or
                            spec.time.label_maturity_lag_seconds is not None):
                        blockers.append('Early classification needs a label maturity timestamp or reviewed lag')
                if spec.validation.kind in ('temporal_holdout','rolling_origin','grouped_temporal_holdout','leave_month_out','leave_device_out') and \
                        not spec.time.event_time_column:
                    blockers.append('Time split lacks an event clock')
                if spec.validation.kind in ('temporal_holdout','rolling_origin','leave_month_out','leave_device_out') and spec.task_kind in ('tabular_regression','tabular_classification'):
                    if not (spec.time.label_available_at_column or
                            spec.time.label_maturity_lag_seconds is not None):
                        blockers.append('Forward-only evaluation needs the training label maturity timestamp or reviewed lag')
                    if not (spec.time.feature_available_at_column or
                            spec.time.publication_lag_days is not None or
                            all(x.available_at_column for x in spec.features)):
                        blockers.append('Time/device evaluation requires per-X publication time or reviewed lag')
                if spec.validation.kind == 'grouped_temporal_holdout' and spec.task_kind in ('tabular_regression','tabular_classification'):
                    blockers.append('Grouped temporal holdout is not supported by the tabular runner; select leave_device_out')
                if profile.get('source_rows',0) < spec.validation.folds+1:
                    blockers.append('Insufficient rows for requested folds')
                warnings.append('Profile rates are sampled; full-row training validation remains mandatory')
                if spec.task_kind in TEMPORAL_KINDS:
                    warnings.append('PIT availability is declared here; per-fold PIT validation runs with training')
        return self._finish_preflight(pid,task_id,current,spec,blockers,warnings)

    def _finish_preflight(self,pid,task_id,current,spec,blockers,warnings):
        ready=not blockers
        digest=canonical_sha({'spec':current['spec_sha256'],'revision':current['revision'],
                              'ready':ready,'blockers':blockers})
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._row(db,pid,task_id)
            if row['revision'] != current['revision'] or row['spec_sha256'] != current['spec_sha256']:
                raise ModelTaskConflict('Task changed during preflight; retry')
            db.execute('''UPDATE model_task_specs SET preflight_sha256=?,approval_sha256=NULL,
                status=?,updated_at=? WHERE project_id=? AND task_id=?''',
                (digest,'ready' if ready else 'review_required',utcnow(),self.catalog._id(pid),self._task_id(task_id)))
            self._event(db,self._task_id(task_id),row['revision'],
                        'preflight_ready' if ready else 'preflight_blocked',digest,utcnow())
        return {'task_id':task_id,'revision':current['revision'],'ready':ready,
                'blockers':blockers,'warnings':warnings,'preflight_sha256':digest,
                'source_sha256':spec.asset.source_sha256,'spec_sha256':current['spec_sha256']}

    def approve(self, pid: str, task_id: str, request: ApproveModelTask) -> dict:
        self.catalog.writable(pid)
        if not (request.confirm_target and request.confirm_feature_availability and request.confirm_split):
            raise ModelTaskError('Explicit target, feature availability and validation confirmation required')
        # Recheck asset on approval; old preflight never authorizes a changed source.
        task=self.get(pid,task_id)
        if task['source_state']!='current':
            raise ModelTaskConflict('Source or column schema changed; create a new task')
        ts=utcnow()
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=self._row(db,pid,task_id)
            if row['revision']!=request.expected_revision:
                raise ModelTaskConflict('Revision changed before approval')
            if row['status'] != 'ready' or not row['preflight_sha256']:
                raise ModelTaskConflict('Run a successful preflight before approval')
            approval=canonical_sha({'task_id':task_id,'revision':row['revision'],
                                    'spec_sha256':row['spec_sha256'],
                                    'preflight_sha256':row['preflight_sha256']})
            db.execute('''UPDATE model_task_specs SET approval_sha256=?,status='approved',updated_at=?
                WHERE project_id=? AND task_id=?''',
                (approval,ts,self.catalog._id(pid),self._task_id(task_id)))
            self._event(db,self._task_id(task_id),row['revision'],'approved',approval,ts)
        return self.get(pid,task_id)

    def events(self, pid: str, task_id: str) -> list[dict]:
        self.catalog.get(pid)
        with self.catalog._db() as db:
            self._row(db,pid,task_id)
            rows=db.execute('''SELECT event_id,revision,event_type,payload_sha256,created_at
                FROM model_task_events WHERE task_id=? ORDER BY created_at,event_id''',
                (self._task_id(task_id),)).fetchall()
        return [dict(r) for r in rows]
