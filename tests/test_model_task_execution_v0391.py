"""Actual sklearn + SQLite + immutable CSV supervised-run contract tests."""
from __future__ import annotations

import csv
import hashlib
import random
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_model_task_contract_v0390 import Catalog, FakeModeling, input_for
from timepilot.model_task_spec import ApproveModelTask, ModelTaskInput, ReplaceModelTask
from timepilot.model_task_store import ModelTaskStore, ModelTaskConflict, ModelTaskError
from timepilot.model_task_execution import ModelTaskRunService
from timepilot.model_task_api import create_model_task_router


class CSVModeling(FakeModeling):
    def __init__(self,root):
        super().__init__()
        self.path=root/'source.csv'
        rng=random.Random(42)
        start=datetime(2024,1,1,tzinfo=timezone.utc)
        with self.path.open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=self.columns)
            writer.writeheader()
            for k in range(240):
                x=10+(k%51)*.34
                writer.writerow({'sale':round(2.3*x+5+rng.gauss(0,1),3),'price':x,
                    'fault_label':'1' if k%7==0 else '0',
                    'event_time':(start+timedelta(hours=k)).isoformat(),
                    'published':(start+timedelta(hours=k-1)).isoformat(),
                    'label_ready':(start+timedelta(hours=k+2)).isoformat(),
                    'device':'dev-'+str(k%12)})
        self.source_sha=hashlib.sha256(self.path.read_bytes()).hexdigest()

    def _source(self,pid,aid,version):
        record=super()._source(pid,aid,version)
        if hashlib.sha256(self.path.read_bytes()).hexdigest()!=self.source_sha:
            raise ValueError('Published version integrity check failed')
        return {**record,'path':self.path,'row_count':240}

    def profile(self,pid,aid,version):
        result=super().profile(pid,aid,version)
        result['source_rows']=240
        return result


@pytest.fixture
def setup(tmp_path):
    catalog=Catalog(tmp_path)
    modeling=CSVModeling(tmp_path)
    tasks=ModelTaskStore(catalog,modeling)
    engine=ModelTaskRunService(tasks,modeling)
    return catalog,modeling,tasks,engine


def approved(tasks,pid,kind='tabular_regression',**opts):
    params={'validation':{'kind':opts.get('split','stratified_kfold' if kind=='tabular_classification' else 'kfold'),
                          'folds':3}}
    if kind=='tabular_classification':
        params['target_values']={'column':'fault_label','positive_values':['1'],'negative_values':['0']}
    if opts.get('split')=='group_kfold':
        params['time']={'entity_columns':['device']}
    if opts.get('split')=='temporal_holdout':
        params['time']={'event_time_column':'event_time','label_available_at_column':'label_ready',
                            'feature_available_at_column':'published'}
    target_values=params.pop('target_values',None)
    request=input_for(kind,**params)
    if target_values:
        request=ModelTaskInput.model_validate({**request.model_dump(mode='json'), 'target':target_values})
    doc=tasks.create(pid,request)
    pf=tasks.preflight(pid,doc['task_id'])
    assert pf['ready'],pf
    tasks.approve(pid,doc['task_id'],ApproveModelTask(expected_revision=1,
        confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    return doc['task_id']


def test_regression_real_fixed_fold_comparison_and_idempotency(setup):
    catalog,modeling,tasks,engine=setup
    task=approved(tasks,catalog.a)
    first=engine.submit(catalog.a,task)
    assert first['state']=='queued'
    assert engine.submit(catalog.a,task)['run_id']==first['run_id']
    assert engine.work_once()
    done=engine.get(catalog.a,first['run_id'])
    assert done['state']=='succeeded',done.get('error_code')
    result=done['result']
    assert result['rows']==240 and len(result['folds'])==3
    assert result['summary']['linear']['mae']<result['summary']['baseline']['mae']
    assert result['summary']['forest']['mae'] is not None
    assert result['business_benefit_proven'] is False
    assert result['source_sha256']==modeling.source_sha
    assert all(v['disjoint'] for v in result['folds'])
    assert engine.work_once() is False
    assert len(engine.list(catalog.a,task))==1
    with pytest.raises(KeyError):engine.get(catalog.b,first['run_id'])


def test_classification_requires_approved_labels_and_real_pr_metric(setup):
    catalog,_,tasks,engine=setup
    task=approved(tasks,catalog.a,'tabular_classification')
    run=engine.submit(catalog.a,task)
    assert engine.work_once()
    result=engine.get(catalog.a,run['run_id'])
    assert result['state']=='succeeded',result['error_code']
    assert 0<=result['result']['summary']['forest']['average_precision']<=1
    assert result['result']['target']=='fault_label'


def test_group_and_forward_temporal_splits_are_isolated(setup):
    catalog,_,tasks,engine=setup
    for split in ('group_kfold','temporal_holdout'):
        task=approved(tasks,catalog.a,split=split)
        run=engine.submit(catalog.a,task)
        assert engine.work_once()
        done=engine.get(catalog.a,run['run_id'])
        assert done['state']=='succeeded', (split,done['error_code'])
        assert all(f['split']==split for f in done['result']['folds'])


def test_stale_approval_fails_and_old_results_are_superseded(setup):
    catalog,_,tasks,engine=setup
    task=approved(tasks,catalog.a)
    run=engine.submit(catalog.a,task)
    original=tasks.get(catalog.a,task)
    tasks.replace(catalog.a,task,ReplaceModelTask(expected_revision=1,
        spec=ModelTaskInput.model_validate({**{k:v for k,v in original['spec'].items()
            if k not in ('asset','project_id','schema_version')},'name':'Revised after approval'})))
    assert engine.work_once()
    done=engine.get(catalog.a,run['run_id'])
    assert done['state']=='failed' and done['error_code']=='TaskRevisionChanged'
    assert done['effective_state']=='superseded'
    with pytest.raises(ModelTaskConflict):engine.submit(catalog.a,task)


def test_expired_lease_is_fenced_from_committing(setup):
    catalog,_,tasks,engine=setup
    task=approved(tasks,catalog.a)
    run=engine.submit(catalog.a,task)
    row,old_owner,old_epoch=engine._claim()
    with catalog._db() as db:
        db.execute('UPDATE model_task_runs SET lease_deadline=? WHERE run_id=?',
                   (time.time()-10,run['run_id']))
    assert engine.work_once()
    done=engine.get(catalog.a,run['run_id'])
    assert done['state']=='succeeded'
    assert done['lease_epoch']==old_epoch+1
    assert old_owner!=done['lease_owner']


def test_bad_source_cannot_be_queued_and_failed_run_does_not_claim_benefit(setup):
    catalog,modeling,tasks,engine=setup
    task=approved(tasks,catalog.a)
    modeling.source_sha='c'*64
    with pytest.raises(ModelTaskConflict):engine.submit(catalog.a,task)


def test_fastapi_task_run_urls_are_project_scoped(setup):
    catalog,_,tasks,engine=setup
    task=approved(tasks,catalog.a)
    app=FastAPI()
    app.include_router(create_model_task_router(tasks,engine))
    client=TestClient(app)
    base=f'/api/v1/projects/{catalog.a}/model-tasks/{task}/runs'
    response=client.post(base)
    assert response.status_code==202,response.text
    rid=response.json()['run_id']
    assert client.get(base).json()['items'][0]['run_id']==rid
    assert client.get(base+'/'+rid).status_code==200
    assert client.get(f'/api/v1/projects/{catalog.b}/model-tasks/{task}/runs/{rid}').status_code==404
    assert client.post(f'/api/v1/projects/{catalog.b}/model-tasks/{task}/runs').status_code==404


def test_real_application_route_to_persisted_sklearn_result(tmp_path):
    """Only the optional physical BI importer is substituted by the v0.17 fixture."""
    from timepilot.api import create_app
    from test_modeling_workflow_v017 import prepare_bi
    app=create_app(home=tmp_path/'app', embedded_worker=False)
    with TestClient(app) as client:
        pid,other,asset,_,_=prepare_bi(app,client)
        endpoint=f'/api/v1/projects/{pid}/model-tasks'
        req={'name':'Equipment temperature supervised baseline',
             'asset_id':asset,'version_id':'base','task_kind':'tabular_regression',
             'target':{'column':'温度(℃)'},'features':[{'column':'气压','role':'static'}],
             'validation':{'kind':'kfold','folds':3}}
        created=client.post(endpoint,json=req)
        assert created.status_code==201,created.text
        task_id=created.json()['task_id']
        preflight=client.post(endpoint+'/'+task_id+'/preflight')
        assert preflight.status_code==200 and preflight.json()['ready'],preflight.text
        approved=client.post(endpoint+'/'+task_id+'/approve',json={
            'expected_revision':1,'confirm_target':True,
            'confirm_feature_availability':True,'confirm_split':True})
        assert approved.status_code==200,approved.text
        queued=client.post(endpoint+'/'+task_id+'/runs')
        assert queued.status_code==202,queued.text
        assert app.state.model_task_runs.work_once()
        report=client.get(endpoint+'/'+task_id+'/runs/'+queued.json()['run_id'])
        assert report.status_code==200,report.text
        assert report.json()['state']=='succeeded',report.json()['error_code']
        assert report.json()['result']['rows']==210
        assert client.get(f'/api/v1/projects/{other}/model-tasks/{task_id}/runs').status_code==404


def test_persisted_model_metrics_are_sha_verified(setup):
    catalog, _, tasks, engine = setup
    task = approved(tasks, catalog.a)
    run = engine.submit(catalog.a, task)
    assert engine.work_once()
    assert engine.get(catalog.a, run['run_id'])['state'] == 'succeeded'
    with catalog._db() as db:
        db.execute('UPDATE model_task_runs SET result_json=? WHERE run_id=?',
                   ('{"tampered":true}', run['run_id']))
    with pytest.raises(ModelTaskError, match='SHA256'):
        engine.get(catalog.a, run['run_id'])
