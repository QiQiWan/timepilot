"""Durable, local-only multi-domain agent executor.

SQLite WAL + atomic lease claims provide restartable read steps. Mutations always
wait for explicit approval; an interrupted/failed write is NEVER blindly retried.
The planner can choose between allowlisted tools using their observed results.
Model advice is optional, schema-only, and cannot modify arguments or permissions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.agent_tools import ToolRegistry, ToolServices

LOG = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 400_000
FINAL = frozenset({'succeeded', 'failed', 'cancelled', 'needs_reconciliation'})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: Literal['understand', 'analyze', 'quality', 'features', 'forecast',
                  'semantic_benchmark', 'autonomous_modeling', 'mechanism', 'catalog', 'generic_understand']
    project_id: str | None = None
    candidate_id: str | None = None
    header_row: int | None = Field(default=None,ge=1,le=100)
    import_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list, max_length=100)
    asset_id: str | None = None
    version_id: str | None = None
    table_id: str | None = None
    view_id: str | None = None
    selection: dict = Field(default_factory=dict)
    lags: list[int] = Field(default_factory=lambda: [1, 3, 12], min_length=1, max_length=4)
    export_features: bool = False
    use_model: bool = False
    instruction: str = Field(default='', max_length=600)
    idempotency_key: str | None = Field(default=None, max_length=100, pattern=r'^[a-zA-Z0-9_.:-]+$')
    max_steps: int = Field(default=10, ge=1, le=24)
    max_units: int = Field(default=30, ge=1, le=100)
    max_seconds: int = Field(default=300, ge=10, le=3600)
    max_model_calls: int = Field(default=1, ge=0, le=2)
    max_attempts: int = Field(default=2, ge=1, le=4)
    horizon: int = Field(default=1, ge=1, le=6)
    folds: int = Field(default=3, ge=2, le=6)
    min_history: int = Field(default=24, ge=12, le=120)
    model_ids: list[str] = Field(default_factory=lambda:['last_value','moving_average','seasonal_naive','lag_ridge'], max_length=4)
    max_series: int = Field(default=8, ge=1, le=30)
    max_rows: int = Field(default=5000, ge=30, le=15000)
    mechanism_evidence_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def target_required(self):
        if self.goal in {'catalog','generic_understand'} and not self.project_id:
            raise ValueError('项目级 Agent 任务必须提供 project_id')
        if self.goal == 'generic_understand' and not (self.import_id and self.candidate_id):
            raise ValueError('通用数据识别需要 import_id 与 candidate_id')
        if self.goal in {'understand', 'semantic_benchmark'} and not self.import_id:
            raise ValueError('数据理解任务必须提供 import_id')
        if self.goal in {'quality', 'analyze', 'features', 'forecast', 'autonomous_modeling'} and not (
            (self.asset_id and self.version_id) or (self.table_id and self.goal == 'analyze')):
            raise ValueError('分析任务需要 asset_id + version_id，或表分析使用 table_id')
        if self.goal == 'forecast' and not self.view_id:
            raise ValueError('预测草稿任务需要已保存的 view_id')
        if self.goal == 'mechanism' and not self.mechanism_evidence_id:
            raise ValueError('机理评估必须提供已登记的物理状态/控制证据编号')
        if self.export_features and self.goal != 'features':
            raise ValueError('仅特征工程任务可以请求导出特征')
        if any(lag < 1 or lag > 36 for lag in self.lags) or len(self.lags) != len(set(self.lags)):
            raise ValueError('特征滞后阶数须在 1—36 且不能重复')
        return self


class AgentPlanner:
    """Constrained adaptive choice; a model can only select a safe candidate tool."""

    @staticmethod
    def _seen(steps: list[dict]) -> dict[str, dict]:
        return {s['name']: s['output'] for s in steps if s['status'] == 'succeeded'}

    def next_candidates(self, request: dict, steps: list[dict]) -> list[tuple[str, dict]]:
        seen = self._seen(steps)
        goal = request['goal']
        common = {'asset_id': request.get('asset_id'), 'version_id': request.get('version_id'),
                  'selection': request.get('selection') or {}}
        raw = {'import_id': request.get('import_id'), 'candidate_ids': request.get('candidate_ids', [])}
        if goal == 'catalog':
            return [('project.assets.list',{'project_id':request['project_id']})] if 'project.assets.list' not in seen else []
        if goal == 'generic_understand':
            return [('generic.schema.inspect',{'project_id':request['project_id'],
                     'import_id':request['import_id'],'candidate_id':request['candidate_id'],
                     'header_row':request.get('header_row')})] if 'generic.schema.inspect' not in seen else []
        if goal == 'semantic_benchmark':
            if 'semantic.seed' not in seen:
                return [('semantic.seed', {'import_id':request['import_id']})]
            # Human review happens between seed and evaluation, not by the model.
            return []
        if goal == 'mechanism':
            return [('mechanism.assess',{'evidence_id':request['mechanism_evidence_id']})] if 'mechanism.assess' not in seen else []
        if goal == 'autonomous_modeling':
            from timepilot.auto_experiment import ExperimentSpec
            options=ExperimentSpec.model_validate({
                 **common,'horizon':request['horizon'],'folds':request['folds'],
                 'min_history':request['min_history'],'model_ids':request['model_ids'],
                 'max_series':request['max_series'],'max_rows':request['max_rows'],
                 'max_seconds':request['max_seconds']}).model_dump(mode='json')
            if 'automodel.plan' not in seen:
                return [('automodel.plan',options)]
            if 'automodel.evaluate' not in seen:
                if not seen['automodel.plan'].get('eligible',False):
                    return []
                return [('automodel.evaluate',options)]
            evidence_id=request.get('mechanism_evidence_id')
            if evidence_id and 'mechanism.assess' not in seen:
                return [('mechanism.assess',{'evidence_id':evidence_id,
                                             'asset_id':request['asset_id'],
                                             'version_id':request['version_id']})]
            return []
        if goal == 'understand':
            for step in ('file.inspect', 'layout.detect', 'semantics.map'):
                if step not in seen:
                    return [(step, raw)]
            layout = seen.get('layout.detect', {})
            roles = seen.get('semantics.map', {})
            has_review = any(v.get('confidence') == 'review'
                             for item in roles.get('fields', []) for v in item.get('roles', {}).values())
            if 'quality.evaluate' not in seen and (has_review or layout.get('structure') != 'wide_monthly'):
                return [('quality.evaluate', raw)]
            if 'series.preview' not in seen:
                return [('series.preview', raw)]
            if 'data.clean_preview' not in seen:
                return [('data.clean_preview', raw)]
            return []
        if request.get('table_id') and goal == 'analyze':
            table = {'table_id': request['table_id']}
            return [('bi.profile', table)] if 'bi.profile' not in seen else []
        if 'quality.asset' not in seen:
            return [('quality.asset', common)]
        if goal == 'quality':
            return []
        if goal == 'analyze':
            if any(name in seen for name in ('bi.asset_group', 'bi.asset_grid')):
                return []
            summary = seen['quality.asset'].get('summary', {})
            # Numeric summation is prohibited across scope/source ambiguities.
            safe = summary.get('distinct_scopes') == 1 and summary.get('distinct_sources') == 1
            if safe:
                return [('bi.asset_group', {**common, 'group_by': 'timestamp', 'operation': 'sum'}),
                        ('bi.asset_grid', {**common, 'limit': 30})]
            return [('bi.asset_grid', {**common, 'limit': 30})]
        if goal == 'features':
            feat = {**common, 'lags': request.get('lags') or [1, 3, 12]}
            if 'features.preview' not in seen:
                return [('features.preview', feat)]
            if request.get('export_features') and 'features.export' not in seen:
                return [('features.export', feat)]
            return []
        if goal == 'forecast':
            if 'forecast.draft' not in seen:
                return [('forecast.draft', {**common, 'view_id': request['view_id'], 'options': {}})]
        return []


class DurableExecutor:
    """One durable database shared by UI, API and local background worker."""

    def __init__(self, root: Path, registry: ToolRegistry, services: ToolServices,
                 *, model_selector: Callable[[dict, list[str], dict], str] | None = None,
                 lease_seconds: float = 120.0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'agent_jobs.sqlite3'
        self.registry = registry
        self.services = services
        self.planner = AgentPlanner()
        self.model_selector = model_selector
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.worker_id = str(uuid4())
        self._init_database()

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=15000')
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self):
        with self._db() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                lease_owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0, spent_units INTEGER NOT NULL DEFAULT 0,
                spent_seconds REAL NOT NULL DEFAULT 0, model_calls INTEGER NOT NULL DEFAULT 0,
                model_status TEXT NOT NULL DEFAULT 'disabled', approved_sha TEXT,
                pending_json TEXT, last_error TEXT, result_json TEXT,
                idempotency_key TEXT UNIQUE)''')
            con.execute('''CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                name TEXT NOT NULL, attempt INTEGER NOT NULL,
                args_json TEXT NOT NULL, args_sha TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT, result_sha TEXT,
                units INTEGER NOT NULL, elapsed_seconds REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, finished_at REAL,
                FOREIGN KEY(job_id) REFERENCES jobs(id))''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_agent_steps_job ON steps(job_id,id)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_agent_jobs_status ON jobs(status,lease_until)')

    def create(self, request: JobRequest | dict) -> dict:
        obj = JobRequest.model_validate(request)
        data = obj.model_dump(mode='json')
        identifier, now = str(uuid4()), time.time()
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            if obj.idempotency_key:
                previous = con.execute('SELECT id,request_json FROM jobs WHERE idempotency_key=?',
                                       (obj.idempotency_key,)).fetchone()
                if previous:
                    if previous['request_json'] != _json(data):
                        con.rollback()
                        raise ValueError('幂等键已用于不同请求，禁止覆盖历史任务')
                    con.commit()
                    return self.status(previous['id'])
            con.execute('''INSERT INTO jobs (id,request_json,status,created_at,updated_at,
                    idempotency_key,model_status) VALUES (?,?,?,?,?,?,?)''',
                    (identifier,_json(data),'queued',now,now,obj.idempotency_key,
                     'not_requested' if obj.use_model else 'disabled'))
            con.commit()
        return self.status(identifier)

    def _row(self, conn, identifier):
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('Agent 任务不存在')
        return row

    def status(self, identifier: str) -> dict:
        with self._db() as con:
            job = self._row(con, identifier)
            steps = con.execute('SELECT * FROM steps WHERE job_id=? ORDER BY id', (identifier,)).fetchall()
        request = json.loads(job['request_json'])
        return {'job_id':job['id'], 'status':job['status'], 'goal':request['goal'],
                'created_at':job['created_at'], 'updated_at':job['updated_at'],
                'attempts':job['attempts'], 'cancel_requested':bool(job['cancel_requested']),
                'budget':{'used_units':job['spent_units'], 'max_units':request['max_units'],
                          'used_seconds':round(job['spent_seconds'],3), 'max_seconds':request['max_seconds'],
                          'used_steps':len([x for x in steps if x['status'] == 'succeeded']),
                          'max_steps':request['max_steps'], 'model_calls':job['model_calls'],
                          'max_model_calls':request['max_model_calls']},
                'model_status':job['model_status'], 'pending_approval':json.loads(job['pending_json']) if job['pending_json'] else None,
                'last_error':job['last_error'],
                'result':json.loads(job['result_json']) if job['result_json'] else None,
                'steps':[{'name':r['name'],'status':r['status'],'attempt':r['attempt'],
                          'units':r['units'],'elapsed_seconds':round(r['elapsed_seconds'],3),
                          'args_sha256':r['args_sha'], 'result_sha256':r['result_sha'],
                          'result':json.loads(r['result_json']) if r['result_json'] else None}
                         for r in steps]}

    def listing(self, limit: int = 50) -> list[dict]:
        with self._db() as con:
            ids=[x['id'] for x in con.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?', (min(100,max(1,limit)),))]
        return [self.status(i) for i in ids]

    def cancel(self, identifier: str) -> dict:
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=self._row(con,identifier)
            if row['status'] in {'queued','waiting_approval'}:
                con.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,updated_at=?,pending_json=NULL WHERE id=?", (time.time(),identifier))
            elif row['status']=='running':
                con.execute('UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?', (time.time(),identifier))
            con.commit()
        return self.status(identifier)

    def is_cancel_requested(self, identifier: str) -> bool:
        with self._db() as con:
            row=self._row(con,identifier)
            return bool(row['cancel_requested'])

    def approve(self, identifier: str, *, pending_sha256: str, confirmed: bool) -> dict:
        if confirmed is not True:
            raise ValueError('写操作必须由用户明确确认')
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=self._row(con,identifier)
            if row['status'] != 'waiting_approval' or not row['pending_json']:
                con.rollback()
                raise ValueError('当前任务没有需要审批的写操作')
            pending=json.loads(row['pending_json'])
            if pending['sha256'] != pending_sha256:
                con.rollback()
                raise ValueError('审批对象已变更，拒绝执行')
            con.execute("UPDATE jobs SET status='queued',approved_sha=?,updated_at=? WHERE id=?",
                        (pending_sha256,time.time(),identifier))
            con.commit()
        return self.status(identifier)

    def retry(self, identifier: str) -> dict:
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=self._row(con,identifier)
            request=json.loads(row['request_json'])
            if row['status'] != 'failed':
                con.rollback()
                raise ValueError('只能重试已失败的任务；不确定写入任务需要人工核对')
            if row['attempts'] >= request['max_attempts']:
                con.rollback()
                raise ValueError('已达到最大重试次数')
            con.execute("UPDATE jobs SET status='queued',attempts=attempts+1,cancel_requested=0,last_error=NULL,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE id=?",
                        (time.time(),identifier))
            con.commit()
        return self.status(identifier)

    def _claim(self):
        now=time.time()
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            # Recover crashed read-only steps; never replay an uncertain write.
            for row in con.execute("SELECT id FROM jobs WHERE status='running' AND lease_until<?", (now,)).fetchall():
                active=con.execute("SELECT name FROM steps WHERE job_id=? AND status='running' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
                if active and self.registry.get(active['name']).permission == 'write_confirm':
                    con.execute("UPDATE jobs SET status='needs_reconciliation',last_error='中断时写操作状态未知；请人工核对外部产物',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now,row['id']))
                else:
                    con.execute("UPDATE steps SET status='interrupted',finished_at=? WHERE job_id=? AND status='running'", (now,row['id']))
                    con.execute("UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?", (now,row['id']))
            picked=con.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not picked:
                con.commit()
                return None
            identifier=picked['id']
            con.execute("UPDATE jobs SET status='running',lease_owner=?,lease_until=?,attempts=CASE WHEN attempts=0 THEN 1 ELSE attempts END,updated_at=? WHERE id=?",
                        (self.worker_id,now+self.lease_seconds,now,identifier))
            con.commit()
            return identifier

    def _completed_steps(self, identifier: str) -> list[dict]:
        with self._db() as con:
            rows=con.execute("SELECT name,status,result_json FROM steps WHERE job_id=? AND status='succeeded' ORDER BY id", (identifier,)).fetchall()
        return [{'name':r['name'],'status':r['status'],'output':json.loads(r['result_json'])} for r in rows]

    def _update(self, identifier: str, *, status: str, error: str | None = None, result: dict | None = None,
                pending: dict | None = None):
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('''UPDATE jobs SET status=?,last_error=?,result_json=?,pending_json=?,
                           updated_at=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?''',
                        (status,error,_json(result) if result else None,_json(pending) if pending else None,
                         time.time(),identifier,self.worker_id))
            con.commit()

    def _select_with_model(self, identifier: str, request: dict, candidates: list[tuple[str,dict]],
                           history: list[dict]) -> tuple[str,dict]:
        chosen=candidates[0]
        if len(candidates)<2 or not request['use_model'] or not request['max_model_calls']:
            return chosen
        if self.model_selector is None:
            with self._db() as con:
                con.execute('UPDATE jobs SET model_status=? WHERE id=?',
                            ('fallback_model_unconfigured', identifier))
            return chosen
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            job=self._row(con,identifier)
            if job['spent_units'] + 3 + self.registry.get(chosen[0]).units > request['max_units']:
                con.commit()
                return chosen
            if job['model_calls'] >= request['max_model_calls']:
                con.commit()
                return chosen
            con.execute('UPDATE jobs SET model_calls=model_calls+1,spent_units=spent_units+3,model_status=? WHERE id=?',('trying',identifier))
            con.commit()
        model_started=time.monotonic()
        try:
            suggested = self.model_selector(request,[x[0] for x in candidates],
                    {'previous_tools':[x['name'] for x in history],
                     'quality_flags':{k:v for k,v in (history[-1]['output'].get('summary', {}) if history else {}).items()
                                      if k in {'distinct_scopes','distinct_sources','missing','unpublished','rows'}}}) if self.model_selector else None
            if suggested in {x[0] for x in candidates}:
                chosen=next(x for x in candidates if x[0]==suggested)
                status='used'
            else:
                status='fallback_invalid_or_unavailable'
        except Exception as exc:
            LOG.warning('Agent model selection failed; deterministic fallback: %s', type(exc).__name__)
            status='fallback_model_error'
        with self._db() as con:
            con.execute('UPDATE jobs SET model_status=?,spent_seconds=spent_seconds+? WHERE id=?',
                        (status,time.monotonic()-model_started,identifier))
        return chosen

    def _heartbeat(self, identifier: str, stop: threading.Event):
        while not stop.wait(max(0.4,self.lease_seconds/3)):
            try:
                with self._db() as con:
                    con.execute("UPDATE jobs SET lease_until=? WHERE id=? AND lease_owner=? AND status='running'",
                                (time.time()+self.lease_seconds,identifier,self.worker_id))
            except sqlite3.Error:
                LOG.exception('Agent heartbeat failed')

    def run_once(self) -> dict | None:
        identifier=self._claim()
        if identifier is None:
            return None
        with self._db() as con:
            job=self._row(con,identifier)
            request=json.loads(job['request_json'])
        if self.is_cancel_requested(identifier):
            self._update(identifier,status='cancelled')
            return self.status(identifier)
        try:
            history=self._completed_steps(identifier)
            candidates=self.planner.next_candidates(request,history)
        except Exception as exc:
            self._update(identifier,status='failed',error=f'{type(exc).__name__}: {str(exc)[:900]}')
            return self.status(identifier)
        if not candidates:
            self._update(identifier,status='succeeded',result={'completed_tools':[s['name'] for s in history],
                              'auto_train':False,'analysis_only':True})
            return self.status(identifier)
        try:
            tool,args=self._select_with_model(identifier,request,candidates,history)
            spec=self.registry.get(tool)
            # Validate arguments before requesting approval or charging the budget.
            validated=spec.args.model_validate(args).model_dump(mode='json')
        except Exception as exc:
            self._update(identifier,status='failed',error=f'{type(exc).__name__}: {str(exc)[:900]}')
            return self.status(identifier)
        digest=_sha({'tool':tool,'args':validated})
        with self._db() as con:
            budget_row=self._row(con,identifier)
            budget_exceeded=(len(history) >= request['max_steps'] or
                             budget_row['spent_units'] + spec.units > request['max_units'] or
                             budget_row['spent_seconds'] >= request['max_seconds'])
            approval=budget_row['approved_sha']
        # Fail before asking users to approve work the budget cannot execute.
        if budget_exceeded:
            self._update(identifier,status='failed',error='执行预算不足：步骤数、计算单位或执行时间已达到限制')
            return self.status(identifier)
        if spec.permission=='write_confirm' and approval!=digest:
            self._update(identifier,status='waiting_approval',pending={
                'tool':tool,'arguments':validated,'sha256':digest,
                'warning':'此操作会写入新草稿或新版本；不覆盖原始数据，必须人工审批'})
            return self.status(identifier)
        with self._db() as con:
            created=time.time()
            row=self._row(con,identifier)
            con.execute('''INSERT INTO steps(job_id,name,attempt,args_json,args_sha,status,units,created_at)
                           VALUES (?,?,?,?,?,'running',?,?)''',
                        (identifier,tool,row['attempts'],_json(validated),digest,spec.units,created))
            step_id=con.execute('SELECT last_insert_rowid()').fetchone()[0]
        stop=threading.Event()
        heartbeat=threading.Thread(target=self._heartbeat,args=(identifier,stop),daemon=True)
        heartbeat.start()
        started=time.monotonic()
        failure=None; result=None
        try:
            # Feature exporters may check the same cancellation hook between rows.
            from dataclasses import replace
            local_services=replace(self.services,cancel_requested=lambda: self.is_cancel_requested(identifier))
            result=self.registry.execute(tool,validated,local_services)
            encoded=_json(result)
            if len(encoded.encode('utf-8')) > MAX_RESPONSE_BYTES:
                raise ValueError('工具输出超出持久化上限，请缩小数据筛选范围')
        except Exception as exc:
            failure=f'{type(exc).__name__}: {str(exc)[:900]}'
        finally:
            stop.set()
            heartbeat.join(timeout=2)
        elapsed=time.monotonic()-started
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            current=self._row(con,identifier)
            if current['lease_owner']!=self.worker_id or current['status']!='running':
                con.rollback()
                return self.status(identifier)
            con.execute('''UPDATE steps SET status=?,result_json=?,result_sha=?,elapsed_seconds=?,finished_at=? WHERE id=?''',
                        ('failed' if failure else 'succeeded',_json(result) if result is not None and not failure else None,
                         _sha(result) if result is not None and not failure else None,elapsed,time.time(),step_id))
            con.execute('''UPDATE jobs SET spent_units=spent_units+?,spent_seconds=spent_seconds+?,updated_at=?,
                           pending_json=NULL,approved_sha=NULL WHERE id=?''',
                        (spec.units,elapsed,time.time(),identifier))
            con.commit()
        if failure:
            self._update(identifier,status='needs_reconciliation' if spec.permission=='write_confirm' else 'failed',
                         error=failure)
        elif self.is_cancel_requested(identifier):
            self._update(identifier,status='cancelled' if spec.permission=='read' else 'needs_reconciliation',
                         error='写入期间收到取消请求，请确认产物是否已提交' if spec.permission!='read' else None)
        elif elapsed >= request['max_seconds'] or self.status(identifier)['budget']['used_seconds']>request['max_seconds']:
            self._update(identifier,status='succeeded' if spec.permission=='write_confirm' else 'failed',
                         error=None if spec.permission=='write_confirm' else '单步执行超过时间预算')
        else:
            with self._db() as con:
                con.execute("UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_owner=?",
                            (time.time(),identifier,self.worker_id))
        return self.status(identifier)

    def work_loop(self, stop: threading.Event, *, poll_seconds: float = 0.7) -> None:
        while not stop.is_set():
            try:
                outcome=self.run_once()
            except Exception:
                LOG.exception('Agent executor worker error')
                outcome=None
            if outcome is None:
                stop.wait(poll_seconds)
