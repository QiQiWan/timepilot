"""Real SQLite contract tests; fake immutable source, no fake training metrics."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from timepilot.model_task_spec import (ModelTaskInput, ReplaceModelTask, ApproveModelTask,
                                        canonical_sha)
from timepilot.model_task_store import ModelTaskStore, ModelTaskConflict, ModelTaskError
from timepilot.model_task_api import create_model_task_router


class Catalog:
    def __init__(self, root: Path):
        self.db_file = root / 'projects.sqlite3'
        self._lock = threading.RLock()
        self.a, self.b = str(uuid4()), str(uuid4())
        with self._db() as db:
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO projects VALUES (?)', [(self.a,), (self.b,)])

    def _db(self):
        db = sqlite3.connect(self.db_file, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        return db

    def _id(self, value):
        return str(value)

    def get(self, pid):
        with self._db() as db:
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
                raise KeyError('No project')
        return {'id':pid}

    def writable(self, pid):
        return self.get(pid)


COLUMNS = ['sale', 'price', 'fault_label', 'event_time', 'published', 'label_ready', 'device']


class FakeModeling:
    def __init__(self):
        self.source_sha = 'a'*64
        self.native_sha = 'b'*64
        self.columns = list(COLUMNS)
        self.revoked = False

    def _source(self, pid, aid, version):
        if self.revoked or aid != 'asset-1' or version != 'base':
            raise KeyError('Asset unavailable')
        return {'original_data_sha256':self.source_sha,'native_sha256':self.native_sha,
                'source_columns':list(self.columns)}

    def profile(self, pid, aid, version):
        fields=[]
        for name in self.columns:
            kind='target_label' if name=='fault_label' else 'time' if name=='event_time' else 'entity' if name=='device' else 'target_numeric'
            fields.append({'name':name,'sample_numeric_rate':float(name in ('sale','price','fault_label')),
                'sample_datetime_rate':float(name in ('event_time','published','label_ready')),
                'sample_distinct':3 if name=='fault_label' else 80,
                'suggested_roles':[{'role':kind,'confidence':.96}]})
        return {'source_rows':500,'fields':fields, 'recommendation':{'time':'event_time'}}


def input_for(kind='tabular_regression', *, target=None, xs=None, split=None, **extra):
    is_time=kind in ('continuous_forecast','classification')
    is_label=kind in ('classification','tabular_classification')
    target=target or ('fault_label' if is_label else 'sale')
    base={
        'name':'My test prediction', 'asset_id':'asset-1', 'version_id':'base',
        'business_goal':'Predict a target', 'task_kind':kind,
        'target':{'column':target},
        'features':[{'column':x,'role':'observed_past' if is_time else 'static'} for x in (xs or ['price'])],
        'time':({'event_time_column':'event_time','feature_available_at_column':'published',
                 'label_available_at_column':'label_ready' if kind=='classification' else None}
                if is_time else {}),
        'validation':{'kind':split or ('rolling_origin' if is_time else 'stratified_kfold' if is_label else 'kfold'),
                      'folds':3},
    }
    base.update(extra)
    return ModelTaskInput.model_validate(base)


@pytest.fixture
def service(tmp_path):
    catalog=Catalog(tmp_path)
    modeling=FakeModeling()
    return ModelTaskStore(catalog,modeling),catalog,modeling


@pytest.mark.parametrize('kind', ['tabular_regression','tabular_classification','continuous_forecast','classification'])
def test_all_four_families_persist_with_bound_data_and_events(service, kind):
    store, catalog, modeling=service
    task=store.create(catalog.a,input_for(kind))
    assert task['spec']['asset']['source_sha256']=='a'*64
    assert task['status']=='review_required' and task['revision']==1
    assert task['spec_sha256']==canonical_sha(task['spec'])
    pf=store.preflight(catalog.a,task['task_id'])
    assert pf['ready'],pf
    ready=store.get(catalog.a,task['task_id'])
    assert ready['status']=='ready' and ready['source_state']=='current'
    approved=store.approve(catalog.a,task['task_id'],ApproveModelTask(
        expected_revision=1,confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    assert approved['status']=='approved' and approved['approval_sha256']
    assert [x['event_type'] for x in store.events(catalog.a,task['task_id'])] == [
        'created','preflight_ready','approved']


def test_conflicting_concurrent_edits_and_approval_reset(service):
    store, catalog,_=service
    task=store.create(catalog.a,input_for())
    tid=task['task_id']
    assert store.preflight(catalog.a,tid)['ready']
    store.approve(catalog.a,tid,ApproveModelTask(
        expected_revision=1,confirm_target=True,confirm_feature_availability=True,confirm_split=True))
    failures=[]
    def update(name):
        try:
            store.replace(catalog.a,tid,ReplaceModelTask(
                expected_revision=1,spec=input_for(name=name)))
        except ModelTaskConflict:
            failures.append(name)
    a=threading.Thread(target=update,args=('one task name',))
    b=threading.Thread(target=update,args=('other task name',))
    a.start();b.start();a.join();b.join()
    updated=store.get(catalog.a,tid)
    assert updated['revision']==2 and updated['status']=='review_required'
    assert updated['approval_sha256'] is None and updated['preflight_sha256'] is None
    assert len(failures)==1
    with pytest.raises(ModelTaskConflict):
        store.approve(catalog.a,tid,ApproveModelTask(
            expected_revision=2,confirm_target=True,confirm_feature_availability=True,confirm_split=True))


def test_source_and_column_mutation_fail_closed(service):
    store,catalog,modeling=service
    task=store.create(catalog.a,input_for())
    tid=task['task_id']
    modeling.source_sha='c'*64
    assert store.get(catalog.a,tid)['effective_status']=='stale_source'
    pf=store.preflight(catalog.a,tid)
    assert not pf['ready'] and 'source_sha256_changed' in pf['blockers']
    modeling.source_sha='a'*64;modeling.columns.append('new_feature')
    assert 'column_schema_changed' in store.get(catalog.a,tid)['source_problems']
    modeling.revoked=True
    assert store.get(catalog.a,tid)['source_state']=='stale_source'


def test_project_isolation_and_version_rebinding_denied(service):
    store,catalog,_=service
    first=store.create(catalog.a,input_for())
    with pytest.raises(KeyError):store.get(catalog.b,first['task_id'])
    assert store.list(catalog.b)==[]
    with pytest.raises(ModelTaskConflict):
        store.replace(catalog.a,first['task_id'],ReplaceModelTask(
            expected_revision=1,spec=input_for(version_id='v2')))


def test_refuse_unconfirmed_approval_and_unknown_fields(service):
    store,catalog,_=service
    t=store.create(catalog.a,input_for())
    store.preflight(catalog.a,t['task_id'])
    with pytest.raises(ModelTaskError):store.approve(catalog.a,t['task_id'],ApproveModelTask(
        expected_revision=1,confirm_target=True,confirm_feature_availability=False,confirm_split=True))
    wrong=store.create(catalog.a,input_for(xs=['unrecognized']))
    pf=store.preflight(catalog.a,wrong['task_id'])
    assert not pf['ready'] and any('Missing columns' in x for x in pf['blockers'])


def test_schema_rejects_leakage_and_incompatible_split():
    with pytest.raises(ValidationError):input_for(xs=['sale'])
    with pytest.raises(ValidationError):input_for('continuous_forecast',split='kfold')
    with pytest.raises(ValidationError):input_for('tabular_regression',
        features=[{'column':'price','role':'known_future'}])
    with pytest.raises(ValidationError):input_for('tabular_regression',random_parameter=True)


def test_api_create_list_get_patch_preflight_approve_and_error_codes(service):
    store,catalog,_=service
    app=FastAPI()
    app.include_router(create_model_task_router(store))
    client=TestClient(app)
    root=f'/api/v1/projects/{catalog.a}/model-tasks'
    req=input_for().model_dump(mode='json')
    response=client.post(root,json=req)
    assert response.status_code==201,response.text
    tid=response.json()['task_id']
    assert client.get(root).json()['items'][0]['task_id']==tid
    assert client.get(root+'/'+tid).json()['source_state']=='current'
    assert client.get(f'/api/v1/projects/{catalog.b}/model-tasks/'+tid).status_code==404
    assert client.post(root+'/'+tid+'/preflight').json()['ready']
    assert client.post(root+'/'+tid+'/approve',json={
        'expected_revision':1,'confirm_target':True,
        'confirm_feature_availability':True,'confirm_split':True}).status_code==200
    patch=client.patch(root+'/'+tid,json={'expected_revision':1,'spec':{**req,'name':'Updated model task'}})
    assert patch.status_code==200
    assert patch.json()['revision']==2
    assert client.patch(root+'/'+tid,json={'expected_revision':1,'spec':req}).status_code==409
    assert client.post(root,json={**req,'features':[{'column':'sale'}]}).status_code==422
    assert len(client.get(root+'/'+tid+'/events').json()['items'])>=4


def test_task_list_contains_business_names_without_raw_spec(service):
    store, catalog, _ = service
    created=store.create(catalog.a, input_for(name='Monthly vehicle demand'))
    items=store.list(catalog.a)
    assert len(items)==1
    assert items[0]['task_id']==created['task_id']
    assert items[0]['name']=='Monthly vehicle demand'
    assert items[0]['target']=='sale'
    assert items[0]['task_kind']=='tabular_regression'
    assert 'spec_json' not in items[0]
    assert store.list(catalog.b)==[]
