from uuid import uuid4
from test_conversational_bi_v0231 import _env,_workspace
from test_universal_import_v0221 import _make_project


def test_asset_search_trash_restore_and_project_scope(tmp_path):
    app,c,pid,native_id,service=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/catalog'
        result=c.get(base+'/assets',params={'q':'Time','page_size':10})
        assert result.status_code==200,result.text
        doc=result.json();assert doc['total']==1,doc
        aid=doc['items'][0]['asset_id'];assert doc['items'][0]['native_asset_id']==native_id
        before=c.get(base+'/assets/'+aid);assert before.status_code==200,before.text
        assert len(before.json()['asset']['fields'])==4
        assert c.get(base+'/assets/'+aid+'/impact').status_code==200
        denied=c.post(base+'/assets/'+aid+'/trash',json={'request_id':str(uuid4()),'confirm':False})
        assert denied.status_code==422
        rid=str(uuid4())
        trashed=c.post(base+'/assets/'+aid+'/trash',json={
            'request_id':rid,'expected_revision':0,'reason':'目录测试','confirm':True})
        assert trashed.status_code==200,trashed.text
        assert trashed.json()['files_deleted'] is False
        assert c.get(base+'/assets').json()['total']==0
        assert c.get(base+'/asset-trash').json()['total']==1
        assert c.get(f'/api/v1/projects/{pid}/native-assets/{native_id}').status_code==404
        assert c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces',json={
            'asset_id':native_id,'version_id':'base'}).status_code==404
        replay=c.post(base+'/assets/'+aid+'/trash',json={
            'request_id':rid,'expected_revision':0,'confirm':True})
        assert replay.status_code==200 and replay.json()['duplicate']
        old=c.post(base+'/assets/'+aid+'/restore',json={
            'request_id':str(uuid4()),'expected_revision':0})
        assert old.status_code==409
        resumed=c.post(base+'/assets/'+aid+'/restore',json={
            'request_id':str(uuid4()),'expected_revision':1})
        assert resumed.status_code==200,resumed.text
        assert c.get(base+'/assets').json()['items'][0]['asset_id']==aid
        assert c.get(base+'/assets/'+aid).json()['asset']['source_hash']==before.json()['asset']['source_hash']
        another=_make_project(c)
        assert c.get(f'/api/v1/projects/{another}/catalog/assets/{aid}').status_code==404
        assert c.post(f'/api/v1/projects/{another}/catalog/assets/{aid}/trash',json={
            'request_id':str(uuid4()),'confirm':True}).status_code==404
    finally:c.__exit__(None,None,None)
