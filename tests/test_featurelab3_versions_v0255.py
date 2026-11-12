"""v0.25.5: source and revision pinned, viewer optimistic concurrency."""
from uuid import uuid4
from test_conversational_bi_v0231 import _env
from test_featurelab2_contract_registry_v0247 import _spec
from timepilot.feature_contracts import FeatureSpecV2


def test_immutable_revisions_diff_and_scoped_viewer_save(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        source=app.state.feature_semantic.latest(pid,aid,'base')
        spec=_spec(source)
        created=c.post(base+'/sets',json={'spec':spec,'request_id':'rev-create'})
        assert created.status_code==201,created.text
        sid=created.json()['set_id']
        newer={**spec,'name':'工业基础特征v2'}
        newer['features']=[{**f} for f in spec['features']]
        newer['features'][1]['parameters']={'window':4,'min_periods':2}
        updated=c.patch(base+'/sets/'+sid,json={'spec':newer,'expected_revision':1})
        assert updated.status_code==200,updated.text
        assert updated.json()['revision']==2
        snapshots=c.get(base+f'/sets/{sid}/revisions').json()['revisions']
        assert [v['revision'] for v in snapshots][:2]==[2,1]
        v1=c.get(base+f'/sets/{sid}/revisions/1').json()
        assert v1['spec']['features'][1]['parameters']['window']==3
        diff=c.get(base+f'/sets/{sid}/diff',params={'left':1,'right':2})
        assert diff.status_code==200
        assert len(diff.json()['changed'])==1
        original=c.get(base+f'/sets/{sid}/viewer-state')
        assert original.status_code==200 and original.json()['viewer_revision']==0
        body={'workspace':{'panels':{'source':{'plugin':'Datagrid'}},'layout':{}},
              'source_sha256':source.source_sha256,'expected_revision':0}
        done=c.patch(base+f'/sets/{sid}/viewer-state',json=body)
        assert done.status_code==200,done.text
        assert done.json()['viewer_revision']==1
        stale=c.patch(base+f'/sets/{sid}/viewer-state',json=body)
        assert stale.status_code==409,stale.text
        assert c.get(base+f'/sets/{sid}/viewer-state').json()['workspace']==body['workspace']
        wrong={**body,'source_sha256':'bad-hash','expected_revision':1}
        assert c.patch(base+f'/sets/{sid}/viewer-state',json=wrong).status_code==422
    finally:c.__exit__(None,None,None)
