"""v0.23.5 plan endpoints, persisted matrices, revision and renderer ack.

The plan service and audit trail are real. Only the Pearson engine is replaced
in the two labelled simulated tests; independent DuckDB tests live separately.
"""
from __future__ import annotations
import json
from uuid import uuid4
import pytest
from test_conversational_bi_v0231 import _env,_workspace,_apply
from test_universal_import_v0221 import _make_project
from timepilot.bi_feasibility import BIFeasibility
from timepilot.bi_statistics import BICorrelationService


def _request(c,pid,wid,revision=1,message='绘制 A1 A2 相关性热力图',request_id=None):
    return c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/analysis-plans',json={
      'message':message,'expected_revision':revision,'request_id':request_id or str(uuid4())})


def test_planning_requires_engine_and_reports_real_capability(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        ws=_workspace(c,pid,aid)
        r=_request(c,pid,ws['workspace_id'],message='请结合所有的序列绘制相关性热力图')
        assert r.status_code==201,r.text
        plan=r.json();assert plan['plan']['intent']['intent_candidates']==['correlation']
        assert plan['plan']['intent']['auto_fields']==['A1','A2','A5']
        if not __import__('importlib').util.find_spec('duckdb'):
            assert plan['status']=='blocked' and 'DuckDB' in plan['plan']['feasibility']['reasons'][0]
        another=_make_project(c,'禁止跨项目')
        assert c.get(f'/api/v1/projects/{another}/bi-agent/workspaces/{ws["workspace_id"]}/analysis-plans/{plan["plan_id"]}').status_code==404
        assert c.get(f'/api/v1/projects/{another}/bi-agent/analyses/{uuid4()}/matrix').status_code==404
    finally:c.__exit__(None,None,None)


def _mock_engine(monkeypatch):
    check_original=BIFeasibility.check
    monkeypatch.setattr(BIFeasibility,'check',lambda self,*args,**kw:check_original(self,*args,**{**kw,'duckdb_ready':True}))
    def fake_compute(self,pid,ws,*,field_ids,method='pearson',null_policy='pairwise'):
        # Synthetic output for API/state tests only. No accuracy claim is made.
        return {'dimension':len(field_ids),'field_ids':field_ids,'method':method,'null_policy':null_policy,
                'matrix':[{'field_x':x,'field_y':y,'coefficient':1. if x==y else 0.5,
                    'n_pair':3,'status':'small_sample'} for x in field_ids for y in field_ids],
                'selection_fingerprint':BICorrelationService.selection_fingerprint(ws['state'],ws['source_sha256']),
                'source_sha256':ws['source_sha256'],'scan_scope':'simulated'}
    monkeypatch.setattr(BICorrelationService,'compute',fake_compute)


def test_plan_full_backend_lifecycle_simulated_statistical_engine(tmp_path,monkeypatch):
    app,c,pid,aid,service=_env(tmp_path)
    _mock_engine(monkeypatch)
    try:
        ws=_workspace(c,pid,aid);wid=ws['workspace_id']
        request_id=str(uuid4())
        r=_request(c,pid,wid,request_id=request_id)
        assert r.status_code==201,r.text
        plan=r.json();plan_id=plan['plan_id'];url=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/analysis-plans/{plan_id}'
        assert plan['status']=='awaiting_confirmation' # Time index is a suggested exclusion
        assert plan['plan']['feasibility']['defaults']['field_ids']==['A1','A2']
        assert _request(c,pid,wid,request_id=request_id).json()['plan_id']==plan_id
        no_confirm=c.post(url+'/apply',json={'plan_revision':1,'request_id':str(uuid4()),'confirm':False})
        assert no_confirm.status_code==422,no_confirm.text
        update=c.patch(url,json={'plan_revision':1,'field_ids':['A2','A5']})
        assert update.status_code==200,update.text
        changed=update.json();assert changed['plan_revision']==2
        stale_update=c.patch(url,json={'plan_revision':1,'field_ids':['A1','A2']})
        assert stale_update.status_code==409,stale_update.text
        execute_id=str(uuid4());body={'request_id':execute_id,'plan_revision':2,'confirm':True}
        run=c.post(url+'/apply',json=body)
        assert run.status_code==200,run.text
        execution=run.json();assert execution['status']=='rendering',execution
        assert execution['result']['matrix_shape']==[2,2]
        assert c.post(url+'/apply',json=body).json()['execution_id']==execution['execution_id']
        assert c.get(url).json()['status']=='rendering'
        matrix=c.get(f'/api/v1/projects/{pid}/bi-agent/analyses/{execution["result"]["analysis_id"]}/matrix')
        assert matrix.status_code==200,matrix.text
        assert len(matrix.json()['matrix'])==4
        assert service.workspace(pid,wid)['state']['correlation']['analysis_id']==execution['result']['analysis_id']
        assert service.workspace(pid,wid)['revision']==2
        with service.projects._db() as db:
            assert db.execute('SELECT count(*) FROM bi_analysis_matrices').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM bi_agent_events WHERE workspace_id=?',(wid,)).fetchone()[0]==1
        ack=c.post(url+'/execution/'+execution['execution_id']+'/rendered',json={'success':True})
        assert ack.status_code==200,ack.text
        assert ack.json()['status']=='succeeded'
        assert c.get(url).json()['status']=='succeeded'
        saved=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/views',json={
            'name':'持久化相关矩阵','perspective':{'kind':'correlation-heatmap','analysis_id':execution['result']['analysis_id']}})
        assert saved.status_code==201,saved.text
        undo=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/undo',json={
            'expected_revision':2,'request_id':str(uuid4())})
        assert undo.status_code==200,undo.text
        assert 'correlation' not in undo.json()['workspace']['state']
        redo=c.post(f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/redo',json={
            'expected_revision':3,'request_id':str(uuid4())})
        assert redo.status_code==200,redo.text
        assert redo.json()['workspace']['state']['correlation']['analysis_id']==execution['result']['analysis_id']
    finally:c.__exit__(None,None,None)


def test_plan_stale_and_untrusted_field_rejected(tmp_path,monkeypatch):
    app,c,pid,aid,service=_env(tmp_path);_mock_engine(monkeypatch)
    try:
        ws=_workspace(c,pid,aid);wid=ws['workspace_id']
        created=_request(c,pid,wid,message='相关性热力图')
        assert created.status_code==201,created.text
        pid_plan=created.json()['plan_id'];url=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/analysis-plans/{pid_plan}'
        bad=c.patch(url,json={'plan_revision':1,'field_ids':['A1','A2; DROP TABLE projects']})
        assert bad.status_code in (409,422),bad.text
        action=_apply(c,pid,wid,1,[{'tool':'bi.filter.set','arguments':{'column':'A1','op':'gt','value':0}}])
        assert action.status_code==200,action.text
        assert c.get(url).json()['status']=='stale'
        stale=c.post(url+'/apply',json={'request_id':str(uuid4()),'plan_revision':1,'confirm':True})
        assert stale.status_code in (409,422),stale.text
        with service.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM bi_analysis_executions').fetchone()[0]==0
    finally:c.__exit__(None,None,None)
