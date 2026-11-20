"""v0.32.4: historical manual-window audit and strict same-source QA.

The gate's synthetic unit reports exercise tamper rejection only; they are not
accepted production or real-browser evidence.
"""
from __future__ import annotations
import hashlib,sqlite3,sys
from pathlib import Path
from uuid import uuid4
import pytest
from timepilot.feature_contracts import DataContract,FeatureDefinition,FeatureSpecV2,SourceRef
from timepilot.feature_legacy_audit import temporal_recipe_audit
from test_conversational_bi_v0231 import _env

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from featurelab324_acceptance import inspect_same_source


def sample_contract():
    return DataContract(asset_id='source',version_id='base',source_sha256='f'*64,
        revision=1,state='confirmed',entity_field_ids=['fld_device'],fields=[
        dict(field_id='fld_device',source_name='device_id',source_column_index=0,
             physical_type='VARCHAR',semantic_type='id',role='entity_key',confirmation='confirmed'),
        dict(field_id='fld_time',source_name='Time',source_column_index=1,
             physical_type='INTEGER',semantic_type='numeric',role='order_index',confirmation='confirmed'),
        dict(field_id='fld_value',source_name='pressure',source_column_index=2,
             physical_type='DOUBLE',semantic_type='numeric',role='value',confirmation='confirmed')])


def sample_spec(parameters):
    return FeatureSpecV2(name='Legacy sample',source=SourceRef(
        asset_id='source',version_id='base',source_sha256='f'*64),contract_revision=1,
        features=[FeatureDefinition(feature_id='f_rolling',output_name='ma5',
          operator_id='rolling.mean',input_field_ids=['fld_value'],
          parameters={'window':5,'min_periods':1,**parameters})])


def test_legacy_audit_is_read_only_and_flags_implicit_row_order():
    contract=sample_contract()
    spec=sample_spec({})
    before=spec.model_dump_json()
    report=temporal_recipe_audit(spec,contract)
    assert not report['can_publish'] and report['risk']=='high'
    assert report['issues'][0]['code']=='implicit_import_row_order'
    assert report['source_sha256']=='f'*64 and report['validation_scope']=='schema_only_no_source_rows_verified'
    assert spec.model_dump_json()==before
    good=temporal_recipe_audit(sample_spec({'order_field_id':'fld_time',
          'partition_field_ids':['fld_device']}),contract)
    assert good['can_publish'] and good['risk']=='not_proven'
    assert good['window_feature_count']==1
    mixed=temporal_recipe_audit(sample_spec({'order_field_id':'fld_time','partition_field_ids':[]}),contract)
    assert mixed['risk']=='high' and any(x['code']=='partition_mismatch' for x in mixed['issues'])


def test_api_old_feature_sets_are_audited_and_publication_blocked(tmp_path):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        contract=client.get(base+'/contracts',params={'asset_id':aid}).json()
        ids={f['source_name']:f['field_id'] for f in contract['fields']}
        spec={'schema_version':2,'name':'old manual window','source':{
            'asset_id':aid,'version_id':'base','source_sha256':contract['source_sha256']},
            'contract_revision':contract['revision'],'features':[{
            'feature_id':'old','output_name':'old_mean','operator_id':'rolling.mean',
            'input_field_ids':[ids['A1']],'parameters':{'window':5,'min_periods':1}}]}
        created=client.post(base+'/sets',json={'spec':spec,'request_id':str(uuid4())})
        assert created.status_code==201,created.text
        sid=created.json()['set_id']
        audit=client.get(base+'/sets/'+sid+'/temporal-audit')
        assert audit.status_code==200,audit.text
        assert audit.json()['risk']=='high' and audit.json()['set_state']=='draft'
        listed=client.get(base+'/sets/temporal-audits',params={'asset_id':aid})
        assert listed.status_code==200,listed.text
        assert any(x['set_id']==sid for x in listed.json()['items'])
        preview=client.get(base+'/sets/'+sid+'/publication-preview').json()
        assert preview['artifact_gate']=='unsafe_legacy_order'
        assert not preview['artifact_ready']
        assert app.state.feature_registry.get(pid,sid)['state']=='draft'
        foreign=client.get(f'/api/v1/projects/{uuid4()}/feature-lab/sets/{sid}/temporal-audit')
        assert foreign.status_code in (403,404)
    finally:client.__exit__(None,None,None)



def test_missing_historical_contract_fails_closed_and_catalog_stays_readable(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        contract=client.get(base+'/contracts',params={'asset_id':aid}).json()
        ids={f['source_name']:f['field_id'] for f in contract['fields']}
        spec={'schema_version':2,'name':'historical missing contract',
            'source':{'asset_id':aid,'version_id':'base',
                      'source_sha256':contract['source_sha256']},
            'contract_revision':contract['revision'],
            'features':[{'feature_id':'legacy','output_name':'past_rolling',
                'operator_id':'rolling.mean','input_field_ids':[ids['A1']],
                'parameters':{'window':3,'min_periods':1}}]}
        resp=client.post(base+'/sets',json={'spec':spec,'request_id':str(uuid4())})
        assert resp.status_code==201,resp.text
        sid=resp.json()['set_id']
        registry=app.state.feature_registry
        original_get=registry.get
        # Simulate a pre-migration record pinned to an unavailable nonzero revision.
        def old_record(project_id,set_id):
            data=original_get(project_id,set_id)
            return {**data,'contract_revision':7} if set_id==sid else data
        monkeypatch.setattr(registry,'get',old_record)
        svc=registry.semantics
        def unavailable(*args,**kwargs):
            raise KeyError('missing old revision')
        monkeypatch.setattr(svc,'get_revision',unavailable)
        single=client.get(base+'/sets/'+sid+'/temporal-audit')
        assert single.status_code==200,single.text
        assert single.json()['risk']=='high' and not single.json()['can_publish']
        assert single.json()['issues'][0]['code']=='historical_contract_unavailable'
        batch=client.get(base+'/sets/temporal-audits',params={'asset_id':aid})
        assert batch.status_code==200,batch.text
        assert any(r['set_id']==sid for r in batch.json()['items'])
        gate=client.get(base+'/sets/'+sid+'/publication-preview')
        assert gate.status_code in (200,409),gate.text
        if gate.status_code==200:
            assert gate.json()['artifact_gate']=='unsafe_legacy_order'
    finally:client.__exit__(None,None,None)


def test_core_gate_verifies_real_csv_and_persisted_parquet_bytes(tmp_path):
    # Controlled report fixture. No claim of having run DuckDB or Chrome.
    csv=tmp_path/'source.csv';csv.write_bytes(b'device,pressure\nA,1\n')
    source_sha=hashlib.sha256(csv.read_bytes()).hexdigest()
    home=tmp_path/'qa';root=home/'isolated_application';(root/'projects').mkdir(parents=True)
    (root/'feature_lab'/'derived').mkdir(parents=True)
    artifact=root/'feature_lab'/'derived'/'data.parquet';artifact.write_bytes(b'QA fake-parquet-fixture')
    artifact_sha=hashlib.sha256(artifact.read_bytes()).hexdigest()
    db=root/'projects'/'catalog.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE feature_artifacts (project_id TEXT,artifact_id TEXT,relpath TEXT,sha256 TEXT)')
        con.execute('INSERT INTO feature_artifacts VALUES (?,?,?,?)',('p','art','derived/data.parquet',artifact_sha))
    backend={'status':'passed_backend_same_source_browser_pending',
        'source':{'sha256':source_sha},'artifact':{'artifact_id':'art','sha256':artifact_sha},
        'evaluation':{'evaluated_artifact_sha256':artifact_sha},
        'qa_home':str(home),'qa_project_id':'p','source_native_asset_id':'src','materialized_native_asset_id':'result'}
    browser={'status':'passed','qa_project_id':'p','source_native_asset_id':'src',
        'materialized_native_asset_id':'result','source_sha256':source_sha,
        'artifact_sha256':artifact_sha,'panel_count':2,'websocket_opened':True,
        'refresh_restored':True,'native_bundle_verified':True,'installed_chrome':True}
    assert inspect_same_source(csv,backend,browser)['status']=='passed'
    assert inspect_same_source(csv,backend,browser,scope='full')['status']=='blocked'
    artifact.write_bytes(b'tampered')
    tampered=inspect_same_source(csv,backend,browser)
    assert tampered['status']=='blocked' and 'Parquet' in str(tampered['issues'])
    artifact.write_bytes(b'QA fake-parquet-fixture')
    csv.write_bytes(b'tampered')
    assert 'CSV bytes changed' in str(inspect_same_source(csv,backend,browser)['issues'])
    csv.write_bytes(b'device,pressure\nA,1\n')
    browser['websocket_opened']=False
    assert inspect_same_source(csv,backend,browser)['status']=='blocked'


def test_true_duckdb_parquet_window_and_entity_isolation(tmp_path):
    """A real numerical test only when both native packages exist."""
    duckdb=pytest.importorskip('duckdb')
    pq=pytest.importorskip('pyarrow.parquet')
    from timepilot.feature_compiler import FeatureCompiler
    con=duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE __tp_effective (__tp_rowid INTEGER,device_id VARCHAR,Time INTEGER,pressure DOUBLE)')
        for i,row in enumerate([('A',3,30),('B',2,200),('A',1,10),('B',1,100),('A',2,20),('B',3,300)]):
            con.execute('INSERT INTO __tp_effective VALUES (?,?,?,?)',(i+1,*row))
        spec=sample_spec({'order_field_id':'fld_time','partition_field_ids':['fld_device']})
        compiler=FeatureCompiler(sample_contract(),spec)
        view=compiler.compile_views(con)
        out=tmp_path/'materialized.parquet'
        con.execute(f"COPY (SELECT * FROM {view}) TO '{out.as_posix()}' (FORMAT PARQUET)")
        data=pq.read_table(out).to_pydict()
        rows={(d,t):round(v,2) for d,t,v in zip(data['device_id'],data['Time'],data['ma5'])}
        assert rows=={('A',1):10,('A',2):15,('A',3):20,('B',1):100,('B',2):150,('B',3):200}
        assert len(hashlib.sha256(out.read_bytes()).hexdigest())==64
    finally:con.close()
