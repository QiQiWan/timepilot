"""Durable wall-clock budget and attempt-fenced stages for the legacy temporal engine.

The legacy forecast queue is SQLAlchemy-owned (separate from project model-task
SQLite). Keep the plan in this database and freeze it in the same transaction
as the queued run. Existing runs without a plan stay explicitly unverified.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import text

from .model_experiment_control import PROFILES, DEFAULT_PROFILE
from .model_task_spec import canonical_sha


def stamp():
    return datetime.now(timezone.utc).isoformat()


class TemporalBudgetExpired(RuntimeError):
    pass


class TemporalLeaseLost(RuntimeError):
    pass


class TemporalExperimentControl:
    def __init__(self, store):
        self.store = store
        with store.engine.begin() as db:
            if store.engine.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            db.exec_driver_sql('''CREATE TABLE IF NOT EXISTS temporal_experiment_controls (
                run_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
                wall_limit_seconds INTEGER NOT NULL, started_epoch REAL, deadline_epoch REAL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            db.exec_driver_sql('''CREATE TABLE IF NOT EXISTS temporal_run_steps (
                run_id TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running','succeeded','failed','interrupted')),
                started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT,
                PRIMARY KEY(run_id,stage,attempt))''')

    @staticmethod
    def plan(profile=DEFAULT_PROFILE):
        if profile not in PROFILES:
            raise ValueError('Temporal experiment budget must be 5m, 15m or 30m')
        return {'schema':'temporal-experiment-plan-v1','budget_profile':profile,
                'run_wall_seconds_total':PROFILES[profile],
                'execution_scope':'entire_legacy_temporal_run_including_io_and_reporting',
                'enforcement':'external_process_tree_watchdog',
                'retry_policy':'same_first_start_and_deadline',
                'fold_resource_limit':'isolated_plugin_wall_cpu_rss'}

    def freeze(self, session, run_id, profile):
        plan = self.plan(profile)
        payload = json.dumps(plan, sort_keys=True, separators=(',', ':'))
        digest = canonical_sha(plan)
        old = session.execute(text('SELECT plan_sha256 FROM temporal_experiment_controls WHERE run_id=:id'),
                              {'id':run_id}).mappings().first()
        if old:
            if old['plan_sha256']!=digest:
                raise ValueError('Temporal run already has a different frozen experiment plan')
            return
        session.execute(text('''INSERT INTO temporal_experiment_controls
            (run_id,plan_json,plan_sha256,wall_limit_seconds,created_at,updated_at)
            VALUES (:id,:p,:sha,:limit,:now,:now)'''),
            {'id':run_id,'p':payload,'sha':digest,'limit':PROFILES[profile],'now':stamp()})

    def on_claim(self, session, run_id, attempt, now=None):
        now = time.time() if now is None else float(now)
        row = session.execute(text('SELECT * FROM temporal_experiment_controls WHERE run_id=:id'),
                              {'id':run_id}).mappings().first()
        # Previously queued jobs cannot retroactively claim that their budget
        # was frozen. They are still fenced by the worker watchdog/lease.
        if row is None:
            return True
        if row['deadline_epoch'] is not None and now>=row['deadline_epoch']:
            return False
        if row['deadline_epoch'] is None:
            session.execute(text('''UPDATE temporal_experiment_controls
                SET started_epoch=:now,deadline_epoch=:deadline,updated_at=:utc
                WHERE run_id=:id AND started_epoch IS NULL'''),
                {'now':now,'deadline':now+row['wall_limit_seconds'],'utc':stamp(),'id':run_id})
        session.execute(text('''UPDATE temporal_run_steps SET state='interrupted',finished_at=:now,
            error_code='WORKER_CRASH_OR_LEASE_RECLAIM' WHERE run_id=:id AND attempt<:attempt
            AND state='running' '''),{'now':stamp(),'id':run_id,'attempt':attempt})
        return True

    def detail(self, run_id):
        with self.store.sessions() as session:
            row = session.execute(text('SELECT * FROM temporal_experiment_controls WHERE run_id=:id'),
                                  {'id':run_id}).mappings().first()
        if not row:
            return {'status':'legacy_budget_unverified','run_id':run_id}
        plan = json.loads(row['plan_json'])
        if canonical_sha(plan)!=row['plan_sha256']:
            raise ValueError('Temporal experiment frozen plan checksum mismatch')
        return {'status':'frozen','run_id':run_id,'plan_sha256':row['plan_sha256'],
                'budget':plan,'started_at_epoch':row['started_epoch'],
                'deadline_epoch':row['deadline_epoch'],
                'remaining_wall_seconds':round(max(0.,(row['deadline_epoch'] or
                    (time.time()+row['wall_limit_seconds']))-time.time()),2)}

    def deadline(self, run_id):
        with self.store.sessions() as session:
            row=session.execute(text('SELECT deadline_epoch FROM temporal_experiment_controls WHERE run_id=:id'),
                                {'id':run_id}).mappings().first()
        return row['deadline_epoch'] if row else None

    def assert_active(self, run_id):
        deadline=self.deadline(run_id)
        if deadline is not None and time.time()>=deadline:
            raise TemporalBudgetExpired('EXPERIMENT_BUDGET_EXHAUSTED: temporal experiment deadline passed')

    def started(self, run_id, stage, attempt):
        with self.store.sessions.begin() as s:
            s.execute(text('''INSERT INTO temporal_run_steps
                (run_id,stage,attempt,state,started_at) VALUES (:id,:stage,:attempt,'running',:now)
                ON CONFLICT (run_id,stage,attempt) DO UPDATE SET state='running',
                    started_at=excluded.started_at,finished_at=NULL,error_code=NULL'''),
                {'id':run_id,'stage':stage,'attempt':attempt,'now':stamp()})

    def finished(self, run_id, stage, attempt, state, error=None):
        if state not in ('succeeded','failed','interrupted'):
            raise ValueError('Invalid stage receipt state')
        with self.store.sessions.begin() as s:
            s.execute(text('''UPDATE temporal_run_steps SET state=:state,finished_at=:now,
                error_code=:err WHERE run_id=:id AND stage=:stage AND attempt=:attempt
                AND state='running' '''),{'id':run_id,'stage':stage,'attempt':attempt,
                                       'now':stamp(),'state':state,'err':error})

    def interrupt(self, run_id, attempt, error):
        with self.store.sessions.begin() as s:
            s.execute(text('''UPDATE temporal_run_steps SET state='interrupted',finished_at=:now,
                error_code=:error WHERE run_id=:id AND attempt=:attempt AND state='running' '''),
                {'now':stamp(),'error':error,'id':run_id,'attempt':attempt})

    def steps(self, run_id):
        with self.store.sessions() as s:
            rows=s.execute(text('''SELECT stage,attempt,state,started_at,finished_at,error_code
                FROM temporal_run_steps WHERE run_id=:id ORDER BY attempt,started_at,stage'''),
                {'id':run_id}).mappings().all()
        return [dict(row) for row in rows]
