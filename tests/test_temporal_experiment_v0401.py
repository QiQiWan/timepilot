"""Actual temporal runtime, external watchdog and device OOD regression."""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import threading
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.config import Settings
from timepilot.contracts import TaskSpec
from timepilot.db import DatasetRow, Store, utcnow
from timepilot.runtime import RuntimeEngine
from timepilot.model_task_spec import ModelTaskInput,ApproveModelTask
from timepilot.model_task_unified_runs import StartUnifiedRun
from test_model_task_unified_runs_v0393 import _real_forecast
from test_modeling_workflow_v017 import prepare_bi
from test_industrial_modeling_v018 import classification_csv,classification_payload


def cheap_temporal(tmp_path):
    settings=Settings.from_env(tmp_path)
    store=Store(settings.db_url)
    engine=RuntimeEngine(settings,store)
    dataset_id=str(uuid4())
    with store.sessions.begin() as s:
        s.add(DatasetRow(id=dataset_id,name='synthetic-watchdog',manifest_json=json.dumps({'snapshot_sha256':'0'*64})))
    task=TaskSpec(task_id='watchdog-check',name='Watchdog run',model_candidates=['last_value'])
    run_id=engine.queue(task,dataset_id,budget_profile='5m')
    assert engine.claim(run_id)==run_id
    return engine,store,run_id


def test_temporal_run_frozen_budget_real_training_and_stable_evidence(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,tid,_=_real_forecast(app,http)
        request=StartUnifiedRun(expected_revision=1,confirm_temporal_execution=True,budget_profile='5m')
        received=app.state.unified_model_runs.start(pid,tid,request)
        engine=app.state.projects.engine
        budget=engine.budgets.detail(received['run_id'])
        assert budget['status']=='frozen'
        assert budget['budget']['run_wall_seconds_total']==300
        assert engine.work_once(received['run_id'])
        done=app.state.unified_model_runs.summary(pid,tid)
        assert done['state']=='succeeded',done
        assert done['experiment']['plan_sha256']==budget['plan_sha256']
        assert done['experiment']['deadline_epoch']>done['experiment']['started_at_epoch']
        assert done['evidence_sha256']==app.state.unified_model_runs.summary(pid,tid)['evidence_sha256']
        steps=app.state.unified_model_runs.steps(pid,tid)
        assert steps['items'] and all(x['state']=='succeeded' for x in steps['items'])
        assert {x['stage'] for x in steps['items']}>={'validate','backtest','forecast','quality'}
        assert app.state.unified_model_runs.preview(pid,tid,'30m')['experiment_plan']['run_wall_seconds_total']==1800
        with pytest.raises(KeyError):app.state.unified_model_runs.steps(other,tid)


def _replace_child_with_sleep(monkeypatch,engine):
    import timepilot.runtime as module
    original=subprocess.Popen
    launched=threading.Event()
    state={}
    def sleepy(_cmd,**kwargs):
        proc=original([sys.executable,'-c','import time; time.sleep(25)'],**kwargs)
        state['pid']=proc.pid
        launched.set()
        return proc
    monkeypatch.setattr(module.subprocess,'Popen',sleepy)
    return launched,state


def test_watchdog_kills_entire_temporal_task_on_frozen_deadline(tmp_path,monkeypatch):
    import psutil
    engine,store,run_id=cheap_temporal(tmp_path)
    launched,state=_replace_child_with_sleep(monkeypatch,engine)
    with store.sessions.begin() as db:
        db.execute(text('UPDATE temporal_experiment_controls SET deadline_epoch=:end WHERE run_id=:run'),
                   {'end':time.time()+.6,'run':run_id})
    began=time.monotonic()
    assert engine.execute(run_id) is False
    assert launched.is_set()
    assert time.monotonic()-began<4
    assert not psutil.pid_exists(state['pid'])
    row=store.run(run_id)
    assert row.status=='failed' and row.error_code=='EXPERIMENT_BUDGET_EXHAUSTED'
    assert engine.claim(run_id) is None
    store.engine.dispose()


def test_watchdog_cancellation_reaps_child_and_is_idempotent(tmp_path,monkeypatch):
    import psutil
    engine,store,run_id=cheap_temporal(tmp_path)
    launched,state=_replace_child_with_sleep(monkeypatch,engine)
    result=[]
    runner=threading.Thread(target=lambda:result.append(engine.execute(run_id)),daemon=True)
    runner.start()
    assert launched.wait(6)
    engine.cancel(run_id)
    runner.join(7)
    assert not runner.is_alive() and result==[False]
    assert not psutil.pid_exists(state['pid'])
    assert store.run(run_id).status=='cancelled'
    assert engine.cancel(run_id)=='cancelled'
    store.engine.dispose()


def test_expired_lease_marks_old_stage_interrupted_on_reclaim(tmp_path):
    engine,store,run_id=cheap_temporal(tmp_path)
    attempt=store.run(run_id).attempts
    engine.budgets.started(run_id,'backtest',attempt)
    store.update_run(run_id,lease_until=utcnow()-timedelta(seconds=3))
    other=RuntimeEngine(engine.settings,store,worker_id='fresh-worker')
    assert other.claim(run_id)==run_id
    row=store.run(run_id)
    assert row.attempts==attempt+1 and row.worker_id=='fresh-worker'
    stages=other.budgets.steps(run_id)
    assert stages[0]['state']=='interrupted'
    assert stages[0]['error_code']=='WORKER_CRASH_OR_LEASE_RECLAIM'
    assert other.budgets.detail(run_id)['budget']['run_wall_seconds_total']==300
    store.engine.dispose()


def multi_device_classification(n=480):
    rows=list(csv.reader(io.StringIO(classification_csv(n).decode('utf-8'))))
    for i,row in enumerate(rows[1:]):
        row[0]=f'SMT-{i%4:02d}'
    out=io.StringIO(newline='')
    writer=csv.writer(out);writer.writerows(rows)
    return out.getvalue().encode('utf-8')


def test_classification_device_ood_has_no_heldout_device_in_training(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,_=prepare_bi(app,http,multi_device_classification())
        root=f'/api/v1/projects/{pid}'
        legacy_payload=classification_payload(aid)
        legacy_payload['plan']['validation_strategy']='leave_device_out'
        created=http.post(root+'/modeling/drafts',json=legacy_payload)
        assert created.status_code==201,created.text
        did=created.json()['draft_id']
        check=app.state.modeling.preflight(pid,did)
        assert check['ready'],check['blockers']
        req=ModelTaskInput.model_validate({
            'name':'Cross equipment early defect','asset_id':aid,'task_kind':'classification',
            'target':{'column':'是否不良','positive_values':['NG'],'negative_values':['OK']},
            'features':[{'column':'锡膏厚度','role':'observed_past'},
                        {'column':'负压','role':'observed_past'}],
            'time':{'event_time_column':'节点时间','entity_columns':['设备'],
                    'frequency':'h','publication_lag_days':0,
                    'label_available_at_column':'标签确认时间'},
            'validation':{'kind':'leave_device_out','folds':2}})
        tasks=app.state.model_tasks
        tid=tasks.create(pid,req)['task_id']
        assert tasks.preflight(pid,tid)['ready']
        app.state.temporal_model_bridge.bind(pid,tid,__import__(
            'timepilot.model_task_temporal_bridge',fromlist=['BindTemporalDraft']).BindTemporalDraft(
                draft_id=did,expected_revision=1))
        tasks.approve(pid,tid,ApproveModelTask(expected_revision=1,
            confirm_target=True,confirm_feature_availability=True,confirm_split=True))
        result=app.state.unified_model_runs.start(pid,tid,StartUnifiedRun(
            expected_revision=1,confirm_temporal_execution=True,budget_profile='5m'))
        assert app.state.projects.engine.work_once(result['run_id'])
        info=app.state.unified_model_runs.summary(pid,tid)
        assert info['state']=='succeeded',info
        assert len(info['folds'])==2
        assert info['comparison_protocol']['split_kind']=='leave_device_out'
        assert all(f['split']=='leave_device_out' and f['device_disjoint']
                   and f['heldout_device_sha256'] and f['train_device_sha256']
                   and f['train_device_sha256']!=f['heldout_device_sha256']
                   and f['training_labels_mature_at_cutoff'] for f in info['folds'])
        assert {f['fold'] for f in info['folds']}=={1,2}
        assert info['diagnostics_available']
        assert info['prediction_asset']['rows']>0


def test_forecast_cannot_claim_unsupported_zero_shot_device_transfer():
    with pytest.raises(ValueError,match='forward-only'):
        ModelTaskInput.model_validate({
            'name':'Unsupported zero-shot forecast','asset_id':'fake',
            'task_kind':'continuous_forecast','target':{'column':'target'},
            'time':{'event_time_column':'t','entity_columns':['device']},
            'validation':{'kind':'leave_device_out','folds':2}})


def test_frozen_temporal_budget_exhausted_before_child_spawn_is_terminal(tmp_path, monkeypatch):
    import timepilot.runtime as module
    engine,store,run_id=cheap_temporal(tmp_path)
    with store.sessions.begin() as db:
        db.execute(text('UPDATE temporal_experiment_controls SET deadline_epoch=:end WHERE run_id=:run'),
                   {'end':time.time()-0.2,'run':run_id})
    launched=[]
    monkeypatch.setattr(module.subprocess,'Popen',lambda *_a,**_k: launched.append(True))
    assert engine.execute(run_id) is False
    assert launched==[]
    row=store.run(run_id)
    assert row.status=='failed'
    assert row.error_code=='EXPERIMENT_BUDGET_EXHAUSTED'
    assert engine.budgets.detail(run_id)['deadline_epoch']<time.time()
    store.engine.dispose()


def test_temporal_budget_frozen_plan_is_idempotent_and_rejects_change(tmp_path):
    engine,store,run_id=cheap_temporal(tmp_path)
    receipt=engine.budgets.detail(run_id)
    with store.sessions.begin() as db:
        engine.budgets.freeze(db,run_id,'5m')
        with pytest.raises(ValueError,match='different frozen'):
            engine.budgets.freeze(db,run_id,'30m')
    frozen=engine.budgets.detail(run_id)
    assert frozen['plan_sha256']==receipt['plan_sha256']
    assert frozen['started_at_epoch']==receipt['started_at_epoch']
    assert frozen['deadline_epoch']==receipt['deadline_epoch']
    store.engine.dispose()
