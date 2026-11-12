"""v0.23.7/9 contract checks with explicit simulated native execution."""
from uuid import uuid4
from test_conversational_bi_v0231 import _env,_workspace


def test_snapshot_revision_scope_source_and_idempotence(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        ws=_workspace(c,pid,aid)
        root=f'/api/v1/projects/{pid}/bi-agent/workspaces/{ws["workspace_id"]}'
        initial=c.get(root+'/snapshot')
        assert initial.status_code==200,initial.text
        assert initial.json()['viewer_revision']==0
        token={'panels':{'main':{'table':'memory.timepilot_active','plugin':'Datagrid','columns':['Time','A1']}},
               'layout':{},'derived_tables':{}}
        request={'workspace_revision':1,'viewer_revision':0,'request_id':str(uuid4()),'token':token}
        stored=c.patch(root+'/snapshot',json=request)
        assert stored.status_code==200,stored.text
        assert stored.json()['viewer_revision']==1
        assert c.patch(root+'/snapshot',json=request).json()['viewer_revision']==1
        bad=c.patch(root+'/snapshot',json={**request,'request_id':str(uuid4())})
        assert bad.status_code==409,bad.text
        evil={'panels':{'main':{'table':'other_project.private_data','plugin':'Datagrid'}}}
        attack=c.patch(root+'/snapshot',json={**request,'request_id':str(uuid4()),'viewer_revision':1,'token':evil})
        assert attack.status_code==422,attack.text
        ack=c.post(root+'/render-receipts',json={'request_id':str(uuid4()),'viewer_revision':1,
             'source_sha256':ws['source_sha256'],'panel_id':'main','plugin':'Datagrid','success':True})
        assert ack.status_code==200,ack.text
        denied=c.post(root+'/render-receipts',json={'request_id':str(uuid4()),'viewer_revision':1,
             'source_sha256':'0'*64,'panel_id':'main','plugin':'Datagrid','success':True})
        assert denied.status_code==409,denied.text
        forged=c.post(root+'/render-receipts',json={'request_id':str(uuid4()),'viewer_revision':1,
             'source_sha256':ws['source_sha256'],'panel_id':'main','plugin':'Y Bar','success':True})
        assert forged.status_code==422,forged.text
        assert c.get(root+'/perspective/capabilities').status_code==200
        manifest=c.get(root+'/perspective/tools')
        assert manifest.status_code==200,manifest.text
        assert any(t['tool']=='bi.schema.inspect' for t in manifest.json()['tools'])
    finally:c.__exit__(None,None,None)
