"""Project boundaries, asset-scoped preferences, plan state safety."""
from __future__ import annotations
from uuid import uuid4
from test_conversational_bi_v0231 import _env,_workspace
from test_universal_import_v0221 import _make_project
from timepilot.bi_feasibility import BIFeasibility
from timepilot.bi_intent_resolver import BIIntentResolver


def test_reference_whitelist_and_incomplete_operator():
    cols=[{'name':'Time','physical_type':'VARCHAR'}, {'name':'A1','physical_type':'DOUBLE'},
          {'name':'A2','physical_type':'VARCHAR'}]
    resolver=BIIntentResolver()
    matched=resolver.resolve('请看 A1 与 A99 的相关性热力图',cols)
    assert matched['unresolved_fields']==['A99']
    f=BIFeasibility().check(matched,{'columns':cols},{},duckdb_ready=True)
    assert f['status']=='needs_clarification'
    correlated=resolver.resolve('相关矩阵',cols)
    assert BIFeasibility().check(correlated,{'columns':cols},{},duckdb_ready=True)['status']=='unsupported'
    for message,expected in [('查看一下波动','trend'),('比较几个设备','compare'),('看一下分布','profile')]:
        r=resolver.resolve(message,cols)
        assert r['intent_candidates']==[expected]
        assert BIFeasibility().check(r,{'columns':cols},{},duckdb_ready=True)['status']=='unsupported'


def test_preferences_validation_and_cross_project_isolation(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/bi-agent/ui-preferences'
        read=c.get(base,params={'asset_id':aid})
        assert read.status_code==200,read.text
        assert read.json()['mode']=='closed'
        written=c.patch(base,json={'asset_id':aid,'mode':'minimized','width':620})
        assert written.status_code==200,written.text
        assert c.get(base,params={'asset_id':aid}).json()['width']==620
        for bad in ({'mode':'evil'},{'width':8192},{'last_workspace_id':str(uuid4())}):
            invalid=c.patch(base,json={'asset_id':aid,**bad})
            assert invalid.status_code in (404,422),invalid.text
        another=_make_project(c,'其他项目')
        forbidden=c.get(f'/api/v1/projects/{another}/bi-agent/ui-preferences',params={'asset_id':aid})
        assert forbidden.status_code in (403,404,422),forbidden.text
        assert c.get(base,params={'asset_id':aid}).json()['mode']=='minimized'
    finally:c.__exit__(None,None,None)


def test_cancel_does_not_create_execution_or_false_success(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        workspace=_workspace(c,pid,aid);wid=workspace['workspace_id']
        base=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/analysis-plans'
        plan=c.post(base,json={'message':'相关性热力图','expected_revision':1,'request_id':str(uuid4())})
        assert plan.status_code==201,plan.text
        url=base+'/'+plan.json()['plan_id']
        cancelled=c.post(url+'/cancel',json={'plan_revision':1})
        assert cancelled.status_code==200,cancelled.text
        assert cancelled.json()['status']=='cancelled'
        run=c.post(url+'/apply',json={'plan_revision':1,'request_id':str(uuid4()),'confirm':True})
        assert run.status_code in (409,422),run.text
        with service.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM bi_analysis_executions').fetchone()[0]==0
            assert db.execute("SELECT status FROM bi_analysis_plans WHERE plan_id=?",(plan.json()['plan_id'],)).fetchone()[0]=='cancelled'
    finally:c.__exit__(None,None,None)
