"""Real HTTP / durable SQLite planning, with only DuckDB operation mocked."""
from uuid import uuid4
from test_conversational_bi_v0231 import _env,_workspace


def test_reasoning_schema_read_durable_and_retry(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);root=f'/api/v1/projects/{pid}/bi-agent/workspaces/{w["workspace_id"]}'
        rid=str(uuid4())
        request={'message':'查看数据字段','expected_revision':1,'request_id':rid}
        created=c.post(root+'/reasoning-plans',json=request)
        assert created.status_code==201,created.text
        doc=created.json();assert doc['status']=='ready',doc
        assert [x['tool'] for x in doc['plan']['tool_calls']]==['bi.schema.inspect']
        assert c.post(root+'/reasoning-plans',json=request).json()['run_id']==doc['run_id']
        result=c.post(root+'/reasoning-plans/'+doc['run_id']+'/apply',json={'request_id':str(uuid4())})
        assert result.status_code==200,result.text
        assert result.json()['status']=='succeeded'
        assert result.json()['execution']['result_type']=='read'
        assert c.get(root).json()['revision']==1
        before=c.get(root+'/reasoning-plans/'+doc['run_id'])
        assert before.status_code==200
        assert before.json()['steps'][0]['status']=='succeeded'
        assert c.post(root+'/reasoning-plans/'+doc['run_id']+'/apply',json={'request_id':str(uuid4())}).status_code==200
        unknown=c.post(root+'/reasoning-plans',json={'message':'检查A99列的统计信息',
                   'expected_revision':1,'request_id':str(uuid4())})
        assert unknown.status_code==201,unknown.text
        assert unknown.json()['status']=='blocked'
        assert not unknown.json()['plan']['tool_calls']
    finally:c.__exit__(None,None,None)


def test_reasoning_mutation_waits_for_browser_and_stale(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);root=f'/api/v1/projects/{pid}/bi-agent/workspaces/{w["workspace_id"]}'
        # Fake native execution is deliberately not permission to pretend the
        # entire Perspective remote stack exists: the planner must block.
        plan=c.post(root+'/reasoning-plans',json={'message':'按 A1 分组求 A2 平均值，绘制柱状图',
                  'expected_revision':1,'request_id':str(uuid4())})
        assert plan.status_code==201,plan.text
        doc=plan.json();assert doc['status'] in ('ready','blocked','needs_clarification')
        assert doc['status']!='succeeded'
        snap=c.get(root+'/perspective/capabilities').json()
        assert not snap['runtime']['virtual_server']
        assert not any(x['available'] for x in snap['features'] if x['feature_id']=='chart.line')
    finally:c.__exit__(None,None,None)
