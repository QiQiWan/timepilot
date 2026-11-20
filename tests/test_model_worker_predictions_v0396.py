"""v0.39.6: isolated worker, signed temporal folds and immutable BI row lineage."""
from __future__ import annotations

import hashlib
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from timepilot.api import create_app
from timepilot.model_fit_isolation import ModelBudgetExceeded, supervise
from timepilot.model_task_spec import ApproveModelTask,ModelTaskInput,ReplaceModelTask
from timepilot.model_task_unified_runs import StartUnifiedRun
from timepilot.model_task_store import ModelTaskError,ModelTaskConflict
from test_model_task_unified_runs_v0393 import _real_forecast
from test_model_task_time_and_results_v0395 import prepare,complete
from test_modeling_workflow_v017 import prepare_bi
from test_industrial_modeling_v018 import classification_payload,classification_csv


def test_child_wall_budget_is_enforced_and_process_does_not_survive():
    began=time.monotonic()
    with pytest.raises(ModelBudgetExceeded,match='MODEL_WALL_TIMEOUT'):
        supervise([sys.executable,'-c','import time;time.sleep(8)'],
                  seconds=.3,max_cpu_seconds=8,max_rss_bytes=512*1024**2)
    assert time.monotonic()-began<2


def test_child_memory_budget_kills_and_returns_actionable_code():
    began=time.monotonic()
    with pytest.raises(ModelBudgetExceeded,match='MODEL_RSS_LIMIT'):
        supervise([sys.executable,'-c',
                   'import time; b=bytearray(160*1024**2);time.sleep(8)'],
                   seconds=8,max_cpu_seconds=8,max_rss_bytes=96*1024**2)
    assert time.monotonic()-began<3


def test_tabular_asset_is_model_fold_pinned_and_fails_closed_on_corruption(tmp_path):
    catalog,model,tasks,runs,unified,tid=prepared=prepare(tmp_path,'rolling_origin')
    done=complete(prepared)
    asset=done['prediction_asset']
    assert asset['rows']>0 and len(asset['manifest_sha256'])==64
    assert asset['source_sha256']==done['source_sha256']
    samples=unified.prediction_rows(catalog.a,tid,model='forest',fold=1,limit=7)
    assert len(samples['items'])==7
    assert all(x['model']=='forest' and int(x['fold'])==1 for x in samples['items'])
    assert samples['next_offset']==7
    assert all(len(x['source_row_sha256'])==64 for x in samples['items'])
    assert unified.summary(catalog.a,tid)['prediction_asset']['asset_id']==asset['asset_id']
    with pytest.raises(KeyError):unified.prediction_rows(catalog.b,tid,limit=2)
    csv_path,_=unified.predictions.source_file(catalog.a,tid,done['run_id'],asset['asset_id'])
    original=csv_path.read_bytes()
    csv_path.write_bytes(original+b'\n')
    with pytest.raises(ModelTaskError,match='checksum'):
        unified.predictions.get(catalog.a,tid,done['run_id'])
    csv_path.write_bytes(original)
    assert unified.predictions.get(catalog.a,tid,done['run_id'])['rows']==asset['rows']
    original_spec=tasks.get(catalog.a,tid)
    payload={key:value for key,value in original_spec['spec'].items()
             if key not in ('asset','project_id','schema_version')}
    payload['name']='Updated source contract'
    tasks.replace(catalog.a,tid,ReplaceModelTask(expected_revision=1,
                    spec=ModelTaskInput.model_validate(payload)))
    with pytest.raises(ModelTaskConflict):unified.prediction_rows(catalog.a,tid)


def test_temporal_running_data_are_reused_in_native_perspective_asset(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,tid,_=_real_forecast(app,http)
        start=app.state.unified_model_runs.start(pid,tid,StartUnifiedRun(
            expected_revision=1,confirm_temporal_execution=True))
        assert start['state']=='queued'
        assert app.state.projects.engine.work_once(start['run_id'])
        base=f'/api/v1/projects/{pid}/model-tasks/{tid}/execution'
        result=http.get(base)
        assert result.status_code==200,result.text
        doc=result.json()
        assert doc['state']=='succeeded' and doc['diagnostics_available']
        assert doc['fold_label_maturity_certified']
        assert {f['fold'] for f in doc['folds']}=={1,2,3}
        assert len(doc['folds'])==6
        assert doc['comparison_protocol']['same_validation_rows_and_fold_indices']
        assert all(f['train_index_sha256'] and f['validation_index_sha256'] for f in doc['folds'])
        assert 'comparison' in doc['metric_sets']
        asset=doc['prediction_asset']
        assert asset['perspective_ready'] is True
        assert asset['rows']==sum(f['validation_rows'] for f in doc['folds'])*len(doc['available_models'])
        meta=http.get(base+'/predictions')
        assert meta.status_code==200 and meta.json()['asset_id']==asset['asset_id']
        records=http.get(base+'/predictions/rows?model=last_value&fold=1&limit=3')
        assert records.status_code==200,records.text
        assert len(records.json()['items'])==3
        assert all(x['model']=='last_value' and int(x['fold'])==1 for x in records.json()['items'])
        assert http.get(f'/api/v1/projects/{other}/model-tasks/{tid}/execution/predictions').status_code==404
        diag=http.get(base+'/diagnostics?model=last_value&dimension=device')
        assert diag.status_code==200,diag.text
        assert diag.json()['status']=='available' and len(diag.json()['fold_scores'])==6
        report=http.get(base+'/report')
        assert report.status_code==200 and 'Signed chronological fold evidence' in report.text
        path,parquet=app.state.unified_model_runs.predictions.source_file(pid,tid,doc['run_id'],asset['asset_id'])
        assert path.is_file() and not parquet
        assert hashlib.sha256(path.read_bytes()).hexdigest()==asset['csv_sha256']
        # Cross-project and invalid origin are closed before any DuckDB import.
        ws=f'/api/v1/native-bi/model-predictions/{pid}/{tid}/{asset["asset_id"]}/ws?project_id={other}'
        with pytest.raises(WebSocketDisconnect) as ws_error:
            with http.websocket_connect(ws,headers={'origin':'http://testserver'}):pass
        assert ws_error.value.code==1008
        # Signed temporal stage bytes cannot be modified after publication.
        stage=app.state.projects.engine.run_dir(start['run_id'])/'stages'/'backtest_result.json'
        with stage.open('a',encoding='utf-8') as fp:fp.write(' ')
        with pytest.raises(ModelTaskError,match='SHA256'):
            app.state.unified_model_runs.summary(pid,tid)


def test_classification_time_folds_and_confusion_are_paired(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,_=prepare_bi(app,http,classification_csv())
        root=f'/api/v1/projects/{pid}'
        legacy=http.post(root+'/modeling/drafts',json=classification_payload(aid)).json()
        assert app.state.modeling.preflight(pid,legacy['draft_id'])['ready']
        req=ModelTaskInput.model_validate({
            'name':'Early defect decision','asset_id':aid,'task_kind':'classification',
            'target':{'column':'\u662f\u5426\u4e0d\u826f','positive_values':['NG'],'negative_values':['OK']},
            'features':[{'column':'\u9521\u818f\u539a\u5ea6','role':'observed_past'},
                        {'column':'\u8d1f\u538b','role':'observed_past'}],
            'time':{'event_time_column':'\u8282\u70b9\u65f6\u95f4','entity_columns':['\u8bbe\u5907'],
                    'frequency':'h','publication_lag_days':0,
                    'label_available_at_column':'\u6807\u7b7e\u786e\u8ba4\u65f6\u95f4'},
            'validation':{'kind':'temporal_holdout','folds':2}})
        tasks=app.state.model_tasks
        saved=tasks.create(pid,req)
        tid=saved['task_id']
        assert tasks.preflight(pid,tid)['ready']
        response=http.post(f'{root}/model-tasks/{tid}/temporal-link',json={
            'draft_id':legacy['draft_id'],'expected_revision':1})
        assert response.status_code==200,response.text
        tasks.approve(pid,tid,ApproveModelTask(expected_revision=1,
            confirm_target=True,confirm_feature_availability=True,confirm_split=True))
        queued=app.state.unified_model_runs.start(pid,tid,StartUnifiedRun(
            expected_revision=1,confirm_temporal_execution=True))
        assert queued['state']=='queued'
        assert app.state.projects.engine.work_once(queued['run_id'])
        summary=app.state.unified_model_runs.summary(pid,tid)
        assert summary['state']=='succeeded',summary
        assert len(summary['folds'])==2 and summary['fold_label_maturity_certified']
        assert summary['prediction_asset']['rows']==sum(x['validation_rows'] for x in summary['folds'])
        diag=app.state.unified_model_runs.diagnostics(pid,tid,model='binary_logistic')
        assert len(diag['fold_diagnostics'])==2
        for fold,record in zip(summary['folds'],diag['fold_diagnostics']):
            assert record['tn']+record['fp']+record['fn']+record['tp']==fold['validation_rows']
        assert all(f['latest_train_event_utc']<f['first_validation_event_utc'] for f in summary['folds'])
        assert http.get(f'/api/v1/projects/{other}/model-tasks/{tid}/execution/predictions').status_code==404
