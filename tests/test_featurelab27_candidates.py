"""v0.26.2/0.27.0: real HTTP and SQLite; DuckDB computation is tested separately."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_conversational_bi_v0231 import _env
from timepilot.feature_candidates import infer_fields, make_feature_name, propose
from timepilot.feature_contracts import DataContract,FieldContract, FeatureSpecV2
from timepilot.feature_operator_registry import discover,compatible_fields
from timepilot.feature_compiler import FeatureCompiler
from timepilot.project_migrations import TARGET_SCHEMA


def _url(pid):return f'/api/v1/projects/{pid}/feature-lab'


def test_semantic_suggestions_exclude_unconfirmed_numeric_time(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        api=_url(pid)
        contract=client.get(api+'/contracts',params={'asset_id':asset}).json()
        fields={f['source_name']:f for f in contract['fields']}
        suggestions=client.get(api+'/field-suggestions',params={'asset_id':asset})
        assert suggestions.status_code==200,suggestions.text
        detected={f['name']:f for f in suggestions.json()['fields']}
        assert detected['Time']['proposed_semantic_type']=='unknown'
        assert detected['Time']['eligible_numeric'] is False
        assert detected['A1']['eligible_numeric'] is True
        ops=client.get(api+'/operators',params={'asset_id':asset})
        assert ops.status_code==200,ops.text
        arithmetic=next(o for o in ops.json()['operators'] if o['operator_id']=='arithmetic.binary')
        assert arithmetic['controls']==['first','second','sign']
        assert fields['Time']['field_id'] not in arithmetic['eligible_field_ids']
        assert fields['A1']['field_id'] in arithmetic['eligible_field_ids']
        event=next(o for o in ops.json()['operators'] if o['operator_id']=='event.since')
        assert event['available'] is False
    finally:client.__exit__(None,None,None)


def test_generate_patch_resume_and_save_existing_feature_spec(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    try:
        payload={'asset_id':asset,'version_id':'base','request_id':'create-27-1',
                 'template':'temporal','budget':24,'task_mode':'exploration'}
        r=client.post(api+'/candidate-sessions',json=payload)
        assert r.status_code==201,r.text
        doc=r.json();sid=doc['session_id']
        assert 1<=len(doc['candidates'])<=24
        assert all('time' not in c['output_name'].lower() for c in doc['candidates'])
        assert all(c['operator_id'] in {'lag.rows','difference.rows','rolling.mean','rolling.std'}
                   for c in doc['candidates'])
        assert len({c['output_name'] for c in doc['candidates']})==len(doc['candidates'])
        assert client.post(api+'/candidate-sessions',json=payload).json()['session_id']==sid
        latest=client.get(api+'/candidate-sessions/latest',params={'asset_id':asset}).json()['session']
        assert latest['session_id']==sid
        choices=[doc['candidates'][0]['candidate_id'],doc['candidates'][-1]['candidate_id']]
        patch=client.patch(api+'/candidate-sessions/'+sid,json={
            'expected_revision':1,'selected_candidate_ids':choices,
            'layout':{'mode':'manual','filter':'selected','focus_candidate_id':choices[0]}})
        assert patch.status_code==200,patch.text
        assert patch.json()['revision']==2
        assert patch.json()['layout']['mode']=='manual'
        assert client.patch(api+'/candidate-sessions/'+sid,json={
            'expected_revision':1,'selected_candidate_ids':choices}).status_code==409
        reloaded=client.get(api+'/candidate-sessions/'+sid).json()
        assert reloaded['selected_candidate_ids']==choices
        assert reloaded['layout']['mode']=='manual'
        saved=client.post(api+'/candidate-sessions/'+sid+'/save-set',json={
            'name':'自动时序特征','request_id':'save-27-1','candidate_ids':choices})
        assert saved.status_code==201,saved.text
        record=saved.json()
        assert record['spec']['schema_version']==2
        assert len(record['spec']['features'])==2
        assert record['source_sha256']==doc['source_sha256']
        assert client.post(api+'/candidate-sessions/'+sid+'/save-set',json={
            'name':'自动时序特征','request_id':'save-27-1','candidate_ids':choices}).json()['set_id']==record['set_id']
        with app.state.projects._db() as db:
            schema=db.execute('SELECT version FROM project_schema').fetchone()['version']
            names={x['name'] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert schema==TARGET_SCHEMA==34
        assert 'feature_candidate_sessions' in names
    finally:client.__exit__(None,None,None)


def test_candidate_guards_unknown_ids_race_and_stale_contract(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    try:
        invalid=client.post(api+'/candidate-sessions',json={'asset_id':asset,
              'request_id':'invalid-ids','selected_field_ids':['fld_unknown']})
        assert invalid.status_code==422
        document=client.post(api+'/candidate-sessions',json={'asset_id':asset,
              'request_id':'good','template':'basic'}).json()
        sid=document['session_id']
        assert client.patch(api+'/candidate-sessions/'+sid,json={
           'expected_revision':1,'selected_candidate_ids':['not-a-candidate']}).status_code==422
        assert client.patch(api+'/candidate-sessions/'+sid,json={
           'expected_revision':1,'layout':{'raw_sql':'SELECT secret'}}).status_code==422
        update=client.patch(api+'/contracts/'+asset,json={'expected_revision':0,'patch':{
            'fields':[{'field_id':document['field_suggestions'][1]['field_id'],
                        'display_name':'压力A1','confirmation':'confirmed'}]}})
        assert update.status_code==200,update.text
        stale=client.get(api+'/candidate-sessions/'+sid)
        assert stale.status_code==200 and stale.json()['stale'] is True
        assert client.post(api+'/candidate-sessions/'+sid+'/save-set',json={
            'name':'过期配方','request_id':'stale-save'}).status_code==409
        assert client.post(api+'/candidate-sessions/'+sid+'/preview-jobs',json={
            'request_id':'stale-preview','candidate_ids':document['selected_candidate_ids'][:1]}).status_code==409
        new=client.post(api+'/candidate-sessions',json={'asset_id':asset,
            'request_id':'after-change','template':'basic'})
        assert new.status_code==201,new.text
        assert new.json()['contract_revision']==1
    finally:client.__exit__(None,None,None)


def test_candidate_preview_job_created_and_is_source_pinned(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    try:
        doc=client.post(api+'/candidate-sessions',json={'asset_id':asset,
              'request_id':'preview-session','template':'basic'}).json()
        ids=doc['selected_candidate_ids'][:2]
        response=client.post(api+'/candidate-sessions/'+doc['session_id']+'/preview-jobs',json={
            'request_id':'preview-1','candidate_ids':ids,'max_rows':12,'scan_budget':500})
        assert response.status_code==202,response.text
        job=response.json();assert job['status']=='queued'
        assert job['kind']=='candidate_preview'
        assert job['payload']['source_sha256']==doc['source_sha256']
        assert job['payload']['candidate_ids']==ids
        assert client.post(api+'/candidate-sessions/'+doc['session_id']+'/preview-jobs',json={
            'request_id':'preview-1','candidate_ids':ids,'max_rows':12,'scan_budget':500}).json()['job_id']==job['job_id']
        recent=client.get(api+'/profile-jobs',params={'asset_id':asset}).json()['items']
        assert any(x['job_id']==job['job_id'] for x in recent)
    finally:client.__exit__(None,None,None)


def test_boolean_events_and_time_duration_require_confirmed_contract():
    c=DataContract(asset_id='a',version_id='base',source_sha256='d'*64,state='confirmed',
      revision=2,timezone='Asia/Shanghai',entity_field_ids=['fld_device'],
      event_time_field_id='fld_stamp',fields=[
        FieldContract(field_id='fld_device',source_name='device_id',source_column_index=0,
            physical_type='VARCHAR',semantic_type='id',role='entity_key',confirmation='confirmed'),
        FieldContract(field_id='fld_stamp',source_name='created_at',source_column_index=1,
            physical_type='TIMESTAMP',semantic_type='datetime',role='event_time',confirmation='confirmed'),
        FieldContract(field_id='fld_marker',source_name='maintenance',source_column_index=2,
            physical_type='BOOLEAN',semantic_type='event',confirmation='confirmed'),
        FieldContract(field_id='fld_pressure',source_name='pressure',source_column_index=3,
            physical_type='DOUBLE',semantic_type='numeric',role='value',confirmation='confirmed')])
    operators={x['operator_id']:x for x in discover(contract=c)}
    assert operators['event.since']['available']
    assert operators['event.since']['eligible_field_ids']==['fld_marker']
    assert operators['rolling.duration.mean']['available']
    items=propose(c,template='industrial',budget=20)
    event=next(x for x in items if x['operator_id']=='event.since')
    assert event['input_field_ids']==['fld_marker']
    assert event['parameters']['order_field_id']=='fld_stamp'
    roll=next(x for x in items if x['operator_id']=='rolling.mean')
    assert roll['parameters']['order_field_id']=='fld_stamp'
    spec=FeatureSpecV2(name='maint',source={'asset_id':'a','version_id':'base','source_sha256':'d'*64},
        contract_revision=2,features=[{k:event[k] for k in ('feature_id','output_name','display_name',
        'operator_id','operator_version','input_field_ids','parameters')}])
    sql=FeatureCompiler(c,spec).expression(spec.features[0])
    assert "DATE_DIFF('second'" in sql
    assert 'PARTITION BY "device_id"' in sql
    assert '"created_at"' in sql
    assert make_feature_name('event.since',[c.fields[2]],{},())


def test_static_workbench_is_conditional_and_no_unsafe_sql_editor():
    root=Path(__file__).resolve().parents[1]/'src'/'timepilot'/'static'
    editor=(root/'feature-builder-ui.js').read_text()
    shell=(root/'feature-shell.js').read_text()
    assert "controls.includes('event')" in editor
    assert "controls.includes('window_seconds')" in editor
    assert "controls.includes('second')" in editor
    assert 'name="name" placeholder="例如 A2_mean5"' not in editor
    assert 'candidate-sessions/latest' in shell
    assert 'candidate-sessions' in shell
    assert 'candidate_preview' in shell
    assert 'data-feature-v2="preview-candidates"' in editor


def test_candidate_idempotency_rejects_reused_request_with_changed_parameters(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    try:
        base={'asset_id':asset,'request_id':'same-request-different-params',
              'template':'basic','budget':10}
        first=client.post(api+'/candidate-sessions',json=base)
        assert first.status_code==201,first.text
        retry=client.post(api+'/candidate-sessions',json=base)
        assert retry.status_code==201 and retry.json()['session_id']==first.json()['session_id']
        conflicting=client.post(api+'/candidate-sessions',json={**base,'template':'quality'})
        assert conflicting.status_code in (409,422),conflicting.text
    finally:client.__exit__(None,None,None)


def test_schema30_upgrade_adds_sessions_without_changing_existing_feature_sets(tmp_path):
    from timepilot.project_migrations import migrate
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    try:
        session=client.post(api+'/candidate-sessions',json={
            'asset_id':asset,'request_id':'upgrade-test','template':'basic'}).json()
        feature_set=client.post(api+'/candidate-sessions/'+session['session_id']+'/save-set',json={
            'name':'升级前已有的特征集','request_id':'upgrade-save'}).json()
        sid=feature_set['set_id']
        # Simulate an actual v30 catalog with existing published-state tables and records;
        # only the new v31 session table/index are absent.
        with app.state.projects._db() as db:
            before=db.execute('SELECT set_id,spec_sha256,revision FROM feature_sets WHERE set_id=?',
                              (sid,)).fetchone()
            assert before is not None
            db.execute('DROP TABLE feature_candidate_sessions')
            db.execute('UPDATE project_schema SET version=30')
        with app.state.projects._db() as db:
            db.execute('BEGIN IMMEDIATE')
            migrate(db)
            version=db.execute('SELECT version FROM project_schema').fetchone()[0]
            after=db.execute('SELECT set_id,spec_sha256,revision FROM feature_sets WHERE set_id=?',
                             (sid,)).fetchone()
            tables={row[0] for row in db.execute('SELECT name FROM sqlite_master WHERE type="table"')}
        assert version==34
        assert tuple(before)==tuple(after)
        assert 'feature_candidate_sessions' in tables
    finally:client.__exit__(None,None,None)


def test_suggested_output_names_never_overlap_existing_source_columns():
    from timepilot.feature_contracts import DataContract,FieldContract
    contract=DataContract(asset_id='a',version_id='base',source_sha256='e'*64,fields=[
        FieldContract(field_id='fld_a',source_name='pressure',source_column_index=0,
            physical_type='DOUBLE',semantic_type='numeric',role='value'),
        FieldContract(field_id='fld_b',source_name='pressure_mean_5',source_column_index=1,
            physical_type='DOUBLE',semantic_type='numeric',role='value')])
    candidates=propose(contract,template='basic',budget=8)
    assert candidates
    names=[x['output_name'] for x in candidates]
    assert len(names)==len(set(names))
    assert not set(names)&{'pressure','pressure_mean_5'}


def test_candidate_preview_worker_wiring_with_sql_stub(tmp_path,monkeypatch):
    """Runs queue->lease->SQL assembly->receipt using a stub; not a DuckDB correctness test."""
    import re
    app,client,pid,asset,_=_env(tmp_path)
    api=_url(pid)
    class FakeResult:
        def __init__(self,names):self.description=[(x,) for x in names]
        def fetchall(self):return [tuple(float(i) for i in range(len(self.description)))]
        def fetchone(self):return tuple(1 for _ in self.description)
    class FakeConnection:
        def __init__(self):self.statements=[]
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def execute(self,sql,parameters=None):
            self.statements.append(sql)
            if sql.startswith('SELECT "__tp_rowid"'):
                names=re.findall(r'"([^"]+)"',sql.split(' FROM ')[0])
                return FakeResult(names)
            if sql.startswith('SELECT COUNT(*) AS "__total"'):
                names=re.findall(r' AS "([^"]+)"',sql.split(' FROM ')[0])
                return FakeResult(names)
            return None
    fake=FakeConnection()
    native=app.state.native_bi
    try:
        doc=client.post(api+'/candidate-sessions',json={
            'asset_id':asset,'request_id':'sql-stub-demo','template':'basic'}).json()
        chosen=doc['selected_candidate_ids'][:2]
        pending=client.post(api+'/candidate-sessions/'+doc['session_id']+'/preview-jobs',json={
            'request_id':'sql-stub-preview','candidate_ids':chosen,'max_rows':30,'scan_budget':500})
        assert pending.status_code==202,pending.text
        monkeypatch.setattr(native,'_connection',lambda:fake)
        monkeypatch.setattr(native,'_install_effective_view',lambda *args,**kwargs:None)
        assert app.state.feature_jobs.work_once() is True
        job=client.get(api+'/runs/'+pending.json()['job_id'])
        assert job.status_code==200,job.text
        item=job.json()
        assert item['status']=='succeeded',item
        assert item['result']['rows_returned']==1
        assert item['result']['predictive_value_evaluated'] is False
        assert item['result']['source_sha256']==doc['source_sha256']
        assert any(statement.startswith('CREATE TEMP VIEW') for statement in fake.statements)
    finally:client.__exit__(None,None,None)
