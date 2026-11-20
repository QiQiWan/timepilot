"""v0.39.3 unified run contract: real SQLite, sklearn, legacy PIT engine and API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.model_task_spec import ModelTaskInput, ApproveModelTask, ReplaceModelTask
from timepilot.model_task_unified_runs import StartUnifiedRun, ModelTaskUnifiedRuns
from timepilot.model_task_store import ModelTaskConflict, ModelTaskError
from test_model_task_execution_v0391 import CSVModeling, approved
from test_model_task_contract_v0390 import Catalog
from timepilot.model_task_store import ModelTaskStore
from timepilot.model_task_execution import ModelTaskRunService
from timepilot.model_task_temporal_bridge import TemporalTaskBridge
from test_modeling_workflow_v017 import prepare_bi, create_plan


def tabular(tmp_path):
    catalog=Catalog(tmp_path)
    model=CSVModeling(tmp_path)
    tasks=ModelTaskStore(catalog,model)
    runs=ModelTaskRunService(tasks,model)
    bridge=TemporalTaskBridge(tasks,model,runs)
    return catalog,model,tasks,runs,ModelTaskUnifiedRuns(tasks,runs,bridge)


def test_tabular_unified_end_to_end_same_run_receipt_and_report(tmp_path):
    catalog,_,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    before=unified.summary(catalog.a,tid)
    assert before['state']=='ready_to_run' and not before['metric_sets']
    request=StartUnifiedRun(expected_revision=1)
    first=unified.start(catalog.a,tid,request)
    second=unified.start(catalog.a,tid,request)
    assert first['run_id']==second['run_id']
    assert first['state']=='queued'
    assert len(unified.history(catalog.a,tid)['items'])==1
    assert runs.work_once()
    after=unified.summary(catalog.a,tid)
    assert after['state']=='succeeded' and after['report_available']
    assert after['metric_sets']['comparison']['forest']['mae'] is not None
    assert len(after['folds'])==3
    assert after['result_sha256'] and after['evidence_sha256']
    report=unified.report(catalog.a,tid)
    assert after['source_sha256'] in report
    assert after['evidence_sha256'] in report
    assert 'No demonstrated production benefit' in report
    assert 'raw predictions' in report
    with pytest.raises(KeyError):
        unified.summary(catalog.b,tid)


def test_old_result_disappears_after_revision_or_source_change(tmp_path):
    catalog,model,tasks,runs,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1))
    assert runs.work_once()
    assert unified.summary(catalog.a,tid)['report_available']
    old=tasks.get(catalog.a,tid)
    spec={k:v for k,v in old['spec'].items() if k not in ('asset','project_id','schema_version')}
    spec['name']='Updated contract after evaluation'
    tasks.replace(catalog.a,tid,ReplaceModelTask(expected_revision=1,
                                                 spec=ModelTaskInput.model_validate(spec)))
    after=unified.summary(catalog.a,tid)
    assert after['state']=='needs_review' and not after['metric_sets']
    assert not after['report_available']
    assert len(unified.history(catalog.a,tid)['items'])==1
    with pytest.raises(ModelTaskConflict): unified.report(catalog.a,tid)
    model.source_sha='d'*64
    assert unified.summary(catalog.a,tid)['state']=='stale'
    assert not unified.summary(catalog.a,tid)['result_sha256']


def test_tampered_submission_receipt_fails_closed(tmp_path):
    catalog,_,tasks,_,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1))
    with catalog._db() as db:
        db.execute("UPDATE model_task_execution_receipts SET run_id=? WHERE task_id=?",
                   ('00000000-0000-4000-8000-000000000999',tid))
    with pytest.raises(ModelTaskError,match='SHA256'):
        unified.summary(catalog.a,tid)


def test_revision_check_and_concurrent_requests_reuse_one_run(tmp_path):
    catalog,_,tasks,_,unified=tabular(tmp_path)
    tid=approved(tasks,catalog.a)
    with pytest.raises(ModelTaskConflict):
        unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=2))
    with ThreadPoolExecutor(max_workers=6) as pool:
        output=list(pool.map(lambda _ : unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1)),range(8)))
    assert len({v['run_id'] for v in output})==1
    assert len(unified.history(catalog.a,tid)['items'])==1


def _real_forecast(app,http):
    pid,other,aid,_,_=prepare_bi(app,http)
    root=f'/api/v1/projects/{pid}'
    req=ModelTaskInput.model_validate({
        'name':'Time series unified run','asset_id':aid,'task_kind':'continuous_forecast',
        'target':{'column':'温度(℃)','horizon_steps':3},'features':[],
        'time':{'event_time_column':'采集时间','entity_columns':['设备编号'],
                'frequency':'h','publication_lag_days':0},
        'validation':{'kind':'rolling_origin','folds':3}})
    saved=app.state.model_tasks.create(pid,req)
    tid=saved['task_id']
    assert app.state.model_tasks.preflight(pid,tid)['ready']
    draft=http.post(root+'/modeling/drafts',json=create_plan(aid=aid)).json()
    assert app.state.modeling.preflight(pid,draft['draft_id'])['ready']
    linked=http.post(root+f'/model-tasks/{tid}/temporal-link',json={
        'draft_id':draft['draft_id'],'expected_revision':1})
    assert linked.status_code==200,linked.text
    app.state.model_tasks.approve(pid,tid,ApproveModelTask(
        expected_revision=1,confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    return pid,other,tid,draft['draft_id']


def test_temporal_one_entry_executes_and_reuses_legacy_real_run(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,tid,did=_real_forecast(app,http)
        root=f'/api/v1/projects/{pid}/model-tasks/{tid}'
        assert http.get(root+'/execution').json()['state']=='needs_approval'
        deny=http.post(root+'/execution',json={'expected_revision':1})
        assert deny.status_code==409,deny.text
        created=http.post(root+'/execution',json={
            'expected_revision':1,'confirm_temporal_execution':True})
        assert created.status_code==202,created.text
        receipt=created.json()
        assert receipt['run_id'] and receipt['executor']=='existing_temporal_pit'
        assert receipt['state']=='queued'
        again=http.post(root+'/execution',json={'expected_revision':1})
        assert again.status_code==202,again.text
        assert again.json()['run_id']==receipt['run_id']
        assert app.state.projects.engine.work_once(receipt['run_id'])
        done=http.get(root+'/execution')
        assert done.status_code==200,done.text
        body=done.json()
        assert body['state']=='succeeded' and body['metric_sets']['test']
        assert body['validation_protocol']!='kfold' and len(body['folds'])>=3
        assert {f['fold'] for f in body['folds']}=={1,2,3}
        assert body['fold_label_maturity_certified'] and body['prediction_asset']['rows']>0
        assert body['comparison_protocol']['same_validation_rows_and_fold_indices']
        assert body['report_available'] is True
        report=http.get(root+'/execution/report')
        assert report.status_code==200 and 'text/markdown' in report.headers['content-type']
        assert 'MAE' in report.text
        assert http.get(f'/api/v1/projects/{other}/model-tasks/{tid}/execution').status_code==404
        # Existing draft/queue remains the authoritative record; no second run.
        assert http.get(root+'/execution/history').json()['items'][0]['run_id']==receipt['run_id']
        assert app.state.modeling.draft(pid,did)['run_id']==receipt['run_id']


def test_temporal_revision_and_link_failure_do_not_execute(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,tid,did=_real_forecast(app,http)
        root=f'/api/v1/projects/{pid}/model-tasks/{tid}'
        doc=app.state.model_tasks.get(pid,tid)
        body={k:v for k,v in doc['spec'].items() if k not in ('asset','project_id','schema_version')}
        body['name']='Changed after PIT draft binding'
        patch=http.patch(root,json={'expected_revision':1,'spec':body})
        assert patch.status_code==200,patch.text
        assert http.get(root+'/execution').json()['state']=='stale'
        assert http.post(root+'/execution',json={
            'expected_revision':1,'confirm_temporal_execution':True}).status_code==409
        with pytest.raises(ModelTaskConflict):app.state.unified_model_runs.report(pid,tid)
        assert app.state.modeling.draft(pid,did)['run_id'] is None
