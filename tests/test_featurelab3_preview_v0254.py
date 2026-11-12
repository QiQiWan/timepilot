"""v0.25.4: preview queue vs immutable materialization and time-window SQL."""
from uuid import uuid4
import pytest
from test_conversational_bi_v0231 import _env
from test_featurelab2_contract_registry_v0247 import _spec
from timepilot.feature_contracts import DataContract,FeatureDefinition,SourceRef,FeatureSpecV2
from timepilot.feature_compiler import FeatureCompiler


def test_preview_is_a_separate_bounded_read_only_job(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        doc=app.state.feature_semantic.latest(pid,aid,'base')
        made=c.post(base+'/sets',json={'spec':_spec(doc),'request_id':'new-preview-set'})
        sid=made.json()['set_id'];assert made.status_code==201
        response=c.post(base+f'/sets/{sid}/preview-jobs',json={
            'request_id':'preview-req','max_rows':20,'scan_budget':500000})
        assert response.status_code==202,response.text
        task=response.json();assert task['kind']=='preview' and task['status']=='queued'
        again=c.post(base+f'/sets/{sid}/preview-jobs',json={
            'request_id':'preview-req','max_rows':20,'scan_budget':500000})
        assert again.json()['job_id']==task['job_id']
        with app.state.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM feature_runs WHERE set_id=?',(sid,)).fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM feature_artifacts WHERE project_id=?',(pid,)).fetchone()[0]==0
        invalid=c.post(base+f'/sets/{sid}/preview-jobs',json={
            'request_id':'too-many','max_rows':10000,'scan_budget':500000})
        assert invalid.status_code==422
        oversized=c.post(base+f'/sets/{sid}/preview-jobs',json={
            'request_id':'too-big','max_rows':20,'scan_budget':10000000})
        assert oversized.status_code==422
    finally:c.__exit__(None,None,None)


def test_duration_window_uses_confirmed_timestamp_and_partition():
    fields=[
      {'field_id':'fld_machine','source_name':'machine','source_column_index':0,
       'physical_type':'VARCHAR','semantic_type':'id','role':'entity_key','confirmation':'confirmed'},
      {'field_id':'fld_timestamp','source_name':'sample_at','source_column_index':1,
       'physical_type':'TIMESTAMP','semantic_type':'datetime','role':'event_time','confirmation':'confirmed'},
      {'field_id':'fld_signal','source_name':'A2','source_column_index':2,
       'physical_type':'DOUBLE','semantic_type':'numeric','role':'value','confirmation':'confirmed'},
    ]
    contract=DataContract(asset_id='a',version_id='base',source_sha256='e'*64,
         fields=fields,revision=2,state='confirmed',event_time_field_id='fld_timestamp',
         entity_field_ids=['fld_machine'],timezone='Asia/Shanghai')
    spec=FeatureSpecV2(name='duration',source=SourceRef(asset_id='a',version_id='base',source_sha256='e'*64),
      contract_revision=2,features=[FeatureDefinition(feature_id='mean',output_name='A2_mean30min',
        operator_id='rolling.duration.mean',input_field_ids=['fld_signal'],
        parameters={'window_seconds':1800,'min_periods':1,'order_field_id':'fld_timestamp'})])
    clause=FeatureCompiler(contract,spec).expression(spec.features[0])
    assert 'PARTITION BY "machine"' in clause
    assert 'ORDER BY "sample_at" RANGE BETWEEN INTERVAL' in clause
    assert '1800 seconds' in clause and 'FOLLOWING' not in clause
    no_time=contract.model_copy(update={'event_time_field_id':None,'timezone':None})
    with pytest.raises(ValueError,match='时长窗口'):
        FeatureCompiler(no_time,spec).expression(spec.features[0])
