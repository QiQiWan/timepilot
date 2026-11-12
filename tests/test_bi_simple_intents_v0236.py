from uuid import uuid4
from test_conversational_bi_v0231 import _env,_workspace
from timepilot.bi_intent_resolver import BIIntentResolver


def test_schema_and_asset_inspect_are_read_only_even_with_active_llm(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);wid=w['workspace_id'];url=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}'
        service.models=None
        for msg,tool in [('查看数据字段','bi.schema.inspect'),('这张表有哪些列','bi.schema.inspect'),
                         ('这个表有多少行、多少列','bi.asset.describe'),('查看前 20 行','bi.rows.peek')]:
            result=c.post(url+'/turns',json={'message':msg,'expected_revision':1,
                'request_id':str(uuid4()),'execute':True})
            assert result.status_code==200,(msg,result.text)
            data=result.json();assert data['actions'][0]['tool']==tool
            assert data['read_results'][0]['tool']==tool
            assert data['result_type']=='read'
            assert data['workspace']['revision']==1
            assert data['changed'] is False
            assert data['executed'] is True
        schema=c.post(url+'/turns',json={'message':'查看数据字段','expected_revision':1,
            'request_id':str(uuid4())}).json()
        assert [x['name'] for x in schema['result']['fields']]==['Time','A1','A2','A5']
        with service.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM bi_agent_events WHERE workspace_id=?',(wid,)).fetchone()[0]==0
    finally:c.__exit__(None,None,None)


def test_resolver_never_fabricates_unknown_column():
    out=BIIntentResolver().resolve('请统计不存在的A99列',[{'name':'A1','physical_type':'DOUBLE'}])
    assert 'A99' in out['unresolved_fields']
