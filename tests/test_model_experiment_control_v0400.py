"""v0.40.0: real catalog, fenced durable experiment tests and process supervision."""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_model_task_unified_runs_v0393 import tabular
from test_model_task_execution_v0391 import approved
from timepilot.model_experiment_control import RunCancelled, ExperimentBudgetExhausted
from timepilot.model_fit_isolation import supervise
from timepilot.model_task_store import ModelTaskConflict, ModelTaskError
from timepilot.model_task_api import create_model_task_router
from timepilot.model_task_unified_runs import StartUnifiedRun


def test_frozen_plan_refuses_profile_mutation_and_cross_project_cancel(tmp_path):
    catalog,_,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    preview=unified.preview(catalog.a,tid,'5m')
    assert preview['plan']['run_wall_seconds_total']==300
    assert preview['available_for_total_budget']
    started=unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1,budget_profile='5m'))
    rid=started['run_id']
    plan=runs.get(catalog.a,rid)['experiment']
    assert plan['plan_sha256']==preview['plan_sha256']
    assert plan['budget']['budget_profile']=='5m'
    with pytest.raises(ModelTaskConflict,match='different immutable budget'):
        runs.submit(catalog.a,tid,budget_profile='30m')
    with pytest.raises(KeyError):runs.cancel(catalog.b,tid,rid)
    with pytest.raises(ModelTaskConflict):unified.cancel(catalog.a,tid,2)
    assert not unified.steps(catalog.a,tid)['items']


def test_queued_cancel_is_idempotent_never_started(tmp_path):
    catalog,_,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1))['run_id']
    response=unified.cancel(catalog.a,tid,1,rid)
    assert response['state']=='cancelled'
    assert not unified.cancel(catalog.a,tid,1,rid)['cancel_accepted']
    assert not runs.work_once()
    ended=runs.get(catalog.a,rid)
    assert ended['state']=='failed' and ended['effective_state']=='cancelled'
    assert ended['error_code']=='RUN_CANCELLED'
    assert ended['prediction_asset'] is None


def test_running_cancel_kills_an_actual_python_worker(tmp_path):
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    row,owner,epoch=runs._claim()
    results=[]
    def cancel():
        time.sleep(.4)
        results.append(runs.cancel(catalog.a,tid,rid))
    t=threading.Thread(target=cancel)
    t.start()
    begin=time.monotonic()
    with runs.experiments.guard(row,owner,epoch) as guard:
        with pytest.raises(RunCancelled,match='RUN_CANCELLED'):
            supervise([sys.executable,'-c','import time;time.sleep(20)'],
                      seconds=20,max_cpu_seconds=20,max_rss_bytes=512*1024**2,
                      stop_check=guard.ensure)
    t.join(timeout=3)
    assert results and results[0]['state']=='cancel_requested'
    assert time.monotonic()-begin<3


def test_total_budget_survives_reclaim_no_new_steps(tmp_path,monkeypatch):
    from timepilot import model_experiment_control as control
    monkeypatch.setitem(control.PROFILES,'5m',1)
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid,budget_profile='5m')['run_id']
    row,owner,epoch=runs._claim()
    with catalog._db() as db:
        db.execute('UPDATE model_experiment_controls SET deadline_epoch=? WHERE run_id=?',
                   (time.time()-.1,rid))
        db.execute('UPDATE model_task_runs SET lease_deadline=? WHERE run_id=?',
                   (time.time()-10,rid))
    assert not runs.work_once()
    ended=runs.get(catalog.a,rid)
    assert ended['error_code']=='EXPERIMENT_BUDGET_EXHAUSTED'
    assert ended['state']=='failed'
    assert not runs.steps(catalog.a,tid,rid)['items']


def test_prepared_prediction_recovers_without_retraining_after_worker_crash(tmp_path,monkeypatch):
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    calls=[]
    def fake_compute(row,guard=None):
        calls.append(row['run_id'])
        return {'engine_version':'crash-fixture','folds':[{'fold':1}],
            '_prediction_rows':[{'model':'baseline','fold':1,
             'source_row_sha256':'f'*64,'source_row_index':0,
             'actual':2.,'prediction':3.,'residual':1.,'absolute_error':1.}]}
    monkeypatch.setattr(runs,'_execute',fake_compute)
    actual_commit=runs.prepared.commit
    def crash(*a,**kw):
        raise SystemExit('injected_worker_death_before_publish')
    monkeypatch.setattr(runs.prepared,'commit',crash)
    with pytest.raises(SystemExit,match='injected_worker_death'):
        runs.work_once()
    assert runs.prepared.prepared(rid)
    with pytest.raises(KeyError):runs.predictions.get(catalog.a,tid,rid)
    with catalog._db() as db:
        db.execute('UPDATE model_task_runs SET lease_deadline=? WHERE run_id=?',
                   (time.time()-10,rid))
    monkeypatch.setattr(runs.prepared,'commit',actual_commit)
    assert runs.work_once()
    assert calls==[rid], 'Recovered a signed preparation without retraining'
    result=runs.get(catalog.a,rid)
    assert result['state']=='succeeded' and result['prediction_asset']['rows']==1
    assert result['lease_epoch']==2
    assert result['result']['engine_version']=='crash-fixture'
    assert runs.prepared._row(rid)['state']=='committed'
    assert not runs.work_once()


def test_tampered_prepared_artifact_never_gets_committed(tmp_path,monkeypatch):
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    row,token,epoch=runs._claim()
    task=tasks.get(catalog.a,tid)
    data={'test':True}
    rows=[{'model':'baseline','fold':1,'source_row_sha256':'a'*64,
           'source_row_index':1,'actual':5,'prediction':4,'residual':-1,'absolute_error':1}]
    runs.prepared.prepare(catalog.a,tid,task,rid,token,epoch,data,rows)
    prep=runs.prepared._row(rid)
    file=runs.predictions._inside(prep['staged_rel'])/'validation.csv'
    file.write_text(file.read_text()+'tamper\n')
    with pytest.raises(ModelTaskError,match='lost or modified'):
        runs.prepared.commit(catalog.a,tid,rid,token,epoch)
    assert runs.get(catalog.a,rid)['state']=='running'
    with pytest.raises(KeyError):runs.predictions.get(catalog.a,tid,rid)


def test_execution_preview_steps_cancel_http_project_boundaries(tmp_path):
    catalog,_,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    app=FastAPI()
    app.include_router(create_model_task_router(tasks,runs,unified_runs=unified))
    client=TestClient(app)
    base=f'/api/v1/projects/{catalog.a}/model-tasks/{tid}/execution'
    preview=client.post(base+'/plan/preview',json={'budget_profile':'30m'})
    assert preview.status_code==200 and preview.json()['plan']['run_wall_seconds_total']==1800
    assert client.post(base+'/plan/preview',json={'budget_profile':'100h'}).status_code==422
    start=client.post(base,json={'expected_revision':1})
    assert start.status_code==202,start.text
    rid=start.json()['run_id']
    assert client.get(base+'/steps').status_code==200
    assert client.post(base+'/cancel',json={'expected_revision':1,'run_id':rid}).status_code==200
    assert client.post(base+'/cancel',json={'expected_revision':1,'run_id':'invalid'}).status_code==409
    other=f'/api/v1/projects/{catalog.b}/model-tasks/{tid}/execution'
    assert client.get(other+'/steps').status_code==404
    assert client.post(other+'/cancel',json={'expected_revision':1,'run_id':rid}).status_code==404


def test_existing_v0396_run_is_upgraded_without_mutating_old_result(tmp_path):
    catalog,model,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    with catalog._db() as db:
        db.execute('DELETE FROM model_experiment_controls WHERE run_id=?',(rid,))
    # Reconstructing the service simulates upgrading an existing catalog.
    from timepilot.model_task_execution import ModelTaskRunService
    reopened=ModelTaskRunService(tasks,model)
    detail=reopened.get(catalog.a,rid)['experiment']
    assert detail['budget']['legacy_budget_origin']=='migration_unverified'
    assert reopened.submit(catalog.a,tid)['run_id']==rid
    with pytest.raises(ModelTaskConflict):reopened.submit(catalog.a,tid,budget_profile='30m')


def test_after_rename_before_catalog_commit_is_recoverable(tmp_path,monkeypatch):
    # Filesystem rename and SQLite cannot be one ACID transaction; a signed
    # preparation must be retried without refitting after a crash at this seam.
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    calls=[]
    def fake_compute(row,guard=None):
        calls.append(row['run_id'])
        return {'engine_version':'rename-recovery-test','folds':[{'fold':1}],
            '_prediction_rows':[{'model':'baseline','fold':1,
             'source_row_sha256':'f'*64,'source_row_index':0,
             'actual':2.,'prediction':3.,'residual':1.,'absolute_error':1.}]}
    monkeypatch.setattr(runs,'_execute',fake_compute)
    verify=runs.prepared._verify_files
    attempts=[]
    def crash_once(final,manifest):
        if not attempts:
            attempts.append('renamed')
            raise SystemExit('crash_after_file_rename')
        return verify(final,manifest)
    monkeypatch.setattr(runs.prepared,'_verify_files',crash_once)
    with pytest.raises(SystemExit,match='crash_after_file_rename'):
        runs.work_once()
    prep=runs.prepared._row(rid)
    assert prep['state']=='prepared'
    assert runs.predictions._inside(prep['final_rel']).is_dir()
    assert not runs.predictions._inside(prep['staged_rel']).exists()
    with pytest.raises(KeyError):runs.predictions.get(catalog.a,tid,rid)
    with catalog._db() as db:
        db.execute('UPDATE model_task_runs SET lease_deadline=? WHERE run_id=?',
                   (time.time()-10,rid))
    assert runs.work_once()
    assert calls==[rid]
    assert runs.get(catalog.a,rid)['prediction_asset']['rows']==1
    assert runs.prepared._row(rid)['state']=='committed'


def test_heartbeat_renews_lease_while_a_step_is_busy(tmp_path,monkeypatch):
    from timepilot import model_experiment_control as control
    from timepilot import model_task_execution as engine
    monkeypatch.setattr(control,'LEASE_SECONDS',1)
    monkeypatch.setattr(control,'RENEW_SECONDS',.15)
    monkeypatch.setattr(engine,'LEASE_SECONDS',1)
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    row,owner,epoch=runs._claim()
    with runs.experiments.guard(row,owner,epoch) as guard:
        time.sleep(1.4)
        guard.ensure()
        assert runs._claim() is None, 'An active train step cannot be stolen'
        with catalog._db() as db:
            lease=db.execute('SELECT lease_deadline FROM model_task_runs WHERE run_id=?',
                             (rid,)).fetchone()['lease_deadline']
        assert lease > time.time() and epoch==1


def test_experiment_deadline_terminates_active_child_before_model_limit(tmp_path):
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    row,owner,epoch=runs._claim()
    with catalog._db() as db:
        db.execute('UPDATE model_experiment_controls SET deadline_epoch=? WHERE run_id=?',
                   (time.time()+.9,rid))
    started=time.monotonic()
    with runs.experiments.guard(row,owner,epoch) as guard:
        with pytest.raises(ExperimentBudgetExhausted,match='EXPERIMENT_BUDGET_EXHAUSTED'):
            supervise([sys.executable,'-c','import time;time.sleep(30)'],
                      seconds=20,max_cpu_seconds=20,max_rss_bytes=512*1024**2,
                      stop_check=guard.ensure)
    assert time.monotonic()-started<3, 'Outer experiment deadline must kill an active model child'


def test_live_budget_countdown_does_not_change_evidence_digest(tmp_path):
    catalog,_,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1))
    assert runs._claim() is not None
    first=unified.summary(catalog.a,tid)
    time.sleep(.08)
    second=unified.summary(catalog.a,tid)
    assert first['experiment']['remaining_wall_seconds'] > second['experiment']['remaining_wall_seconds']
    assert first['evidence_sha256'] == second['evidence_sha256']


def test_lease_is_shorter_than_total_experiment_budget(tmp_path):
    catalog,_,tasks,runs,_=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    rid=runs.submit(catalog.a,tid)['run_id']
    plan=runs.get(catalog.a,rid)['experiment']['budget']
    assert plan['lease_ttl_seconds']==60
    assert plan['lease_heartbeat_seconds']==5
    assert plan['run_wall_seconds_total'] > plan['lease_ttl_seconds']
    assert runs._claim() is not None
    with catalog._db() as db:
        deadline=db.execute('SELECT lease_deadline FROM model_task_runs WHERE run_id=?',(rid,)).fetchone()['lease_deadline']
    assert 0 < deadline-time.time() <= 60
