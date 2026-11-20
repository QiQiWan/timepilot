"""v0.39.4/5: actual source timestamp, split, same-budget, UI evidence acceptance."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from timepilot.model_task_execution import ModelTaskRunService
from timepilot.model_task_spec import ModelTaskInput, ApproveModelTask, ReplaceModelTask
from timepilot.model_task_store import ModelTaskStore, ModelTaskConflict, ModelTaskError
from timepilot.model_task_unified_runs import ModelTaskUnifiedRuns, StartUnifiedRun
from timepilot.model_task_temporal_bridge import TemporalTaskBridge
from timepilot.model_task_time_audit import audit_availability
from timepilot.model_task_api import create_model_task_router
from test_model_task_contract_v0390 import Catalog, input_for
from test_model_task_execution_v0391 import CSVModeling


def prepare(tmp_path,kind='rolling_origin',folds=3,classification=False):
    catalog=Catalog(tmp_path)
    modeling=CSVModeling(tmp_path)
    with modeling.path.open(newline='',encoding='utf-8') as handle:
        rows=list(csv.DictReader(handle))
    t0=datetime(2024,1,1,tzinfo=timezone.utc)
    # Eight complete months with repeated equipment identities.
    for i,row in enumerate(rows):
        when=t0+timedelta(days=i)
        row['event_time']=when.isoformat()
        row['published']=(when-timedelta(hours=1)).isoformat()
        row['label_ready']=(when+timedelta(hours=2)).isoformat()
    with modeling.path.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=modeling.columns)
        writer.writeheader();writer.writerows(rows)
    modeling.source_sha=hashlib.sha256(modeling.path.read_bytes()).hexdigest()
    tasks=ModelTaskStore(catalog,modeling)
    runs=ModelTaskRunService(tasks,modeling)
    bridge=TemporalTaskBridge(tasks,modeling,runs)
    unified=ModelTaskUnifiedRuns(tasks,runs,bridge)
    time={'event_time_column':'event_time','label_available_at_column':'label_ready',
          'feature_available_at_column':'published','timezone':'Asia/Shanghai'}
    if kind=='leave_device_out':time['entity_columns']=['device']
    spec=input_for('tabular_classification' if classification else 'tabular_regression',
        validation={'kind':kind,'folds':folds},time=time)
    if classification:
        payload=spec.model_dump(mode='json')
        payload['target']={'column':'fault_label',
                          'positive_values':['1'],'negative_values':['0']}
        spec=ModelTaskInput.model_validate(payload)
    doc=tasks.create(catalog.a,spec)
    pf=tasks.preflight(catalog.a,doc['task_id'])
    assert pf['ready'],pf
    tasks.approve(catalog.a,doc['task_id'],ApproveModelTask(expected_revision=1,
        confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    return catalog,modeling,tasks,runs,unified,doc['task_id']


def complete(prepared):
    catalog,_,_,runs,unified,tid=prepared
    before=unified.start(catalog.a,tid,StartUnifiedRun(expected_revision=1))
    assert before['state']=='queued'
    assert runs.work_once()
    done=unified.summary(catalog.a,tid)
    assert done['state']=='succeeded',done
    return done


@pytest.mark.parametrize('kind',[ 'rolling_origin','leave_month_out','leave_device_out'])
def test_actual_rolling_month_and_device_folds_same_budget(tmp_path,kind):
    prepared=prepare(tmp_path,kind)
    done=complete(prepared)
    folds=done['folds']
    assert len(folds)==3
    assert done['availability_ledger']['status']=='checked'
    assert done['availability_ledger']['features'][0]['late_rows']==0
    assert done['comparison_protocol']['same_source'] is True
    assert done['comparison_protocol']['same_soft_resource_envelope'] is True
    assert done['comparison_protocol']['equal_realized_compute'] is False
    for item in folds:
        assert item['latest_train_event_utc']<item['first_validation_event_utc']
        assert item['training_labels_mature_at_cutoff'] is True
        assert item['shared_by_all_models'] is True
        if kind=='leave_device_out':
            assert item['heldout_groups'] and item['train_groups_sha256']!=item['validation_groups_sha256']
    catalog,_,tasks,runs,unified,tid=prepared
    diag=unified.diagnostics(catalog.a,tid,model='forest',dimension='month')
    assert diag['status']=='available'
    assert len(diag['fold_scores'])==3 and len(diag['fold_diagnostics'])==3
    assert diag['budget']['cpu_threads']==1
    assert len(diag['fit_seconds'])==3
    assert diag['metric_provenance']['result_sha256']==done['result_sha256']
    assert diag['available_dimensions']
    assert diag['slices']
    assert diag['feature_contribution'] is None or diag['feature_contribution']['status'] in (
        'validation_only_permutation','not_available_for_constant_baseline')
    assert 'PIT ledger SHA256' in unified.report(catalog.a,tid)
    assert 'Same-fold protocol SHA256' in unified.report(catalog.a,tid)
    report=unified.report(catalog.a,tid)
    assert 'Paired fold evaluation' in report and 'Comparison budget and environment' in report
    assert 'Native BI validation slices' in report and 'Feature contribution' in report
    assert 'raw predictions' in report and 'Per-X availability ledger' in report
    assert all(x['source']['kind']=='source_column' for x in done['availability_ledger']['features'])


def test_binary_confusion_per_fold_and_device_slice(tmp_path):
    prepared=prepare(tmp_path,'leave_device_out',classification=True)
    done=complete(prepared)
    catalog,_,_,_,unified,tid=prepared
    diag=unified.diagnostics(catalog.a,tid,model='forest',dimension='device')
    assert diag['status']=='available'
    assert len(diag['fold_diagnostics'])==3
    assert 'confusion_matrix' in unified.report(catalog.a,tid)
    for fold,record in zip(done['folds'],diag['fold_diagnostics']):
        assert record['tp']+record['tn']+record['fp']+record['fn']==fold['validation_rows']
    # Device-level values have <15 validation records and must be suppressed.
    assert not diag['slices']
    by_month=unified.diagnostics(catalog.a,tid,model='forest',dimension='month')
    assert all(row['validation_rows']>=15 for row in by_month['slices'])
    assert by_month['slices']
    one=by_month['slices'][0]['value']
    filtered=unified.diagnostics(catalog.a,tid,model='forest',dimension='month',value=one)
    assert len(filtered['slices'])==1 and filtered['slices'][0]['value']==one
    assert not unified.diagnostics(catalog.a,tid,model='forest',dimension='month',value='missing')['slices']


def test_unmatured_labels_never_cross_roll_forward_cutoff(tmp_path):
    catalog,model,tasks,runs,unified,tid=prepare(tmp_path)
    # User must create a *new* spec for the changed immutable data version.
    with model.path.open(newline='',encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    for row in rows:
        row['label_ready']=(datetime.fromisoformat(row['event_time'])+timedelta(days=900)).isoformat()
    with model.path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=model.columns);writer.writeheader();writer.writerows(rows)
    model.source_sha=hashlib.sha256(model.path.read_bytes()).hexdigest()
    new=tasks.create(catalog.a,input_for('tabular_regression',
        validation={'kind':'rolling_origin','folds':3},time={
        'event_time_column':'event_time','label_available_at_column':'label_ready',
        'feature_available_at_column':'published'}))
    assert tasks.preflight(catalog.a,new['task_id'])['ready']
    tasks.approve(catalog.a,new['task_id'],ApproveModelTask(expected_revision=1,
        confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    unified.start(catalog.a,new['task_id'],StartUnifiedRun(expected_revision=1))
    assert runs.work_once()
    failed=unified.summary(catalog.a,new['task_id'])
    assert failed['state']=='failed' and failed['failure_code']=='ModelTaskError'
    assert not failed['metric_sets'] and not failed['report_available']
    with pytest.raises(ModelTaskConflict):unified.diagnostics(catalog.a,new['task_id'])
    # Original still immutable; old evidence may not be displayed for changed SHA.
    assert unified.summary(catalog.a,tid)['state']=='stale'


def test_per_x_publication_timestamp_violation_is_feature_specific(tmp_path):
    prepared=prepare(tmp_path)
    catalog,_,tasks,runs,unified,_=prepared
    request=input_for('tabular_regression',validation={'kind':'rolling_origin','folds':3},
        time={'event_time_column':'event_time','label_available_at_column':'label_ready',
              'feature_available_at_column':'published'},xs=['price'])
    payload=request.model_dump(mode='json')
    payload['features'][0]['available_at_column']='label_ready'
    task=tasks.create(catalog.a,ModelTaskInput.model_validate(payload))
    assert tasks.preflight(catalog.a,task['task_id'])['ready']
    tasks.approve(catalog.a,task['task_id'],ApproveModelTask(expected_revision=1,
        confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    unified.start(catalog.a,task['task_id'],StartUnifiedRun(expected_revision=1))
    assert runs.work_once()
    failed=unified.summary(catalog.a,task['task_id'])
    assert failed['state']=='failed' and 'price' in failed['failure_reason']
    assert 'PIT violation' in failed['failure_reason']


def test_iana_naive_timestamps_and_mixed_offsets_are_fail_closed():
    # 08:00 in Asia/Shanghai is 00:00Z, never implicitly treated as UTC.
    df=pd.DataFrame({'event':['2024-01-01 08:00','2024-01-02 08:00'],
       'pub':['2024-01-01 07:59','2024-01-02 07:59'],
       'ready':['2024-01-01 08:01','2024-01-02 08:01'],
       'x':[1,2],'y':[3,4]})
    spec=input_for('tabular_regression',validation={'kind':'rolling_origin','folds':2},
        time={'event_time_column':'event','label_available_at_column':'ready',
              'feature_available_at_column':'pub','timezone':'Asia/Shanghai'},xs=['x'])
    ledger,event,decision,labels=audit_availability(df,spec)
    assert event.iloc[0].isoformat()=='2024-01-01T00:00:00+00:00'
    assert ledger['timezone']=='Asia/Shanghai'
    df.loc[1,'pub']='2024-01-02T07:59:00+08:00'
    with pytest.raises(ModelTaskError,match='mixes timezone'):
        audit_availability(df,spec)


def test_diagnostics_api_project_source_revision_and_parameter_guards(tmp_path):
    catalog,model,tasks,runs,unified,tid=prepared=prepare(tmp_path,'rolling_origin')
    complete(prepared)
    app=FastAPI()
    app.include_router(create_model_task_router(tasks,runs,None,unified))
    with TestClient(app) as api:
        prefix=f'/api/v1/projects/{catalog.a}/model-tasks/{tid}/execution'
        res=api.get(prefix+'/diagnostics?model=forest&dimension=month')
        assert res.status_code==200 and res.json()['status']=='available'
        assert api.get(prefix+'/diagnostics?dimension=secret').status_code==422
        assert api.get(prefix+'/diagnostics?model=exploit').status_code==422
        assert api.get(prefix+'/diagnostics?value=abc').status_code==422
        assert api.get(f'/api/v1/projects/{catalog.b}/model-tasks/{tid}/execution/diagnostics').status_code==404
        old=tasks.get(catalog.a,tid)
        spec={k:v for k,v in old['spec'].items() if k not in ('asset','schema_version','project_id')}
        spec['name']='Revised task no stale diagnostic'
        tasks.replace(catalog.a,tid,ReplaceModelTask(expected_revision=1,
            spec=ModelTaskInput.model_validate(spec)))
        assert api.get(prefix+'/diagnostics').status_code==409
        assert api.get(prefix+'/report').status_code==409
