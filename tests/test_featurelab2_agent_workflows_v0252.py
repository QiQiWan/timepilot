"""Deterministic agent requests, bounded planning, no false executor claims."""
from uuid import uuid4
from test_conversational_bi_v0231 import _env


def test_multifield_moving_average_creates_safe_draft(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        root=f'/api/v1/projects/{pid}/feature-lab'
        r=c.post(root+'/agent/plans',json={'message':'对 A1 和 A2 计算移动平均，放到新列',
            'asset_id':aid,'request_id':'moving-average-A1-A2'})
        assert r.status_code==201,r.text
        plan=r.json()['plan']
        assert plan['status']=='ready'
        assert plan['actions'][0]['tool_id']=='feature.set.draft_create'
        spec=plan['actions'][0]['args']['spec']
        assert [x['output_name'] for x in spec['features']]==['A1_ma5','A2_ma5']
        assert all(x['parameters']['window']==5 for x in spec['features'])
        done=c.post(root+'/agent/plans/'+r.json()['plan_id']+'/execute')
        assert done.status_code==200,done.text
        assert done.json()['status']=='succeeded'
        assert done.json()['steps'][0]['result']['state']=='draft'
        assert c.post(root+'/agent/plans',json={'message':'对 A1 和 A2 计算移动平均，放到新列',
            'asset_id':aid,'request_id':'moving-average-A1-A2'}).json()['plan_id']==r.json()['plan_id']
        unsupported=c.post(root+'/agent/plans',json={'message':'对 A1 和 A2 计算过去30分钟的移动平均',
            'asset_id':aid,'request_id':'duration-is-not-rows'})
        assert unsupported.status_code==201
        assert unsupported.json()['plan']['status']=='needs_clarification'
        assert unsupported.json()['plan']['actions']==[]
        assert c.get(f'/api/v1/projects/{uuid4()}/feature-lab/tools',params={'asset_id':aid}).status_code in (403,404)
    finally:c.__exit__(None,None,None)
