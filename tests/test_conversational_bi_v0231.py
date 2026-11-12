"""Project BI tools, revision history, pinned provenance, and source-safe model drafts."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from timepilot.api import create_app
from timepilot.bi_agent_service import BIWorkspaceService
from timepilot.bi_tool_registry import BIAction, ToolBatch, TOOLS, validate_action
from timepilot.bi_query_engine import BIQueryEngine
from timepilot.native_bi import NativeBIError
from test_universal_import_v0221 import _app, _make_project, _upload


def _env(tmp_path):
    app, saved = _app(tmp_path)
    api = TestClient(app)
    api.__enter__()
    pid = _make_project(api)
    content = ('Time,A1,A2,A5\n1,0.1,1,1.1\n2,0.2,0,1.2\n3,0.3,5,1.3\n').encode()
    iid, catalog = _upload(api,pid,content,'housing.csv')
    regions=catalog['files'][0]['worksheets'][0]['regions']
    assert regions
    start = api.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',json={
        'region_ids':[regions[0]['region_id']],'confirm':True})
    assert start.status_code == 202, start.text
    assert app.state.project_publication_jobs.work_once()['status']=='succeeded'
    assets=app.state.projects.generic_for(pid)
    assert assets
    aid=assets[0]['native_asset_id']
    saved[aid][0]['columns']=[{'name':name,'physical_type':'DOUBLE'} for name in ['Time','A1','A2','A5']]
    service=app.state.bi_agent
    service.engine.execute=lambda _pid,state,arrow=False,limit=None: (
        b'arrow-placeholder' if arrow else {'columns':['Time','A1','A2','A5'],
            'rows':[{'Time':1,'A1':0.1,'A2':1,'A5':1.1}], 'returned':1,
            'source':'duckdb_test_double','chart':state['chart'],'filters':state['query']['filters']})
    return app, api, pid, aid, service


def _workspace(api,pid,aid):
    r=api.post(f'/api/v1/projects/{pid}/bi-agent/workspaces',json={'asset_id':aid,'version_id':'base'})
    assert r.status_code==201,r.text
    return r.json()


def _apply(api,pid,wid,revision,actions,confirm=False,request_id=None):
    url=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/apply'
    r=api.post(url,json={'expected_revision':revision,'request_id':request_id or str(uuid4()),
                         'actions':actions,'confirm':confirm})
    return r


def test_v0230_multiturn_filter_sort_chart_save_restore_undo(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        tools=c.get(f'/api/v1/projects/{pid}/bi-agent/tools')
        assert tools.status_code==200 and len(tools.json())>=24
        w=_workspace(c,pid,aid);wid=w['workspace_id']
        base=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}'
        plan=c.post(base+'/turns',json={'message':'筛选 A2 > 0，按 Time 升序排序，绘制 A1、A5 折线图',
            'expected_revision':1,'request_id':str(uuid4()),'execute':False})
        assert plan.status_code==200,plan.text
        actions=[a['tool'] for a in plan.json()['actions']]
        assert actions==['bi.filter.set','bi.sort.set','bi.chart.create'],actions
        assert c.get(base).json()['revision']==1
        turn=c.post(base+'/turns',json={'message':'筛选 A2 > 0，按 Time 升序排序，绘制 A1、A5 折线图',
            'expected_revision':1,'request_id':str(uuid4()),'execute':True})
        assert turn.status_code==200,turn.text
        doc=turn.json();assert doc['workspace']['revision']==2
        assert doc['workspace']['state']['query']['filters'][0]['column']=='A2'
        assert doc['workspace']['state']['chart']['y']==['A1','A5']
        save=c.post(base+'/views',json={'name':'传感器趋势','perspective':{'panels':[{'plugin':'Y Line'}]}})
        assert save.status_code==201,save.text
        vid=save.json()['view_id']
        undo=c.post(base+'/undo',json={'expected_revision':2,'request_id':str(uuid4())})
        assert undo.status_code==200,undo.text
        assert undo.json()['workspace']['state']['query']['filters']==[]
        redo=c.post(base+'/redo',json={'expected_revision':3,'request_id':str(uuid4())})
        assert redo.status_code==200,redo.text
        assert redo.json()['workspace']['state']['chart']['type']=='line'
        clear=_apply(c,pid,wid,4,[{'tool':'bi.filter.remove','arguments':{}}]);assert clear.status_code==200,clear.text
        restore=c.post(base+'/open-view',json={'view_id':vid,'expected_revision':5,'request_id':str(uuid4())})
        assert restore.status_code==200,restore.text
        assert restore.json()['workspace']['state']['query']['filters'][0]['column']=='A2'
        assert restore.json()['perspective']['panels'][0]['plugin']=='Y Line'
        assert len(service.list_views(pid,aid))==1
        assert service.workspace(pid,wid)['revision']==6
        persisted=BIWorkspaceService(app.state.projects,app.state.native_bi,service.platform,
                                     modeling=service.modeling)
        assert persisted.workspace(pid,wid)['revision']==6
    finally:c.__exit__(None,None,None)


def test_v0230_optimistic_lock_idempotence_previews_and_invalid_tool(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);wid=w['workspace_id'];rid=str(uuid4())
        action=[{'tool':'bi.filter.set','arguments':{'column':'A2','op':'gt','value':0}}]
        before=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/preview',
            json={'expected_revision':1,'request_id':str(uuid4()),'actions':action})
        assert before.status_code==200,before.text
        assert before.json()['would_change']
        assert service.workspace(pid,wid)['revision']==1
        r=_apply(c,pid,wid,1,action,request_id=rid);assert r.status_code==200,r.text
        again=_apply(c,pid,wid,1,action,request_id=rid)
        assert again.status_code==200,again.text
        assert again.json()['duplicate'] is True
        old=_apply(c,pid,wid,1,action)
        assert old.status_code==409,old.text
        attack=_apply(c,pid,wid,2,[{'tool':'bi.filter.set','arguments':{
            'column':'A2; DROP TABLE metadata','op':'eq','value':0}}])
        assert attack.status_code==422
        bad=_apply(c,pid,wid,2,[{'tool':'os.system','arguments':{'command':'whoami'}}])
        assert bad.status_code==422
        read=c.get(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/query')
        assert read.status_code==200,read.text
        assert read.json()['source']=='duckdb_test_double'
        other=_make_project(c,'其它部门')
        assert c.get(f'/api/v1/projects/{other}/bi-agent/workspaces/{wid}').status_code==404
    finally:c.__exit__(None,None,None)


def test_v0231_derived_math_join_preview_and_model_scope(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);wid=w['workspace_id']
        expr={'kind':'binary','op':'-','left':{'kind':'column','value':'A5'},
              'right':{'kind':'column','value':'A1'}}
        result=_apply(c,pid,wid,1,[{'tool':'bi.expression.create',
            'arguments':{'name':'sensor_delta','expression':expr}}])
        assert result.status_code==200,result.text
        assert 'sensor_delta' in service._columns(pid,result.json()['workspace']['state'])
        frozen=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/freeze',json={'confirmed':True})
        assert frozen.status_code==422, frozen.text
        rollback=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/undo',
            json={'expected_revision':2,'request_id':str(uuid4())})
        assert rollback.status_code==200,rollback.text
        freeze_called={}
        def stub_freeze(_pid,req):
            freeze_called['filters']=[f.model_dump() for f in req.filters]
            return {'selection_id':'selection-stub','row_count':3,'filters':freeze_called['filters']}
        service.platform.create_selection=stub_freeze
        result=_apply(c,pid,wid,3,[{'tool':'bi.filter.set','arguments':{'column':'A2','op':'gt','value':0}}])
        assert result.status_code==200,result.text
        selection=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/freeze',json={'confirmed':True})
        assert selection.status_code==201,selection.text
        assert freeze_called['filters'][0]['column']=='A2'
        def fake_create_draft(_pid,doc):
            assert doc.selection_id=='selection-stub'
            assert doc.version_id=='base'
            return {'draft_id':'draft-stub','status':'review_required'}
        service.modeling.create_draft=fake_create_draft
        model=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/model-drafts',
            json={'name':'设备温度预测','confirm_scope':True})
        assert model.status_code==201,model.text
        assert model.json()['draft']['draft_id']=='draft-stub'
    finally:c.__exit__(None,None,None)


def test_v0231_join_cardinality_and_cross_project_rejected(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        w=_workspace(c,pid,aid);wid=w['workspace_id']
        other=_make_project(c,'独立车间')
        # The same fake native id is never linked to the other project.
        input={'right_asset_id':str(uuid4()),'right_version_id':'base','left_key':'A2','right_key':'A2','how':'left'}
        denied=_apply(c,pid,wid,1,[{'tool':'bi.join.apply_virtual','arguments':input}],confirm=True)
        assert denied.status_code in {404,422},denied.text
        # Risk checks are mandatory even with user approval.
        service.engine.join_preview=lambda _pid,_state,_args: (_ for _ in ()).throw(
            NativeBIError('关联估算膨胀至百万行，拒绝'))
        rejected=_apply(c,pid,wid,1,[{'tool':'bi.join.apply_virtual','arguments':input}],confirm=True)
        assert rejected.status_code==422,rejected.text
        assert service.workspace(pid,wid)['revision']==1
    finally:c.__exit__(None,None,None)


def test_v0231_audited_turn_transcript_and_dashboard_panels(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        opened=_workspace(c,pid,aid);wid=opened['workspace_id'];base=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}'
        message='筛选 A2 > 0，按 Time 排序，绘制 A1、A5 折线图'
        result=c.post(base+'/turns',json={'message':message,'expected_revision':1,
            'request_id':str(uuid4())})
        assert result.status_code==200,result.text
        history=c.get(base+'/turns').json()
        assert history[-1]['message']==message and history[-1]['status']=='applied'
        assert history[-1]['revision']==2
        assert all('rows' not in h for h in history)
        second=_apply(c,pid,wid,2,[{'tool':'bi.chart.add_panel',
            'arguments':{'type':'scatter','x':'A1','y':['A5']}}])
        assert second.status_code==200,second.text
        assert len(second.json()['workspace']['state']['dashboard'])==2
        saved=c.post(base+'/views',json={'name':'双面板', 'perspective':{'panels':[{'plugin':'Y Line'},{'plugin':'X/Y Scatter'}]}})
        assert saved.status_code==201
        again=c.get(base+'/turns').json()
        assert len(again)==1
    finally:c.__exit__(None,None,None)


def test_v0231_computed_field_and_source_column_contract_is_strict(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        opened=_workspace(c,pid,aid);wid=opened['workspace_id'];rev=1
        natural=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/turns',json={
            'message':'计算 A5 减 A1 的差值', 'execute':True,'expected_revision':rev,'request_id':str(uuid4())})
        assert natural.status_code==200,natural.text
        assert natural.json()['workspace']['state']['expressions'][0]['name']=='computed_difference'
        bad=_apply(c,pid,wid,2,[{'tool':'bi.expression.create',
           'arguments':{'name':'evil','expression':{'kind':'binary','op':'/','left':{'kind':'column','value':'Unknown'},
                'right':{'kind':'number','value':0}}}}])
        assert bad.status_code==422,bad.text
        cancel=_apply(c,pid,wid,2,[{'tool':'bi.expression.clear','arguments':{}}])
        assert cancel.status_code==200,cancel.text
        assert cancel.json()['workspace']['state']['expressions']==[]
        bad_chart=_apply(c,pid,wid,3,[{'tool':'bi.chart.create',
            'arguments':{'type':'histogram','x':'Time','y':['A1']}}])
        assert bad_chart.status_code==422,bad_chart.text
    finally:c.__exit__(None,None,None)


def test_v0231_rolling_and_bucket_explicit_timestamps(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        wid=_workspace(c,pid,aid)['workspace_id']
        window=_apply(c,pid,wid,1,[{'tool':'bi.window.compute',
            'arguments':{'column':'A1','time_field':'Time','window':5,'name':'moving_A1'}}])
        assert window.status_code==200,window.text
        assert window.json()['workspace']['state']['window']['window']==5
        clear=_apply(c,pid,wid,2,[{'tool':'bi.window.clear','arguments':{}}])
        assert clear.status_code==200,clear.text
        invalid=_apply(c,pid,wid,3,[{'tool':'bi.timeseries.bucket',
            'arguments':{'time_field':'Time','metric_field':'A1','interval':'1h'}}])
        assert invalid.status_code==422
        assert '日期' in invalid.text or '时间' in invalid.text
    finally:c.__exit__(None,None,None)


from test_enterprise_security_v021 import enterprise, login, write

def test_v0231_enterprise_bi_agent_role_gates(enterprise):
    app,manager,tenant_a,tenant_b=enterprise
    with TestClient(app,base_url='https://testserver') as admin:
        csrf=login(admin,'factory-a','admin-a','A!long-random-passphrase-2026')
        created=write(admin,'/api/v1/projects',{'name':'仪表盘管理'},csrf)
        assert created.status_code==201,created.text
        pid=created.json()['id']
        user=write(admin,'/api/v1/identity/users',{'username':'analyst1',
             'password':'Analyst-Strong-Password-2026'},csrf)
        assert user.status_code==201,user.text
        uid=user.json()['user_id']
        grant=f'/api/v1/identity/projects/{pid}/members'
        assert write(admin,grant,{'user_id':uid,'role':'viewer'},csrf).status_code==201
        with TestClient(app,base_url='https://testserver') as actor:
            actor_csrf=login(actor,'factory-a','analyst1','Analyst-Strong-Password-2026')
            assert actor.get(f'/api/v1/projects/{pid}/bi-agent/tools').status_code==200
            path=f'/api/v1/projects/{pid}/bi-agent/workspaces'
            assert write(actor,path,{'asset_id':str(uuid4())},actor_csrf).status_code==403
            assert write(admin,grant,{'user_id':uid,'role':'analyst'},csrf).status_code in {200,201}
            allowed=write(actor,path,{'asset_id':str(uuid4())},actor_csrf)
            assert allowed.status_code==404,allowed.text  # authorization passed; asset absent
            protected=write(actor,path+'/'+str(uuid4())+'/model-drafts',
                {'name':'模型草稿','confirm_scope':True},actor_csrf)
            assert protected.status_code==403,protected.text
            protected=write(actor,path+'/'+str(uuid4())+'/freeze',{'confirmed':True},actor_csrf)
            assert protected.status_code==403,protected.text
            assert write(actor,path,{'asset_id':str(uuid4())},'wrong').status_code==403
        with TestClient(app,base_url='https://testserver') as outside:
            login(outside,'factory-b','admin-b','B!long-random-passphrase-2026')
            assert outside.get(f'/api/v1/projects/{pid}/bi-agent/tools').status_code==404


def test_v0231_generic_project_tables_appear_in_join_catalog(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        iid,catalog=_upload(c,pid,('A2,Tag\n1,NG\n5,OK\n').encode(),'quality.csv')
        ids=[r['region_id'] for r in catalog['files'][0]['worksheets'][0]['regions']]
        r=c.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
            json={'region_ids':ids,'confirm':True})
        assert r.status_code==202,r.text
        assert app.state.project_publication_jobs.work_once()['status']=='succeeded'
        found=c.get(f'/api/v1/projects/{pid}/native-assets')
        assert found.status_code==200,found.text
        source_ids={x['native_asset_id'] for x in found.json()}
        assert aid in source_ids and len(source_ids)>=2
        assert all(x['kind']=='generic_table' for x in found.json())
        wid=_workspace(c,pid,aid)['workspace_id']
        others=[x for x in found.json() if x['native_asset_id']!=aid]
        name=others[0]['asset']['name']
        plan=service.plan_turn(pid,wid,'按 A2 关联 '+name)
        assert plan['actions'] and plan['actions'][0]['tool']=='bi.join.preview',plan
        assert plan['actions'][0]['arguments']['right_asset_id']==others[0]['native_asset_id']
        other=_make_project(c,'没有这些数据的项目')
        assert c.get(f'/api/v1/projects/{other}/native-assets').json()==[]
    finally:c.__exit__(None,None,None)


def test_v0231_shared_view_can_query_without_mutating_owner_workspace(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        doc=_workspace(c,pid,aid)
        wid=doc['workspace_id']
        saved=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/views',
           json={'name':'共享审阅视图','shared':True})
        assert saved.status_code==201,saved.text
        vid=saved.json()['view_id']
        result=c.get(f'/api/v1/projects/{pid}/bi-agent/views/{vid}/query')
        assert result.status_code==200 and result.json()['returned']==1,result.text
        assert service.workspace(pid,wid)['revision']==1
    finally:c.__exit__(None,None,None)
