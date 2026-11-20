"""v0.33.3 reliability: bounded LLM, durable continuation and source-pinned receipts.

The artifact fixture is deliberately a byte-level SHA mock, NOT real Parquet.
Actual DuckDB/Perspective rendering must be checked on the user's machine.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from test_conversational_bi_v0231 import _env
from test_featurelab321_agent_orchestration import ResponsiveModel
from test_featurelab322_same_artifact import make_review, inject_mock_artifact
from test_featurelab32_auto_workflow import simulate_profile, set_job
from timepilot.feature_auto_agent import SAFE_TOOLS
from timepilot.feature_auto_analysis import FeatureAutoAnalysis
from timepilot.feature_auto_coordinator import FeatureAutoCoordinator
from timepilot.feature_contracts import canonical
from timepilot.feature_evaluation import FeatureEvaluator
from timepilot.projects import now


class ToggleModel(ResponsiveModel):
    def __init__(self, mode):
        super().__init__()
        self.switch = mode
        self.mode = {'enabled':'valid', 'api_failure':'timeout', 'unauthorized':'publish',
                     'disabled':'valid'}[mode]

    def config(self):
        return {'enabled':self.switch != 'disabled', 'has_api_key':True,
                'share_schema_only':True}


def _service(app, model=None):
    return FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
        app.state.feature_semantic,app.state.feature_registry,
        app.state.feature_jobs,model)


def _complete_previews(app, svc, pid, session):
    doc=svc.candidates.get(pid,session['candidate_session_id'])
    qualities=[{'feature_name':c['output_name'],'rows_scanned':3,
        'finite_count':3,'null_rate':0.0,'constant_on_full_data':False}
        for c in doc['candidates']]
    for jid in session['preview_job_ids']:
        set_job(app,jid,{'quality':qualities,'source_sha256':session['source_sha256']})


@pytest.mark.parametrize('mode', ['enabled','disabled','api_failure','unauthorized'])
def test_four_llm_modes_preserve_safe_pipeline_and_report(tmp_path,mode):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        model=ToggleModel(mode)
        svc=_service(app,model)
        created=svc.create(pid,asset,'base',goal='设备质量异常波动',task_hint='auto',
                    request_id='333-model-'+mode,budget=6)
        sid=created['session_id']
        con=app.state.feature_semantic.latest(pid,asset,'base').model_dump()
        simulate_profile(app,pid,asset,con,created['profile_job_id'])
        coordinator=FeatureAutoCoordinator(svc)
        assert coordinator.work_once() is True
        preview=svc.get(pid,sid)
        assert preview['stage']=='preview'
        _complete_previews(app,svc,pid,preview)
        # An independent coordinator instance can continue a durable session.
        assert FeatureAutoCoordinator(_service(app,model)).work_once() is True
        ready=svc.get(pid,sid)
        assert ready['status']=='ready' and ready['stage']=='review'
        assert ready['selection'] and ready['report']['evaluated_count'] > 0
        assert ready['report']['predictive_value_evaluated'] is False
        assert all(rec['step'] in SAFE_TOOLS for rec in ready['agent']['trace'])
        assert not ready['agent'].get('human_actions')
        if mode=='enabled':
            assert ready['agent']['mode']=='llm' and model.calls
        elif mode=='disabled':
            assert ready['agent']['mode']=='rules' and not model.calls
        elif mode=='api_failure':
            assert ready['agent']['mode']=='rules'
            assert ready['explanation']['provider']=='rules'
            assert model.calls
        else:
            assert ready['agent']['candidate_adjustment']['provider']=='rules'
            assert ready['agent']['rejected_proposals']>0
            assert 'feature.publish' not in str(ready['agent']['trace'])
        assert all('raw_rows' not in str(payload) for _,payload in model.calls)
        report=svc.report_markdown(pid,sid)
        assert report['source_sha256']==ready['source_sha256']
        assert report['report_sha256']==hashlib.sha256(report['markdown'].encode()).hexdigest()
        assert svc.get(pid,sid)['revision']==ready['revision']  # GET is read-only
        assert not coordinator.work_once()  # finished sessions are not repeated
    finally:
        client.__exit__(None,None,None)


def test_shutdown_resume_without_browser_and_read_only_get(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        svc=_service(app)
        s=svc.create(pid,asset,'base',goal='查看压力变化',task_hint='quality',
                     request_id='333-restart-'+uuid4().hex,budget=6)
        sid=s['session_id'];base=f'/api/v1/projects/{pid}/feature-lab'
        # A refresh cannot implicitly trigger the next stage.
        for _ in range(3):
            assert client.get(base+f'/analysis/sessions/{sid}').json()['revision']==s['revision']
        # Simulate an interrupted process: recreate all coordinators and services.
        simulate_profile(app,pid,asset,
             app.state.feature_semantic.latest(pid,asset,'base').model_dump(),s['profile_job_id'])
        assert FeatureAutoCoordinator(_service(app)).work_once()
        waiting=_service(app).get(pid,sid)
        assert waiting['status']=='previewing'
        assert not FeatureAutoCoordinator(_service(app)).work_once()
        _complete_previews(app,_service(app),pid,waiting)
        assert FeatureAutoCoordinator(_service(app)).work_once()
        ready=_service(app).get(pid,sid)
        assert ready['stage']=='review' and ready['status']=='ready'
        assert client.get(base+f'/analysis/sessions/{sid}').json()['revision']==ready['revision']
        assert app.state.feature_auto_coordinator.__class__ is FeatureAutoCoordinator
    finally:
        client.__exit__(None,None,None)


def test_retry_failed_job_keeps_approved_scope_and_source(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        svc=_service(app)
        original=svc.create(pid,asset,'base',goal='检测异常',task_hint='anomaly',
                 request_id='333-retry-'+uuid4().hex,budget=6)
        sid=original['session_id'];jid=original['profile_job_id']
        with app.state.projects._lock,app.state.projects._db() as db:
            db.execute("UPDATE feature_jobs SET status='failed_retryable',attempts=1,"
                       "error_code='dependency_unavailable',error_text='test outage' WHERE job_id=?",(jid,))
        assert FeatureAutoCoordinator(svc).work_once()
        failed=svc.get(pid,sid)
        assert failed['status']=='failed' and failed['stage']=='profile'
        stale_retry=client.post(base+f'/analysis/sessions/{sid}/retry',json={
            'expected_revision':failed['revision']-1})
        assert stale_retry.status_code in (409,422)
        assert client.get(base+f'/analysis/sessions/{sid}/retry').status_code in (404,405)
        good=client.post(base+f'/analysis/sessions/{sid}/retry',json={
            'expected_revision':failed['revision']})
        assert good.status_code==200,good.text
        restarted=good.json()
        assert restarted['status']=='profiling'
        assert app.state.feature_jobs.get_job(pid,jid)['status']=='queued'
        assert restarted['source_sha256']==original['source_sha256']
        simulate_profile(app,pid,asset,app.state.feature_semantic.latest(pid,asset,'base').model_dump(),jid)
        assert FeatureAutoCoordinator(_service(app)).work_once()
        assert svc.get(pid,sid)['status']=='previewing'
        # A stale revision cannot initiate a second retry.
        assert client.post(base+f'/analysis/sessions/{sid}/retry',json={
            'expected_revision':failed['revision']}).status_code in (409,422)
    finally:
        client.__exit__(None,None,None)


def test_source_contract_change_invalidates_session_and_historical_report(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        svc=_service(app)
        state=svc.create(pid,asset,'base',goal='check quality',task_hint='quality',
                  request_id='333-stale-'+uuid4().hex,budget=6)
        sid=state['session_id']
        contract=app.state.feature_semantic.latest(pid,asset,'base')
        field=contract.fields[0]
        patched=app.state.feature_semantic.patch(pid,asset,'base',{
            'fields':[{'field_id':field.field_id,'display_name':'Updated field name'}]},
            contract.revision)
        assert patched.revision>contract.revision
        old=svc.get(pid,sid)
        assert old['stale'] and old['source_sha256']==state['source_sha256']
        assert not FeatureAutoCoordinator(svc).work_once()
        assert svc.delivery(pid,sid)['status']=='stale'
        with pytest.raises(ValueError,match='stale|变化|changed'):
            svc.advance(pid,sid)
        with pytest.raises(ValueError,match='变化'):
            svc.retry_failed(pid,sid,old['revision'])
        assert client.get(f'/api/v1/projects/{pid}/feature-lab/analysis/sessions/{sid}').json()['stale']
    finally:
        client.__exit__(None,None,None)


def test_report_bi_and_materialized_sha_consistency_and_tamper(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base,sid,saved=make_review(app,client,pid,asset)
        state=client.post(base+f'/analysis/sessions/{sid}/materialize',
                          json={'request_id':'333-mat-'+uuid4().hex}).json()
        artifact=inject_mock_artifact(app,pid,state['materialize_job_id'],saved['feature_set_id'])
        assert FeatureAutoCoordinator(_service(app)).work_once()
        evaluating=_service(app).get(pid,sid)
        assert evaluating['status']=='evaluating'
        proof=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert proof['artifact']['sha256']==artifact['sha256']
        assert proof['bi']['artifact_sha256']==artifact['sha256']
        assert artifact['artifact_id'] in proof['bi']['ws_path']
        assert f'project_id={pid}' in proof['bi']['ws_path']
        report_before=client.get(base+f'/analysis/sessions/{sid}/report').json()
        assert report_before['report_sha256']==hashlib.sha256(report_before['markdown'].encode()).hexdigest()
        assert artifact['sha256'] in report_before['markdown']
        assert report_before['delivery']['artifact']['sha256']==artifact['sha256']
        assert not report_before['delivery']['same_artifact_evaluation']
        ejob=app.state.feature_jobs.get_job(pid,evaluating['evaluation_job_id'])
        eval_obj=FeatureEvaluator(app.state.projects,app.state.native_bi,
           app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
        evaluated=eval_obj.run_plan(pid,ejob['payload']['plan_id'],'333-evaluation-id')
        set_job(app,evaluating['evaluation_job_id'],evaluated)
        assert FeatureAutoCoordinator(_service(app)).work_once()
        done=_service(app).get(pid,sid)
        assert done['status']=='ready' and done['stage']=='evaluated'
        # Viewer can only record a client claim; it does not prove screenshot rendering.
        wrong=client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
          'expected_revision':done['revision'],'artifact_id':artifact['artifact_id'],
          'artifact_sha256':'0'*64,'viewer_version':'5.6.1','panel_count':2,
          'workspace':{'panels':{'original':{},'feature':{}}}})
        assert wrong.status_code in (409,422)
        good=client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
          'expected_revision':done['revision'],'artifact_id':artifact['artifact_id'],
          'artifact_sha256':artifact['sha256'],'viewer_version':'5.6.1','panel_count':2,
          'workspace':{'panels':{'original':{},'feature':{}}}})
        assert good.status_code==200,good.text
        proof=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert proof['same_artifact_evaluation'] and proof['same_artifact_bi_receipt']
        assert proof['status']=='app_evidence_linked' and not proof['production_verified']
        assert proof['evaluation']['artifact_sha256']==artifact['sha256']
        assert proof['bi']['artifact_sha256']==artifact['sha256']
        report=client.get(base+f'/analysis/sessions/{sid}/report').json()
        assert report['report_sha256']==hashlib.sha256(report['markdown'].encode('utf-8')).hexdigest()
        assert report['delivery_status']=='app_evidence_linked'
        assert report['artifact_sha256']==artifact['sha256']
        assert report['evaluation_id']==proof['evaluation']['evaluation_id']
        # Corruption must immediately invalidate report provenance and new BI claims.
        path=app.state.projects.home/'feature_lab'/pid/'artifacts'/artifact['artifact_id']/'data.parquet'
        path.write_bytes(b'fixture bytes modified after SHA256 validation')
        broken=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert broken['status']=='blocked' and not broken['same_artifact_evaluation']
        stale_report=client.get(base+f'/analysis/sessions/{sid}/report').json()
        assert stale_report['delivery_status']=='blocked'
        assert stale_report['report_sha256']==hashlib.sha256(stale_report['markdown'].encode()).hexdigest()
        assert client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
          'expected_revision':good.json()['revision'],'artifact_id':artifact['artifact_id'],
          'artifact_sha256':artifact['sha256'],'viewer_version':'5.6.1','panel_count':2,
          'workspace':{'panels':{'original':{},'feature':{}}}}).status_code in (409,422)
    finally:
        client.__exit__(None,None,None)


def test_changed_source_fingerprint_blocks_resume_report_and_bi(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base,sid,_=make_review(app,client,pid,asset)
        previous=_service(app).get(pid,sid)
        source=app.state.native_bi.asset(asset)
        source['sha256']='d'*64  # immutable source version was unexpectedly replaced
        updated=_service(app).get(pid,sid)
        assert updated['stale'] is True
        assert updated['source_sha256']==previous['source_sha256']
        assert not FeatureAutoCoordinator(_service(app)).work_once()
        report=client.get(base+f'/analysis/sessions/{sid}/report').json()
        assert '历史来源已变化' in report['markdown']
        assert report['delivery_status']=='stale'
        assert report['report_sha256']==hashlib.sha256(report['markdown'].encode()).hexdigest()
        assert client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
            'expected_revision':previous['revision'],'artifact_id':'fake',
            'artifact_sha256':'0'*64,'viewer_version':'5.6.1','panel_count':2,
            'workspace':{'panels':{'original':{},'feature':{}}}}).status_code in (409,422)
    finally:
        client.__exit__(None,None,None)


def test_enterprise_coordinator_revalidates_user_and_permission(tmp_path):
    import sqlite3
    from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
    app,client,pid,asset,_=_env(tmp_path)
    try:
        owner='user-333';database=tmp_path/'users-test.db'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE users(user_id TEXT,tenant_id TEXT,username TEXT,tenant_role TEXT,active INTEGER)')
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(owner,'tenant-a','agent-user','member',1))
        class Security:
            approved=True
            def _db(self):
                db=sqlite3.connect(database)
                db.row_factory=sqlite3.Row
                return db
            def allowed_project(self,principal,project_id,*,required='viewer'):
                assert required=='editor' and principal.user_id==owner
                assert project_id==pid
                if not self.approved:raise SecurityError('permission revoked')
        security=Security()
        app.state.projects.security=security
        coordinator=FeatureAutoCoordinator(_service(app))
        assert CURRENT_PRINCIPAL.get() is None
        with coordinator._owner_context(pid,owner):
            assert CURRENT_PRINCIPAL.get().user_id==owner
            assert CURRENT_PRINCIPAL.get().tenant_id=='tenant-a'
        assert CURRENT_PRINCIPAL.get() is None
        security.approved=False
        with pytest.raises(SecurityError):
            with coordinator._owner_context(pid,owner):pass
        security.approved=True
        with sqlite3.connect(database) as db:
            db.execute('UPDATE users SET active=0 WHERE user_id=?',(owner,))
        with pytest.raises(SecurityError):
            with coordinator._owner_context(pid,owner):pass
        assert CURRENT_PRINCIPAL.get() is None
    finally:
        app.state.projects.security=None
        client.__exit__(None,None,None)


def test_failed_evaluation_requeues_only_failed_job_and_preserves_artifact(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base,sid,saved=make_review(app,client,pid,asset)
        state=client.post(base+f'/analysis/sessions/{sid}/materialize',
                          json={'request_id':'333-eval-retry-'+uuid4().hex}).json()
        artifact=inject_mock_artifact(app,pid,state['materialize_job_id'],saved['feature_set_id'])
        assert FeatureAutoCoordinator(_service(app)).work_once()
        evaluating=_service(app).get(pid,sid)
        assert evaluating['stage']=='evaluation' and evaluating['status']=='evaluating'
        with app.state.projects._lock,app.state.projects._db() as db:
            db.execute("UPDATE feature_jobs SET status='failed_retryable',error_text=? WHERE job_id=?",
                       ('simulated model runtime failure',evaluating['evaluation_job_id']))
        assert FeatureAutoCoordinator(_service(app)).work_once()
        paused=_service(app).get(pid,sid)
        assert paused['status']=='ready' and paused['stage']=='materialized'
        assert 'simulated model runtime failure' in paused['evaluation_error']
        assert paused['artifact']['sha256']==artifact['sha256']
        retry=client.post(base+f'/analysis/sessions/{sid}/retry',json={
            'expected_revision':paused['revision']})
        assert retry.status_code==200,retry.text
        resumed=retry.json()
        assert resumed['status']=='evaluating' and resumed['stage']=='materialized'
        assert resumed['artifact']['sha256']==artifact['sha256']
        assert app.state.feature_jobs.get_job(pid,evaluating['evaluation_job_id'])['status']=='queued'
        assert client.post(base+f'/analysis/sessions/{sid}/retry',json={
            'expected_revision':paused['revision']}).status_code in (409,422)
    finally:
        client.__exit__(None,None,None)
