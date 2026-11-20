"""Causal holdout regression with real CSV, SQLite and sklearn; no mock metrics."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta, timezone

from test_model_task_execution_v0391 import CSVModeling
from test_model_task_contract_v0390 import Catalog, input_for
from timepilot.model_task_store import ModelTaskStore
from timepilot.model_task_execution import ModelTaskRunService
from timepilot.model_task_spec import ApproveModelTask, ModelTaskInput


def _prepare(tmp_path, *, feature_column='published', label_column='label_ready', decision_column=None):
    catalog=Catalog(tmp_path)
    modeling=CSVModeling(tmp_path)
    tasks=ModelTaskStore(catalog,modeling)
    engine=ModelTaskRunService(tasks,modeling)
    times={'event_time_column':'event_time','feature_available_at_column':feature_column,
           'label_available_at_column':label_column}
    if decision_column:
        times['decision_time_column']=decision_column
    spec=input_for('tabular_regression',validation={'kind':'temporal_holdout','folds':2},time=times)
    created=tasks.create(catalog.a,spec)
    return catalog,modeling,tasks,engine,created


def _run(catalog,tasks,engine,created):
    assert tasks.preflight(catalog.a,created['task_id'])['ready']
    tasks.approve(catalog.a,created['task_id'],ApproveModelTask(expected_revision=1,
        confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    run=engine.submit(catalog.a,created['task_id'])
    assert engine.work_once()
    return engine.get(catalog.a,run['run_id'])


def test_valid_publication_and_label_maturity_recorded_in_result(tmp_path):
    catalog,_,tasks,engine,created=_prepare(tmp_path)
    result=_run(catalog,tasks,engine,created)
    assert result['state']=='succeeded',result
    assert result['result']['point_in_time_check']=='decision_time_publication_and_label_maturity'
    assert result['result']['folds'][0]['split']=='temporal_holdout'


def test_after_decision_publication_is_rejected_even_with_valid_labels(tmp_path):
    catalog,_,tasks,engine,created=_prepare(tmp_path,feature_column='label_ready',
                                              decision_column='published')
    result=_run(catalog,tasks,engine,created)
    assert result['state']=='failed'
    assert result['error_code']=='ModelTaskError'
    assert 'PIT violation' in result['error_message']
    assert result['result'] is None


def test_unmatured_training_labels_must_not_enter_holdout(tmp_path):
    catalog,modeling,tasks,engine,created=_prepare(tmp_path)
    with modeling.path.open(newline='',encoding='utf-8') as stream:
        rows=list(csv.DictReader(stream))
    for row in rows:
        row['label_ready']=(datetime.fromisoformat(row['event_time'])+timedelta(days=400)).isoformat()
    with modeling.path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=modeling.columns)
        writer.writeheader();writer.writerows(rows)
    modeling.source_sha=hashlib.sha256(modeling.path.read_bytes()).hexdigest()
    # Contract is newly bound to this exact source; label maturity remains invalid.
    renewed=tasks.create(catalog.a,input_for('tabular_regression',
        validation={'kind':'temporal_holdout','folds':2},
        time={'event_time_column':'event_time','feature_available_at_column':'published',
              'label_available_at_column':'label_ready'}))
    result=_run(catalog,tasks,engine,renewed)
    assert result['state']=='failed' and result['result'] is None
    assert result['error_code']=='ModelTaskError'


def test_missing_label_availability_and_unknown_feature_publication_fail_preflight(tmp_path):
    catalog,_,tasks,_,_= _prepare(tmp_path)
    for time_contract in (
        {'event_time_column':'event_time','feature_available_at_column':'published'},
        {'event_time_column':'event_time','label_available_at_column':'label_ready'},
    ):
        created=tasks.create(catalog.a,input_for('tabular_regression',
            validation={'kind':'temporal_holdout','folds':2},time=time_contract))
        preflight=tasks.preflight(catalog.a,created['task_id'])
        assert preflight['ready'] is False
        assert preflight['blockers']


def test_fold_pit_audit_contains_verified_cutoff_and_maturity(tmp_path):
    catalog,_,tasks,engine,created=_prepare(tmp_path)
    done=_run(catalog,tasks,engine,created)
    assert done['state']=='succeeded'
    record=done['result']['folds'][0]
    assert record['latest_train_event_utc']<record['first_validation_event_utc']
    assert record['latest_train_event_utc']<record['cutoff_utc']
    assert record['training_labels_mature_at_cutoff'] is True
    assert record['feature_publication_checked_at_decision'] is True
