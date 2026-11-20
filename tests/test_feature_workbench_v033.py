"""v0.33.0/v0.33.1: executable feature evidence and source-safe chart API."""
from __future__ import annotations

import time
from uuid import uuid4
import pytest
from test_conversational_bi_v0231 import _env
from timepilot.feature_contracts import DataContract,FeatureSpecV2,FeatureDefinition
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_operator_registry import compatible_fields


def _ready(app,client,pid,aid,task_hint='explore'):
    base=f'/api/v1/projects/{pid}/feature-lab'
    response=client.post(base+'/analysis/sessions',json={'asset_id':aid,'version_id':'base',
        'task_hint':task_hint,'request_id':'v033-'+str(uuid4()),'budget':10})
    assert response.status_code==201,response.text
    sid=response.json()['session_id']
    for _ in range(80):
        app.state.feature_jobs.work_once()
        next_state=client.post(base+f'/analysis/sessions/{sid}/advance')
        assert next_state.status_code==200,next_state.text
        state=next_state.json()
        if state['status'] in ('ready','failed','needs_input'):break
        time.sleep(.025)
    assert state['status']=='ready',state
    assert state['report']['evaluated_count']>=1
    return base,sid,state


def test_real_numeric_feature_distribution_matches_duckdb_statistics(tmp_path):
    pytest.importorskip('duckdb', reason='Requires real DuckDB for an executable integration test')
    app,client,pid,aid,_=_env(tmp_path)
    try:
        base,sid,state=_ready(app,client,pid,aid)
        assert state['report']['quality_rows']
        cid=state['report']['quality_rows'][0]['candidate_id']
        resp=client.get(base+f'/analysis/sessions/{sid}/candidates/{cid}/chart',params={'mode':'distribution'})
        assert resp.status_code==200,resp.text
        doc=resp.json()
        assert doc['status']=='ready' and doc['scope']=='full_source_aggregate'
        assert sum(x['count'] for x in doc['points'])==doc['quality']['finite_count']
        assert doc['population_rows']==doc['quality']['rows_scanned']==3
        assert doc['predictive_value_evaluated'] is False
        assert doc['source_sha256']==state['source_sha256']
        assert doc['candidate_id']==cid
        again=client.get(base+f'/analysis/sessions/{sid}/candidates/{cid}/chart')
        assert again.status_code==200 and again.json()['quality']==doc['quality']
        invalid=client.get(base+f'/analysis/sessions/{sid}/candidates/{cid}/chart?mode=unknown')
        assert invalid.status_code==422
        other=client.get(base+f'/analysis/sessions/{sid}/candidates/other/chart')
        # Unknown candidate must never return another session's data.
        assert other.status_code in (404,422)
    finally:client.__exit__(None,None,None)


def test_categorical_is_value_is_real_row_local_feature_no_label_leakage():
    pytest.importorskip('duckdb', reason='Requires real DuckDB to execute a categorical feature')
    contract=DataContract(asset_id='a',version_id='base',source_sha256='b'*64,revision=1,state='confirmed',fields=[
        dict(field_id='fld_cat',source_name='condition',source_column_index=0,physical_type='VARCHAR',
             semantic_type='category',role='regime',confirmation='confirmed'),
        dict(field_id='fld_target',source_name='bad_label',source_column_index=1,physical_type='VARCHAR',
             semantic_type='category',role='label',confirmation='confirmed'),
        dict(field_id='fld_private',source_name='secret',source_column_index=2,physical_type='VARCHAR',
             semantic_type='category',classification='restricted',confirmation='confirmed'),
    ])
    assert compatible_fields(contract,'category')==['fld_cat']
    feat=FeatureDefinition(feature_id='cat_f',output_name='condition_A',
        operator_id='category.is_value',operator_version='0.33',input_field_ids=['fld_cat'],
        parameters={'category_value':"A'quote"})
    spec=FeatureSpecV2(name='category',source={'asset_id':'a','version_id':'base','source_sha256':'b'*64},
                       contract_revision=1,features=[feat])
    compiler=FeatureCompiler(contract,spec)
    assert "A''quote" in compiler.expression(feat)
    import duckdb
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE data (__tp_rowid INT,condition VARCHAR,bad_label VARCHAR,secret VARCHAR)')
        con.executemany('INSERT INTO data VALUES (?,?,?,?)',[(1,"A'quote",'bad','secretA'),(2,'B','good','secretB'),(3,None,'bad','secretC')])
        con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM data')
        table=compiler.compile_views(con)
        values=con.execute(f'SELECT condition_A FROM {table} ORDER BY __tp_rowid').fetchall()
        assert values==[(1.0,),(0.0,),(None,)]
    with pytest.raises(ValueError,match='普通类别字段'):
        illegal=feat.model_copy(update={'input_field_ids':['fld_target']})
        FeatureCompiler(contract,spec.model_copy(update={'features':[illegal]}))


def test_grouped_temporal_real_duckdb_and_cross_device_partition():
    pytest.importorskip('duckdb', reason='Requires real DuckDB to execute grouped temporal windows')
    import duckdb
    from timepilot.feature_visualization import summarize_preview
    contract=DataContract(asset_id='group',version_id='base',source_sha256='c'*64,revision=2,
       state='confirmed',fields=[
        dict(field_id='fld_machine',source_name='device',source_column_index=0,physical_type='VARCHAR',
             semantic_type='id',role='entity_key',confirmation='confirmed'),
        dict(field_id='fld_order',source_name='sample_index',source_column_index=1,physical_type='INTEGER',
             semantic_type='numeric',role='order_index',confirmation='confirmed'),
        dict(field_id='fld_signal',source_name='pressure',source_column_index=2,physical_type='DOUBLE',
             semantic_type='numeric',role='value',confirmation='confirmed'),
       ],entity_field_ids=['fld_machine'])
    spec=FeatureSpecV2(name='grouped',source={'asset_id':'group','version_id':'base','source_sha256':'c'*64},
       contract_revision=2,features=[FeatureDefinition(feature_id='pressure_win',output_name='pressure_avg',
         operator_id='rolling.mean',input_field_ids=['fld_signal'],parameters={
           'window':2,'min_periods':1,'order_field_id':'fld_order','partition_field_ids':['fld_machine']})])
    with duckdb.connect(':memory:') as con:
        con.execute('CREATE TABLE data(__tp_rowid INT,device VARCHAR,sample_index INT,pressure DOUBLE)')
        con.executemany('INSERT INTO data VALUES (?,?,?,?)',[(1,'D1',1,10),(2,'D2',1,100),(3,'D1',2,14),(4,'D2',2,200)])
        con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM data')
        relation=FeatureCompiler(contract,spec).compile_views(con)
        rows=con.execute(f'SELECT device,sample_index,pressure_avg FROM {relation} ORDER BY __tp_rowid').fetchall()
        assert rows==[('D1',1,10.),('D2',1,100.),('D1',2,12.),('D2',2,150.)]
        stats=summarize_preview(con,relation,['pressure_avg'],sample_count=4)[0]
        assert stats['mean']==pytest.approx(68.0)
