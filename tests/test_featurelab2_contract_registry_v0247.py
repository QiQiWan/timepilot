"""FeatureLab 2.0 contract & registry HTTP tests, no DuckDB simulation."""
from uuid import uuid4
from test_conversational_bi_v0231 import _env
from timepilot.feature_contracts import DataContract,FeatureSpecV2,SourceRef,FeatureDefinition
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_operator_registry import discover


def _spec(contract):
    ids={f.source_name:f.field_id for f in contract.fields}
    return {'schema_version':2,'name':'工业基础特征','source':{
            'asset_id':contract.asset_id,'version_id':contract.version_id,
            'source_sha256':contract.source_sha256},'contract_revision':contract.revision,
            'features':[{'feature_id':'feat_add','output_name':'A1_plus_A2',
                  'operator_id':'arithmetic.binary','operator_version':'2.0',
                  'input_field_ids':[ids['A1'],ids['A2']],
                  'parameters':{'operator':'+'}},
                  {'feature_id':'feat_roll','output_name':'A1_ma3',
                   'operator_id':'rolling.mean','input_field_ids':[ids['A1']],
                   'parameters':{'window':3,'min_periods':2}}]}


def test_data_contract_and_asset_registration(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        r=c.get(base+'/contracts',params={'asset_id':aid})
        assert r.status_code==200,r.text
        doc=r.json();assert doc['revision']==0
        assert [f['source_name'] for f in doc['fields']]==['Time','A1','A2','A5']
        ids=[f['field_id'] for f in doc['fields']]
        assert len(set(ids))==4
        changes={'expected_revision':0,'patch':{'fields':[{'field_id':ids[1],
            'display_name':'信号 A1','confirmation':'confirmed','unit':'kPa'}]}}
        edited=c.patch(base+'/contracts/'+aid,json=changes)
        assert edited.status_code==200,edited.text
        assert edited.json()['revision']==1
        assert c.patch(base+'/contracts/'+aid,json=changes).status_code==409
        confirm=c.post(base+'/contracts/'+aid+'/confirm',json={'expected_revision':1})
        assert confirm.status_code==200,confirm.text
        assert confirm.json()['state']=='confirmed'
        candidate=c.get(base+'/fields/search',params={'asset_id':aid,'q':'A1'}).json()
        assert candidate['total']==1
        wrong=c.patch(base+'/contracts/'+aid,json={'expected_revision':2,
              'patch':{'fields':[{'field_id':ids[1],'source_name':'rewritten'}]}})
        assert wrong.status_code==422
        assert c.get(f'/api/v1/projects/{uuid4()}/feature-lab/contracts',params={'asset_id':aid}).status_code in (403,404)
    finally:c.__exit__(None,None,None)


def test_feature_set_dag_jobs_idempotency_and_agent(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        document=app.state.feature_semantic.latest(pid,aid,'base')
        spec=_spec(document)
        result=c.post(base+'/sets',json={'spec':spec,'request_id':'same-create'})
        assert result.status_code==201,result.text
        sid=result.json()['set_id']
        assert c.post(base+'/sets',json={'spec':spec,'request_id':'same-create'}).json()['set_id']==sid
        assert c.post(base+'/sets/validate',json=spec).status_code==200
        run=c.post(base+f'/sets/{sid}/runs',json={'request_id':'material-1'})
        assert run.status_code==202,run.text
        rid=run.json()['job_id']
        assert c.post(base+f'/sets/{sid}/runs',json={'request_id':'material-1'}).json()['job_id']==rid
        assert c.get(base+f'/runs/{rid}').json()['status']=='queued'
        assert app.state.feature_jobs.work_once() is True
        failed=c.get(base+f'/runs/{rid}').json()
        assert failed['status'] in ('failed_retryable','failed_terminal')
        assert failed['error_code'] and failed['result'] is None
        assert c.get(base+f'/runs/{rid}/result').status_code==409
        assert c.get(base+f'/sets/{sid}/publication-preview').json()['artifact_ready'] is False
        agent=c.post(base+'/agent/plans',json={'message':'查看数据字段',
              'asset_id':aid,'request_id':'schema-req','version_id':'base'})
        assert agent.status_code==201,agent.text
        plan=agent.json();assert plan['plan']['actions'][0]['tool_id']=='feature.schema.inspect'
        executed=c.post(base+'/agent/plans/'+plan['plan_id']+'/execute')
        assert executed.status_code==200,executed.text
        assert executed.json()['status']=='succeeded'
        assert len(executed.json()['steps'][0]['result']['fields'])==4
        math=c.post(base+'/agent/plans',json={'message':'将 A1 与 A2 相加放到新列',
             'asset_id':aid,'request_id':'math-req','version_id':'base'})
        assert math.status_code==201,math.text
        assert math.json()['plan']['actions'][0]['tool_id']=='feature.set.draft_create'
        applied=c.post(base+'/agent/plans/'+math.json()['plan_id']+'/execute')
        assert applied.status_code==200,applied.text
        assert applied.json()['status']=='succeeded'
        assert applied.json()['steps'][0]['result']['set_id']
        bad=spec.copy();bad['features'][0]['input_field_ids']=['fld_unknown',spec['features'][0]['input_field_ids'][1]]
        r=c.post(base+'/sets/validate',json=bad)
        assert r.status_code==422
    finally:c.__exit__(None,None,None)


def test_feature_compiler_dag_and_no_future():
    fields=[{'field_id':'fld_1','source_name':'A1','source_column_index':0,'physical_type':'DOUBLE'},
            {'field_id':'fld_2','source_name':'A2','source_column_index':1,'physical_type':'DOUBLE'}]
    c=DataContract(asset_id='a',version_id='base',source_sha256='1'*64,fields=fields)
    spec=FeatureSpecV2(name='safe',source=SourceRef(asset_id='a',version_id='base',source_sha256='1'*64),
         contract_revision=0,features=[FeatureDefinition(feature_id='sum',output_name='sum',
           operator_id='arithmetic.binary',input_field_ids=['fld_1','fld_2']),
           FeatureDefinition(feature_id='rolling',output_name='sum_ma',operator_id='rolling.mean',
             input_field_ids=['sum'],parameters={'window':5,'min_periods':3})])
    f=FeatureCompiler(c,spec)
    assert '("A1" + "A2")' in f.expression(f.order[0])
    assert '"sum"' in f.expression(f.order[1])
    assert 'CURRENT ROW' in f.expression(f.order[1])
    assert 'FOLLOWING' not in f.expression(f.order[1])
    invalid=spec.model_copy(update={'features':[
       spec.features[0].model_copy(update={'input_field_ids':['rolling','fld_2']}),spec.features[1]]})
    import pytest
    with pytest.raises(ValueError,match='循环'):FeatureCompiler(c,invalid)
    assert discover(physical_types=['DOUBLE'])[0]['available']
    assert not [x for x in discover(physical_types=['DOUBLE']) if x['operator_id']=='ewma'][0]['available']
