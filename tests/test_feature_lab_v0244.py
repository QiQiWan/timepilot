"""Pure validation and project scoped FeatureLab API tests; DuckDB E2E gated."""
from uuid import uuid4
from fastapi.testclient import TestClient
from timepilot.feature_lab import FeatureLab,FeatureStep,FeatureRecipeInput
from test_conversational_bi_v0231 import _env


def test_safe_causal_feature_spec_compilation():
    cols=['Time','A1','A2','device_id'];types={'Time':'BIGINT','A1':'DOUBLE','A2':'DOUBLE','device_id':'VARCHAR'}
    for kind in ('lag','difference','rolling_mean','rolling_std'):
        step=FeatureStep(kind=kind,field='A1',name=f'A1_{kind}',window=5,lag=3,
                         partition_by='device_id',order_by='Time')
        sql=FeatureLab.compile_step(step,cols,types)
        assert 'PARTITION BY "device_id"' in sql
        assert 'ORDER BY "Time", "__tp_rowid"' in sql
        assert 'FOLLOWING' not in sql
    assert 'NULLIF' in FeatureLab.compile_step(FeatureStep(kind='arithmetic',field='A1',
                other_field='A2',operator='/',name='ratio'),cols,types)
    import pytest
    with pytest.raises(ValueError):
        FeatureLab.compile_step(FeatureStep(kind='lag',field='nonexistent',name='x'),cols,types)
    with pytest.raises(ValueError):
        FeatureLab.compile_step(FeatureStep(kind='rolling_mean',field='device_id',name='x'),cols,types)
    with pytest.raises(ValueError):
        FeatureStep(kind='lag',field='A1',name='x; DROP TABLE jobs')


def test_feature_lab_catalog_review_workflow(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        native=app.state.native_bi
        native.verify=lambda _id:{'valid':True}
        base=f'/api/v1/projects/{pid}/feature-lab'
        payload={'asset_id':aid,'version_id':'base','name':'设备状态基础特征',
                 'steps':[{'kind':'lag','field':'A1','name':'A1_lag_1','lag':1},
                          {'kind':'difference','field':'A1','name':'A1_diff_1','lag':1},
                          {'kind':'rolling_mean','field':'A2','name':'A2_ma_5','window':5}]}
        made=c.post(base+'/recipes',json=payload)
        assert made.status_code==201,made.text
        rid=made.json()['recipe_id']
        assert made.json()['state']=='draft'
        assert len(c.get(base+'/recipes').json())==1
        assert c.get(base+'/recipes/'+rid).status_code==200
        bad=c.post(base+'/recipes',json={**payload,'name':'非法窗口','steps':[{'kind':'lag','field':'wrong','name':'x'}]})
        assert bad.status_code==422
        approved=c.post(base+'/recipes/'+rid+'/approve',json={'confirm':True,'reason':'确认特征'})
        assert approved.status_code==422
        requested=c.post(base+'/recipes/'+rid+'/request-review')
        assert requested.status_code==200,requested.text
        reviewed=c.post(base+'/recipes/'+rid+'/approve',json={'confirm':True,'reason':'逐项复核数值类型及无未来泄漏'})
        assert reviewed.status_code==200,reviewed.text
        assert reviewed.json()['state']=='approved'
        with app.state.projects._db() as db:
            recorded=db.execute('SELECT event_type,from_state,to_state FROM feature_recipe_events WHERE recipe_id=? ORDER BY created_at,rowid',(rid,)).fetchall()
        assert [e['event_type'] for e in recorded]==['create','request_review','approve']
        assert recorded[-1]['to_state']=='approved'
        other=c.get(f'/api/v1/projects/{uuid4()}/feature-lab/recipes/{rid}')
        assert other.status_code in (403,404)
    finally:
        c.__exit__(None,None,None)
