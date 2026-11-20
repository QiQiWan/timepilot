"""v0.40.0: durable per-experiment wall budget, cancellation and fenced steps.

The catalog is the authority for run ownership. A daemon monitoring thread
renews the lease while a parent waits for an isolated model process. The
isolated-process supervisor receives a cheap, synchronous cancellation probe.
This module never executes user supplied code or SQL.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from .model_task_spec import canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError

PROFILES = {'5m': 300, '15m': 900, '30m': 1800}
DEFAULT_PROFILE = '15m'
LEASE_SECONDS = 60
PULSE_SECONDS = .25
RENEW_SECONDS = 5.0


def _utc():
    return datetime.now(timezone.utc).isoformat()


class RunCancelled(ModelTaskError):
    pass


class ExperimentBudgetExhausted(ModelTaskError):
    pass


class RunLeaseLost(ModelTaskConflict):
    pass


class ExperimentControls:
    def __init__(self, catalog):
        self.catalog = catalog
        with catalog._lock, catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS model_experiment_controls(
                run_id TEXT PRIMARY KEY REFERENCES model_task_runs(run_id),
                project_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
                plan_json TEXT NOT NULL,plan_sha256 TEXT NOT NULL,
                wall_limit_seconds INTEGER NOT NULL,
                started_at_epoch REAL,deadline_epoch REAL,
                cancel_requested_at TEXT, created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS model_run_steps(
                step_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES model_task_runs(run_id),
                project_id TEXT NOT NULL,model_id TEXT NOT NULL,fold INTEGER NOT NULL,
                lease_epoch INTEGER NOT NULL,attempt INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running','succeeded','failed','cancelled','interrupted')),
                started_at TEXT NOT NULL,finished_at TEXT,
                wall_seconds REAL,cpu_seconds REAL,peak_rss_bytes INTEGER,error_code TEXT)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_model_run_steps
                ON model_run_steps(run_id,lease_epoch,fold,model_id)''')
            # Additive migration: historical runs keep their state, signatures and
            # results. No budget is retroactively claimed to have been enforced.
            old_plan=self.plan(DEFAULT_PROFILE)
            old_plan['legacy_budget_origin']='migration_unverified'
            old_payload=json.dumps(old_plan,sort_keys=True,separators=(',',':'))
            previous=db.execute('''SELECT r.run_id,r.project_id,r.task_id,r.revision
                FROM model_task_runs r LEFT JOIN model_experiment_controls c
                  ON c.run_id=r.run_id WHERE c.run_id IS NULL''').fetchall()
            for row in previous:
                db.execute('''INSERT INTO model_experiment_controls
                    (run_id,project_id,task_id,revision,plan_json,plan_sha256,
                     wall_limit_seconds,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (row['run_id'],row['project_id'],row['task_id'],row['revision'],old_payload,
                     canonical_sha(old_plan),old_plan['run_wall_seconds_total'],_utc(),_utc()))

    @staticmethod
    def plan(profile=DEFAULT_PROFILE):
        if profile not in PROFILES:
            raise ModelTaskError('Budget profile must be one of 5m, 15m, 30m')
        return {'schema':'experiment-plan-v1','budget_profile':profile,
                'run_wall_seconds_total':PROFILES[profile],
                'max_fit_seconds_per_fold':45,'max_parallel_steps':1,
                'cleanup_grace_seconds':5,'lease_heartbeat_seconds':RENEW_SECONDS,'lease_ttl_seconds':LEASE_SECONDS,
                'execution_scope':'tabular_models_and_fold_metrics',
                'enforcement':'parent_supervised_process_tree'}

    def freeze(self, db, *, run_id, pid, tid, revision, profile):
        plan=self.plan(profile)
        payload=json.dumps(plan,sort_keys=True,separators=(',',':'))
        row=db.execute('SELECT plan_sha256 FROM model_experiment_controls WHERE run_id=?',
                       (run_id,)).fetchone()
        digest=canonical_sha(plan)
        if row and row['plan_sha256']!=digest:
            existing=db.execute('SELECT plan_json FROM model_experiment_controls WHERE run_id=?',
                                (run_id,)).fetchone()
            previous=json.loads(existing['plan_json'])
            if previous.get('legacy_budget_origin')=='migration_unverified' and profile==DEFAULT_PROFILE:
                return
            raise ModelTaskConflict('Run already has a different immutable budget; revise the task to rerun')
        if not row:
            db.execute('''INSERT INTO model_experiment_controls
                (run_id,project_id,task_id,revision,plan_json,plan_sha256,
                 wall_limit_seconds,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                (run_id,pid,tid,revision,payload,digest,plan['run_wall_seconds_total'],_utc(),_utc()))

    def on_claim(self, db, run_id, now=None):
        """Preserve the first start/deadline across worker crashes and takeovers."""
        now=time.time() if now is None else now
        control=db.execute('SELECT * FROM model_experiment_controls WHERE run_id=?',
                           (run_id,)).fetchone()
        if control is None:
            raise ModelTaskError('Frozen experiment budget is missing')
        if control['cancel_requested_at']:
            return 'RUN_CANCELLED'
        if control['deadline_epoch'] is not None and now>=control['deadline_epoch']:
            return 'EXPERIMENT_BUDGET_EXHAUSTED'
        if control['deadline_epoch'] is None:
            db.execute('''UPDATE model_experiment_controls SET started_at_epoch=?,deadline_epoch=?,
                updated_at=? WHERE run_id=? AND started_at_epoch IS NULL''',
                (now,now+control['wall_limit_seconds'],_utc(),run_id))
        return None

    def detail(self, pid, task_id, run_id):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT c.*,r.state,r.error_code,r.lease_epoch,r.attempts
              FROM model_experiment_controls c JOIN model_task_runs r ON c.run_id=r.run_id
              WHERE c.project_id=? AND c.task_id=? AND c.run_id=?''',
              (self.catalog._id(pid),task_id,run_id)).fetchone()
        if row is None:
            raise KeyError('Experiment plan does not exist for this project/task')
        doc=dict(row)
        plan=json.loads(doc.pop('plan_json'))
        if canonical_sha(plan)!=doc['plan_sha256']:
            raise ModelTaskError('Experiment plan digest mismatch')
        remaining=(max(0.,doc['deadline_epoch']-time.time())
                   if doc['deadline_epoch'] is not None else doc['wall_limit_seconds'])
        return {'run_id':run_id,'plan_sha256':doc['plan_sha256'],'budget':plan,
                'deadline_epoch':doc['deadline_epoch'],'started_at_epoch':doc['started_at_epoch'],
                'remaining_wall_seconds':round(remaining,2),'cancel_requested':bool(doc['cancel_requested_at']),
                'state':doc['state'],'error_code':doc['error_code'],
                'lease_epoch':doc['lease_epoch'],'attempts':doc['attempts']}

    def steps(self, pid, tid, run_id):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            check=db.execute('SELECT 1 FROM model_task_runs WHERE project_id=? AND task_id=? AND run_id=?',
                             (self.catalog._id(pid),tid,run_id)).fetchone()
            if check is None:raise KeyError('Model run not found in this project/task')
            rows=db.execute('''SELECT step_id,model_id,fold,lease_epoch,attempt,state,
                started_at,finished_at,wall_seconds,cpu_seconds,peak_rss_bytes,error_code
                FROM model_run_steps WHERE run_id=? ORDER BY started_at,fold,model_id''',(run_id,)).fetchall()
        return [dict(item) for item in rows]

    def cancel(self, pid, tid, run_id):
        self.catalog.writable(pid)
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT r.state,c.cancel_requested_at
                FROM model_task_runs r JOIN model_experiment_controls c ON c.run_id=r.run_id
                WHERE r.project_id=? AND r.task_id=? AND r.run_id=?''',
                (self.catalog._id(pid),tid,run_id)).fetchone()
            if row is None:raise KeyError('Model run not found in this project/task')
            if row['state'] in ('succeeded','failed'):
                return {'run_id':run_id,'state':row['state'],'cancel_accepted':False}
            if not row['cancel_requested_at']:
                db.execute('''UPDATE model_experiment_controls SET cancel_requested_at=?,updated_at=?
                    WHERE run_id=?''',(_utc(),_utc(),run_id))
            if row['state']=='queued':
                db.execute('''UPDATE model_task_runs SET state='failed',error_code='RUN_CANCELLED',
                    error_message='Model run cancelled before training',updated_at=?
                    WHERE run_id=? AND state='queued' ''',(_utc(),run_id))
                return {'run_id':run_id,'state':'cancelled','cancel_accepted':True}
            return {'run_id':run_id,'state':'cancel_requested','cancel_accepted':True}

    def guard(self, row, token, epoch):
        with self.catalog._db() as db:
            ctrl=db.execute('SELECT deadline_epoch FROM model_experiment_controls WHERE run_id=?',
                            (row['run_id'],)).fetchone()
        if ctrl is None or ctrl['deadline_epoch'] is None:
            raise ModelTaskError('Experiment deadline must be frozen before execution')
        remaining=max(0,ctrl['deadline_epoch']-time.time())
        return RunGuard(self,row,token,epoch,time.monotonic()+remaining)


class RunGuard:
    def __init__(self,controls,row,token,epoch,deadline_mono):
        self.controls=controls
        self.catalog=controls.catalog
        self.row,self.token,self.epoch=row,token,epoch
        self.deadline_mono=deadline_mono
        self._stop=threading.Event()
        self._closed=threading.Event()
        self._reason=None
        self._thread=None

    def __enter__(self):
        self._pulse(renew=True)
        self._thread=threading.Thread(target=self._loop,name='model-lease-'+self.row['run_id'][:8],daemon=True)
        self._thread.start()
        self.ensure()
        return self

    def _loop(self):
        last_renew=time.monotonic()
        while not self._closed.wait(PULSE_SECONDS):
            try:
                now=time.monotonic()
                self._pulse(renew=(now-last_renew>=RENEW_SECONDS))
                if now-last_renew>=RENEW_SECONDS:last_renew=now
            except Exception:
                self._reason='RUN_LEASE_LOST'
                self._stop.set()
                return
            if self._stop.is_set():return

    def _pulse(self,renew=False):
        now=time.time()
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            control=db.execute('''SELECT r.state,r.lease_owner,r.lease_epoch,r.lease_deadline,
                c.cancel_requested_at,c.deadline_epoch
                FROM model_task_runs r JOIN model_experiment_controls c ON r.run_id=c.run_id
                WHERE r.run_id=?''',(self.row['run_id'],)).fetchone()
            if (not control or control['state']!='running' or
                control['lease_owner']!=self.token or control['lease_epoch']!=self.epoch or
                control['lease_deadline'] is None or control['lease_deadline']<now):
                self._reason='RUN_LEASE_LOST'
            elif control['cancel_requested_at']:
                self._reason='RUN_CANCELLED'
            elif control['deadline_epoch']<=now:
                self._reason='EXPERIMENT_BUDGET_EXHAUSTED'
            elif renew:
                update=db.execute('''UPDATE model_task_runs SET lease_deadline=?,updated_at=?
                  WHERE run_id=? AND state='running' AND lease_owner=? AND lease_epoch=?
                  AND lease_deadline>=?''',
                  (now+LEASE_SECONDS,_utc(),self.row['run_id'],self.token,self.epoch,now))
                if update.rowcount!=1:self._reason='RUN_LEASE_LOST'
        if self._reason:self._stop.set()

    def ensure(self):
        if self._reason=='RUN_CANCELLED':raise RunCancelled('RUN_CANCELLED: cancel requested by user')
        if self._reason=='RUN_LEASE_LOST':raise RunLeaseLost('RUN_LEASE_LOST: ownership changed; computation discarded')
        if self._reason=='EXPERIMENT_BUDGET_EXHAUSTED' or time.monotonic()>=self.deadline_mono:
            raise ExperimentBudgetExhausted('EXPERIMENT_BUDGET_EXHAUSTED: total experiment deadline reached')
        return True

    def limit(self,step_cap=45):
        self.ensure()
        # Closing/checkpoint work is accounted for in the experiment wall cap.
        remaining=self.deadline_mono-time.monotonic()-5
        if remaining<=0:
            raise ExperimentBudgetExhausted('EXPERIMENT_BUDGET_EXHAUSTED: no safe time left for a new model/fold')
        return min(float(step_cap),remaining)

    def step(self,model,fold):
        self.ensure()
        return _Step(self,model,fold)

    def __exit__(self,exc_type,exc,tb):
        self._closed.set()
        if self._thread:self._thread.join(timeout=2)


class _Step:
    def __init__(self,guard,model,fold):
        self.guard=guard
        self.model=model
        self.fold=int(fold)
        self.step_id=str(uuid4())
        self.started=time.monotonic()
        self.cost={}

    def __enter__(self):
        guard=self.guard
        with guard.catalog._lock,guard.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT state,lease_owner,lease_epoch FROM model_task_runs
                WHERE run_id=?''',(guard.row['run_id'],)).fetchone()
            if not row or row['state']!='running' or row['lease_owner']!=guard.token or row['lease_epoch']!=guard.epoch:
                raise RunLeaseLost('RUN_LEASE_LOST: cannot begin model fold')
            db.execute('''INSERT INTO model_run_steps
                (step_id,run_id,project_id,model_id,fold,lease_epoch,attempt,state,started_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (self.step_id,guard.row['run_id'],guard.row['project_id'],self.model,self.fold,
                 guard.epoch,guard.row['attempts']+1,'running',_utc()))
        guard.ensure()
        return self

    def observed(self,cost):
        self.cost=dict(cost)

    def __exit__(self,exc_type,exc,tb):
        guard=self.guard
        status=('succeeded' if exc is None else 'cancelled' if isinstance(exc,RunCancelled)
                else 'interrupted' if isinstance(exc,RunLeaseLost) else 'failed')
        error=(None if exc is None else
            'EXPERIMENT_BUDGET_EXHAUSTED' if isinstance(exc,ExperimentBudgetExhausted) else
            'RUN_CANCELLED' if isinstance(exc,RunCancelled) else
            'RUN_LEASE_LOST' if isinstance(exc,RunLeaseLost) else
            str(exc).split(':',1)[0][:80] if isinstance(exc,ModelTaskError) else type(exc).__name__)
        cost=self.cost
        with guard.catalog._lock,guard.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE model_run_steps SET state=?,finished_at=?,wall_seconds=?,
                cpu_seconds=?,peak_rss_bytes=?,error_code=? WHERE step_id=?''',
                (status,_utc(),round(time.monotonic()-self.started,4),
                 cost.get('cpu_seconds'),cost.get('peak_rss_bytes'),error,self.step_id))
