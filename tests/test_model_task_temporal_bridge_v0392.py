"""Real project SQLite/API, PIT-aware temporal engine and unified safe evidence."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.model_task_spec import ModelTaskInput, ApproveModelTask, ReplaceModelTask
from timepilot.model_task_temporal_bridge import BindTemporalDraft
from test_modeling_workflow_v017 import prepare_bi, create_plan
from test_industrial_modeling_v018 import classification_payload, classification_csv


def _setup_forecast(http, app, pid, aid):
    root=f'/api/v1/projects/{pid}'
    req=ModelTaskInput.model_validate({
        'name':'Unified forecast for equipment','asset_id':aid,'task_kind':'continuous_forecast',
        'target':{'column':'温度(℃)','horizon_steps':3},'features':[],
        'time':{'event_time_column':'采集时间','entity_columns':['设备编号'],
                'frequency':'h','publication_lag_days':0},
        'validation':{'kind':'rolling_origin','folds':3}})
    tasks=app.state.model_tasks
    saved=tasks.create(pid,req)
    assert tasks.preflight(pid,saved['task_id'])['ready']
    payload=create_plan(aid=aid)
    draft=http.post(root+'/modeling/drafts',json=payload).json()
    assert app.state.modeling.preflight(pid,draft['draft_id'])['ready']
    return saved, draft, req


def test_temporal_forecast_link_approval_run_produces_unified_evidence(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,_=prepare_bi(app,http)
        task,draft,req=_setup_forecast(http,app,pid,aid)
        prefix=f'/api/v1/projects/{pid}/model-tasks/{task["task_id"]}'
        link=http.post(prefix+'/temporal-link',json={'draft_id':draft['draft_id'],'expected_revision':1})
        assert link.status_code==200,link.text
        assert link.json()['status']=='needs_approval'
        assert http.post(prefix+'/temporal-link',json={'draft_id':draft['draft_id'],'expected_revision':1}).status_code==200
        assert http.get(prefix+'/evidence').json()['status']=='needs_approval'
        app.state.model_tasks.approve(pid,task['task_id'],ApproveModelTask(
            expected_revision=1,confirm_target=True,confirm_feature_availability=True,confirm_split=True))
        path=f'/api/v1/projects/{pid}/modeling/drafts/{draft["draft_id"]}'
        approved=http.post(path+'/approve',json={'expected_revision':1,'confirm':True,
            'acknowledge_single_target':True,'acknowledge_availability':True})
        assert approved.status_code==200,approved.text
        assert http.get(prefix+'/temporal-link').json()['status']=='linked'
        assert http.get(prefix+'/evidence').json()['status']=='not_submitted'
        materialized=http.post(path+'/materialize')
        assert materialized.status_code==201,materialized.text
        submitted=http.post(path+'/submit')
        assert submitted.status_code==201,submitted.text
        assert app.state.projects.engine.work_once(submitted.json()['run_id'])
        record=http.get(prefix+'/evidence')
        assert record.status_code==200,record.text
        doc=record.json()
        assert doc['status']=='succeeded'
        assert doc['validation_protocol']!='kfold'
        assert doc['evidence_level']=='independent_temporal_test'
        assert doc['metrics'] and doc['validation_metrics']
        assert doc['business_benefit_proven'] is False
        assert 'predictions' not in doc and 'test' not in doc
        assert doc['evidence_sha256']
        assert http.get(f'/api/v1/projects/{other}/model-tasks/{task["task_id"]}/evidence').status_code==404
        # A new unified spec revision makes the old approved run historical.
        app.state.model_tasks.replace(pid,task['task_id'],ReplaceModelTask(
            expected_revision=1,spec=ModelTaskInput.model_validate({**req.model_dump(mode='json'),
                'name':'Revised forecast'})))
        assert http.get(prefix+'/temporal-link').json()['status']=='stale'
        assert http.get(prefix+'/evidence').json()['metrics'] is None


def test_filtered_legacy_draft_cannot_misrepresent_full_asset_scope(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http)
        task,_,_= _setup_forecast(http,app,pid,aid)
        payload=create_plan(aid=aid,filters=[{'column':'设备编号','op':'eq','value':'设备-A'}])
        draft=http.post(f'/api/v1/projects/{pid}/modeling/drafts',json=payload).json()
        assert app.state.modeling.preflight(pid,draft['draft_id'])['ready']
        reply=http.post(f'/api/v1/projects/{pid}/model-tasks/{task["task_id"]}/temporal-link',
                        json={'draft_id':draft['draft_id'],'expected_revision':1})
        assert reply.status_code==409
        assert 'Filtered' in reply.text


def test_classification_label_maturity_and_positive_mapping_are_exact(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,classification_csv())
        payload=classification_payload(aid)
        legacy=http.post(f'/api/v1/projects/{pid}/modeling/drafts',json=payload).json()
        assert app.state.modeling.preflight(pid,legacy['draft_id'])['ready']
        req=ModelTaskInput.model_validate({
            'name':'Early defect decision','asset_id':aid,'task_kind':'classification',
            'target':{'column':'是否不良','positive_values':['NG'],'negative_values':['OK']},
            'features':[{'column':'锡膏厚度','role':'observed_past'},
                        {'column':'负压','role':'observed_past'}],
            'time':{'event_time_column':'节点时间','entity_columns':['设备'],
                    'frequency':'h','publication_lag_days':0,
                    'label_available_at_column':'标签确认时间'},
            'validation':{'kind':'temporal_holdout','folds':2}})
        task=app.state.model_tasks.create(pid,req)
        assert app.state.model_tasks.preflight(pid,task['task_id'])['ready']
        path=f'/api/v1/projects/{pid}/model-tasks/{task["task_id"]}'
        ok=http.post(path+'/temporal-link',json={'draft_id':legacy['draft_id'],'expected_revision':1})
        assert ok.status_code==200,ok.text
        assert ok.json()['status']=='needs_approval'
        # Swapping the meaning of the positive class is never allowed.
        wrong=ModelTaskInput.model_validate({**req.model_dump(mode='json'),
            'target':{'column':'是否不良','positive_values':['OK'],'negative_values':['NG']}})
        app.state.model_tasks.replace(pid,task['task_id'],ReplaceModelTask(expected_revision=1,spec=wrong))
        assert http.get(path+'/temporal-link').json()['status']=='stale'


def test_unified_tabular_evidence_has_no_unsafe_deployment_claim(tmp_path):
    from test_model_task_execution_v0391 import CSVModeling, approved
    from test_model_task_contract_v0390 import Catalog
    from timepilot.model_task_store import ModelTaskStore
    from timepilot.model_task_execution import ModelTaskRunService
    from timepilot.model_task_temporal_bridge import TemporalTaskBridge
    catalog=Catalog(tmp_path)
    modeling=CSVModeling(tmp_path)
    tasks=ModelTaskStore(catalog,modeling)
    runs=ModelTaskRunService(tasks,modeling)
    bridge=TemporalTaskBridge(tasks,modeling,runs)
    tid=approved(tasks,catalog.a)
    before=bridge.evidence(catalog.a,tid)
    assert before['status']=='not_submitted' and before['metrics'] is None
    queued=runs.submit(catalog.a,tid)
    assert runs.work_once()
    finished=bridge.evidence(catalog.a,tid)
    assert finished['status']=='succeeded' and finished['metrics']
    assert finished['executor']=='sklearn_tabular'
    assert finished['business_benefit_proven'] is False
    assert 'predictions' not in finished


def test_idempotent_same_spec_replace_keeps_approval_and_evidence(tmp_path):
    from test_model_task_contract_v0390 import Catalog, FakeModeling, input_for
    from timepilot.model_task_store import ModelTaskStore
    catalog=Catalog(tmp_path)
    model=FakeModeling()
    tasks=ModelTaskStore(catalog,model)
    spec=input_for('tabular_regression')
    saved=tasks.create(catalog.a,spec)
    assert tasks.preflight(catalog.a,saved['task_id'])['ready']
    old=tasks.approve(catalog.a,saved['task_id'],ApproveModelTask(
        expected_revision=1,confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    repeated=tasks.replace(catalog.a,saved['task_id'],ReplaceModelTask(expected_revision=1,spec=spec))
    assert repeated['revision']==1
    assert repeated['status']=='approved'
    assert repeated['approval_sha256']==old['approval_sha256']
    assert len(tasks.events(catalog.a,saved['task_id']))==3


def test_source_sha_rebind_during_edit_is_denied(tmp_path):
    from test_model_task_contract_v0390 import Catalog, FakeModeling, input_for
    from timepilot.model_task_store import ModelTaskStore, ModelTaskConflict
    catalog=Catalog(tmp_path)
    model=FakeModeling()
    tasks=ModelTaskStore(catalog,model)
    spec=input_for('tabular_regression')
    saved=tasks.create(catalog.a,spec)
    model.source_sha='c'*64
    with pytest.raises(ModelTaskConflict,match='source SHA'):
        tasks.replace(catalog.a,saved['task_id'],ReplaceModelTask(expected_revision=1,spec=spec))
    assert tasks.get(catalog.a,saved['task_id'])['source_state']=='stale_source'


def test_early_classification_real_training_metrics_are_read_only_and_pit_scoped(tmp_path):
    """Production-like binary decision workflow: real 230-row legacy test with unified evidence."""
    app = create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,classification_csv())
        root=f'/api/v1/projects/{pid}'
        draft_response=http.post(root+'/modeling/drafts',json=classification_payload(aid))
        assert draft_response.status_code==201,draft_response.text
        draft_id=draft_response.json()['draft_id']
        assert app.state.modeling.preflight(pid,draft_id)['ready']
        task_request=ModelTaskInput.model_validate({
            'name':'Early quality classification','asset_id':aid,'task_kind':'classification',
            'target':{'column':'是否不良','positive_values':['NG'],'negative_values':['OK']},
            'features':[{'column':'锡膏厚度','role':'observed_past'},
                        {'column':'负压','role':'observed_past'}],
            'time':{'event_time_column':'节点时间','entity_columns':['设备'],
                    'frequency':'h','publication_lag_days':0,
                    'label_available_at_column':'标签确认时间'},
            'validation':{'kind':'temporal_holdout','folds':2}})
        saved=app.state.model_tasks.create(pid,task_request)
        task_id=saved['task_id']
        assert app.state.model_tasks.preflight(pid,task_id)['ready']
        prefix=f'{root}/model-tasks/{task_id}'
        bound=http.post(prefix+'/temporal-link',json={'draft_id':draft_id,'expected_revision':1})
        assert bound.status_code==200,bound.text
        app.state.model_tasks.approve(pid,task_id,ApproveModelTask(
            expected_revision=1,confirm_target=True,confirm_feature_availability=True,
            confirm_split=True))
        old=f'{root}/modeling/drafts/{draft_id}'
        approved=http.post(old+'/approve',json={'expected_revision':1,'confirm':True,
            'acknowledge_single_target':True,'acknowledge_availability':True})
        assert approved.status_code==200,approved.text
        assert http.post(old+'/materialize').status_code==201
        submitted=http.post(old+'/submit')
        assert submitted.status_code==201,submitted.text
        assert app.state.projects.engine.work_once(submitted.json()['run_id'])
        response=http.get(prefix+'/evidence')
        assert response.status_code==200,response.text
        evidence=response.json()
        assert evidence['status']=='succeeded'
        assert evidence['validation_protocol']=='chronological_matured_labels_v1'
        assert evidence['metrics']['recall']>=0
        assert evidence['validation_metrics']['recall']>=0
        assert evidence['business_benefit_proven'] is False
        assert 'predictions' not in evidence
        assert evidence['evidence_sha256']


def test_temporal_contract_cannot_silently_drop_per_feature_pit_or_embargo(tmp_path):
    """The original temporal model has a shared publication policy only."""
    from timepilot.model_task_store import ModelTaskConflict
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,classification_csv())
        path=f'/api/v1/projects/{pid}/modeling/drafts'
        response=http.post(path,json=classification_payload(aid))
        assert response.status_code==201,response.text
        draft=app.state.modeling.preflight(pid,response.json()['draft_id'])
        assert draft['ready']
        original=ModelTaskInput.model_validate({
            'name':'Temporal availability review','asset_id':aid,'task_kind':'classification',
            'target':{'column':'是否不良','positive_values':['NG'],'negative_values':['OK']},
            'features':[{'column':'锡膏厚度','role':'observed_past'},
                        {'column':'负压','role':'observed_past'}],
            'time':{'event_time_column':'节点时间','entity_columns':['设备'],
                    'frequency':'h','publication_lag_days':0,
                    'label_available_at_column':'标签确认时间'},
            'validation':{'kind':'temporal_holdout','folds':2}})
        saved=app.state.model_tasks.create(pid,original)
        task=app.state.model_tasks.get(pid,saved['task_id'])
        draft=app.state.modeling.draft(pid,response.json()['draft_id'])
        bridge=app.state.temporal_model_bridge
        # These options fit a new unified schema, but have no equivalent
        # execution guarantee in the original classification draft.
        mutations=[
            ({'features':[{'column':'锡膏厚度','role':'known_future'},
                           {'column':'负压','role':'observed_past'}]},'future-known'),
            ({'features':[{'column':'锡膏厚度','role':'observed_past',
                           'available_at_column':'标签确认时间'},
                           {'column':'负压','role':'observed_past'}]},'Per-feature'),
            ({'time':{**original.time.model_dump(),'decision_time_column':'标签确认时间'}},'decision clock'),
            ({'validation':{'kind':'temporal_holdout','folds':2,'gap':1}},'embargo gap'),
            ({'validation':{'kind':'temporal_holdout','folds':3}},'window count'),
        ]
        for changes,warning in mutations:
            candidate=ModelTaskInput.model_validate({**original.model_dump(mode='json'),**changes})
            modified={**task,'spec':{**task['spec'],**candidate.model_dump(mode='json')}}
            with pytest.raises(ModelTaskConflict,match=warning):
                bridge._verify(modified,draft)
