"""P0 and P1 regression: order determinism, entity isolation and goal semantics.

SQLite executes the same ANSI window clauses for a local numerics smoke test;
real DuckDB remains an independent integration test on the target runtime.
"""
from __future__ import annotations
import json
import sqlite3
import pytest
from timepilot.feature_contracts import DataContract,FieldContract,FeatureDefinition,FeatureSpecV2
from timepilot.feature_topology import infer_topology
from timepilot.feature_temporal_profile import temporal_diagnostics
from timepilot.feature_candidates import propose
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_auto_analysis import classify_goal,interpret_goal,advanced_algorithm_advice


def contract(*, entity=True, confirmed=True, duplicate=False):
    fields=[FieldContract(field_id='fld_device',source_name='machine_id',source_column_index=0,
                   physical_type='VARCHAR',semantic_type='id',role='entity_key' if entity else 'unspecified',
                   confirmation='confirmed' if entity and confirmed else 'unconfirmed'),
            FieldContract(field_id='fld_seq',source_name='Time',source_column_index=1,
                   physical_type='INTEGER',semantic_type='numeric',role='order_index' if confirmed else 'unspecified',
                   confirmation='confirmed' if confirmed else 'unconfirmed'),
            FieldContract(field_id='fld_pressure',source_name='pressure',source_column_index=2,
                   physical_type='DOUBLE',semantic_type='numeric',role='value',confirmation='confirmed')]
    return DataContract(asset_id='sample',version_id='base',source_sha256='a'*64,
                        state='confirmed' if confirmed else 'needs_review',fields=fields,
                        entity_field_ids=['fld_device'] if entity else [])


def sql_db(rows):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE __tp_effective ("__tp_rowid" INTEGER, "machine_id" TEXT, "Time" INT, "pressure" REAL)')
    db.executemany('INSERT INTO __tp_effective VALUES (?,?,?,?)',
                   [(i+1,*r) for i,r in enumerate(rows)])
    return db


def test_windows_use_confirmed_numeric_sequence_and_isolate_entity():
    c=contract()
    rows=[('A',3,30),('B',2,200),('A',1,10),('B',1,100),('A',2,20),('B',3,300)]
    db=sql_db(rows)
    diag=temporal_diagnostics(db,c)
    assert diag['status']=='verified' and diag['safe_for_windows']
    assert diag['out_of_order_source_rows']>0
    t=infer_topology(c,diag)
    assert t['safe_for_windows'] and t['partition_field_ids']==['fld_device']
    features=propose(c,template='basic',budget=2,topology=t)
    assert len(features)==2
    assert all(f['parameters']['order_field_id']=='fld_seq' for f in features)
    assert all(f['parameters']['partition_field_ids']==['fld_device'] for f in features)
    feat=FeatureDefinition.model_validate({k:v for k,v in features[0].items() if k in FeatureDefinition.model_fields});
    spec=FeatureSpecV2(name='P0',source={'asset_id':'sample','version_id':'base','source_sha256':'a'*64},
                       contract_revision=0,features=[feat])
    compiler=FeatureCompiler(c,spec)
    compiler._verify_order_keys(db,'__tp_effective')
    # SQLite supports SQL window syntax but uses CAST rather than DuckDB TRY_CAST.
    expr=compiler.expression(feat).replace('TRY_CAST(', 'CAST(')
    actual=db.execute(f'SELECT machine_id,Time,{expr} AS value FROM __tp_effective ORDER BY "__tp_rowid"').fetchall()
    assert [(r[0],r[1],round(r[2],2)) for r in actual]==[
        ('A',3,20.0),('B',2,150.0),('A',1,10.0),('B',1,100.0),('A',2,15.0),('B',3,200.0)]
    # Shuffle import rows, then compare by immutable entity+order keys.
    other=sql_db(list(reversed(rows)))
    compiler._verify_order_keys(other,'__tp_effective')
    out=other.execute(f'SELECT machine_id,Time,{expr} AS value FROM __tp_effective').fetchall()
    normalized=lambda arr:{(a,b):round(c,4) for a,b,c in arr}
    assert normalized(actual)==normalized(out)


def test_duplicate_and_missing_keys_block_full_source_windows():
    c=contract()
    for bad in ([('A',1,5),('A',1,6)], [('A',None,5),('B',2,7)], [(None,1,4)]):
        db=sql_db(bad)
        result=temporal_diagnostics(db,c)
        assert not result['safe_for_windows']
        assert not infer_topology(c,result)['can_propose_windows'] or not infer_topology(c,result)['safe_for_windows']
        items=propose(c,template='temporal',budget=8,topology=infer_topology(c,result))
        assert all(not x['operator_id'].startswith('rolling.') for x in items)
        feat=FeatureDefinition(feature_id='feat1',output_name='mean5',operator_id='rolling.mean',
           input_field_ids=['fld_pressure'],parameters={'window':5,'min_periods':1,
               'order_field_id':'fld_seq','partition_field_ids':['fld_device']})
        spec=FeatureSpecV2(name='bad',source={'asset_id':'sample','version_id':'base','source_sha256':'a'*64},
                           contract_revision=0,features=[feat])
        with pytest.raises(ValueError,match='空值|重复'):
            FeatureCompiler(c,spec)._verify_order_keys(db,'__tp_effective')


def test_unconfirmed_order_or_unconfirmed_machine_never_proposes_row_windows():
    for c in [contract(confirmed=False), contract(entity=False,confirmed=True)]:
        topo=infer_topology(c)
        assert not topo['can_propose_windows']
        items=propose(c,template='temporal',budget=12)
        assert items and all(x['operator_id'] in ('transform.abs','arithmetic.binary') for x in items)


def test_explicit_time_window_cannot_be_redirected_to_another_unconfirmed_field():
    c=contract()
    feat=FeatureDefinition(feature_id='f',output_name='x',operator_id='rolling.mean',
                           input_field_ids=['fld_pressure'],parameters={'window':5,'min_periods':1,
                             'order_field_id':'fld_pressure','partition_field_ids':['fld_device']})
    spec=FeatureSpecV2(name='x',source={'asset_id':'sample','version_id':'base','source_sha256':'a'*64},
                       contract_revision=0,features=[feat])
    with pytest.raises(ValueError,match='确认'):
        FeatureCompiler(c,spec).expression(feat)


@pytest.mark.parametrize(('goal','expected'),[
 ('预测不良率','regression'),('分析分类特征的统计分布','explore'),
 ('预测设备故障是否发生','classification'),('质量分类','classification'),
 ('未来销量预测','forecast'),('设备工况异常分析','anomaly')])
def test_evidence_based_intent(goal,expected):
    assert classify_goal(goal)==expected
    detailed=interpret_goal(goal,contract=contract())
    assert detailed['objective']==expected and 0<detailed['confidence']<=1


def test_highcost_algorithms_are_opt_in_without_full_topology_evidence():
    c=contract()
    assert advanced_algorithm_advice(c,{'rows_scanned':100000,'topology':{'safe_for_windows':False}},'quality')==[]
    a=advanced_algorithm_advice(c,{'rows_scanned':100000,
         'topology':{'safe_for_windows':True,'partition_field_ids':['fld_device']}},'quality')
    assert {x['engine'] for x in a}=={'tsfresh','featuretools'}
    assert all(x['requires_confirmation'] for x in a)
    assert next(x for x in a if x['engine']=='featuretools')['eligible'] is False


def test_internal_field_names_not_sent_to_external_optional_model():
    from timepilot.feature_auto_analysis import FeatureAutoAnalysis
    class Model:
        sent=''
        def config(self):return {'enabled':True,'has_api_key':True}
        def _message(self,messages,max_tokens=500):
            self.sent=messages[1]['content']
            return {'content':json.dumps({'field_ids':['fld_pressure'],'template':'quality','reason':'bounded'})}
        def _parse_json(self,out):return json.loads(out)
    c=contract()
    c.fields[2].source_name='internal_secret_pressure'
    m=Model();svc=object.__new__(FeatureAutoAnalysis);svc.models=m
    ids,template,provider,_=svc._llm_strategy('data inspection','quality',c,
        {'fields':[{'field_id':'fld_pressure','null_rate':0.01,'std':3,'distinct_count':70}]},
        ['fld_pressure'],'quality',8)
    assert provider=='llm' and ids==['fld_pressure']
    assert 'internal_secret_pressure' not in m.sent
    assert 'internal_column_' in m.sent
    assert 'std' not in m.sent


def test_auto_accept_and_run_requires_explicit_user_and_is_idempotent(tmp_path):
    """The combined UX action never bypasses human approval or double-enqueues."""
    from uuid import uuid4
    from test_conversational_bi_v0231 import _env
    from test_featurelab32_auto_workflow import simulate_profile, set_job
    app,client,pid,asset,_=_env(tmp_path)
    try:
        prefix=f'/api/v1/projects/{pid}/feature-lab'
        created=client.post(prefix+'/analysis/sessions',json={
            'asset_id':asset,'goal':'探索变化特征','task_hint':'explore',
            'budget':4,'request_id':str(uuid4())})
        assert created.status_code==201,created.text
        start=created.json();sid=start['session_id']
        simulate_profile(app,pid,asset,client.get(prefix+'/contracts',params={'asset_id':asset}).json(),start['profile_job_id'])
        step=client.post(prefix+f'/analysis/sessions/{sid}/advance').json()
        candidates=client.get(prefix+'/candidate-sessions/'+step['candidate_session_id']).json()['candidates']
        for job_id in step['preview_job_ids']:
            set_job(app,job_id,{'quality':[{'feature_name':c['output_name'],
                'rows_scanned':100,'finite_count':98,'null_rate':0.02,
                'constant_on_full_data':False} for c in candidates]})
        review=client.post(prefix+f'/analysis/sessions/{sid}/advance').json()
        assert review['stage']=='review'
        req=str(uuid4())
        bad=client.post(prefix+f'/analysis/sessions/{sid}/accept-and-run',json={
            'name':'QA safe set','request_id':req,'confirmed':False})
        assert bad.status_code in (400,409,422),bad.text
        assert not client.get(prefix+f'/analysis/sessions/{sid}').json().get('feature_set_id')
        yes=client.post(prefix+f'/analysis/sessions/{sid}/accept-and-run',json={
            'name':'QA safe set','request_id':req,'confirmed':True})
        assert yes.status_code==202,yes.text
        saved=yes.json();assert saved['stage']=='materializing' and saved['materialize_job_id']
        same=client.post(prefix+f'/analysis/sessions/{sid}/accept-and-run',json={
            'name':'QA safe set','request_id':req,'confirmed':True})
        assert same.status_code==202 and same.json()['materialize_job_id']==saved['materialize_job_id']
        unauthorized=client.post(prefix+f'/analysis/sessions/{sid}/accept-and-run',json={
            'name':'different','request_id':str(uuid4()),'confirmed':True})
        assert unauthorized.status_code in (400,409,422)
    finally:client.__exit__(None,None,None)


def test_optional_real_duckdb_topology_smoke():
    """Mark real engine as skipped if unavailable; never claim SQLite is DuckDB."""
    duckdb=pytest.importorskip('duckdb')
    c=contract()
    db=duckdb.connect(':memory:')
    try:
        db.execute('CREATE TABLE __tp_effective ("__tp_rowid" INTEGER, "machine_id" VARCHAR, "Time" INTEGER, "pressure" DOUBLE)')
        rows=[('A',3,30),('B',2,200),('A',1,10),('B',1,100),('A',2,20),('B',3,300)]
        db.executemany('INSERT INTO __tp_effective VALUES (?,?,?,?)',[(i+1,*r) for i,r in enumerate(rows)])
        evidence=temporal_diagnostics(db,c)
        assert evidence['safe_for_windows']
        spec_feature=propose(c,template='basic',budget=1,topology=infer_topology(c,evidence))[0]
        feat=FeatureDefinition.model_validate({k:v for k,v in spec_feature.items() if k in FeatureDefinition.model_fields})
        spec=FeatureSpecV2(name='real duckdb test',source={'asset_id':'sample','version_id':'base','source_sha256':'a'*64},contract_revision=0,features=[feat])
        compiler=FeatureCompiler(c,spec)
        output=compiler.compile_views(db)
        values=db.execute(f'SELECT machine_id,"Time",{feat.output_name} FROM {output}').fetchall()
        assert {(a,b):round(v,2) for a,b,v in values}=={('A',3):20.0,('A',2):15.0,('A',1):10.0,('B',3):200.0,('B',2):150.0,('B',1):100.0}
    finally:db.close()


def test_unconfirmed_camelcase_device_identifier_also_blocks_window():
    from timepilot.feature_topology import infer_topology
    c=contract(entity=False,confirmed=True)
    # A deviceId field from real CSVs must not silently lose its partition role.
    c.fields[0].source_name='deviceId'
    topo=infer_topology(c)
    assert not topo['can_propose_windows']
    assert '疑似设备' in ';'.join(topo['reasons'])


def test_large_data_uses_bounded_queue_instead_of_200k_hard_stop(tmp_path, monkeypatch):
    from uuid import uuid4
    from test_conversational_bi_v0231 import _env
    app,client,pid,asset,_=_env(tmp_path)
    try:
        semantic=app.state.feature_semantic
        original=semantic.source
        simulated_rows={'n':200001}
        def source(*args,**kwargs):
            result=dict(original(*args,**kwargs))
            result['row_count']=simulated_rows['n']
            return result
        monkeypatch.setattr(semantic,'source',source)
        prefix=f'/api/v1/projects/{pid}/feature-lab/analysis/sessions'
        ready=client.post(prefix,json={'asset_id':asset,'goal':'分析数值分布',
            'task_hint':'explore','budget':4,'request_id':str(uuid4())})
        assert ready.status_code==201,ready.text
        body=ready.json()
        assert body['status']=='profiling' and body['profile_job_id']
        assert any('全量' in x for x in body['warnings'])
        simulated_rows['n']=2_000_001
        blocked=client.post(prefix,json={'asset_id':asset,'goal':'分析数值分布',
            'task_hint':'explore','budget':4,'request_id':str(uuid4())})
        assert blocked.status_code==201,blocked.text
        body=blocked.json()
        assert body['status']=='needs_input' and not body['profile_job_id']
        assert any('分区' in x for x in body['warnings'])
    finally:client.__exit__(None,None,None)
