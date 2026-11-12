"""v0.26.0: job lifecycle, artifact eligibility, and real-viewer receipt contract.

The receipt test uses a synthetic browser token and synthetic artifact metadata;
this does NOT replace a genuine Perspective + DuckDB end-to-end acceptance.
"""
from __future__ import annotations
import json
from uuid import uuid4
from pathlib import Path
import pytest
from test_conversational_bi_v0231 import _env
from timepilot.feature_agent import FeatureAgent
from timepilot.feature_tool_manifest import manifest
from timepilot.feature_contracts import PlanRequest
from timepilot.projects import now
from timepilot.storage import sha256_file


def _make(tmp_path, monkeypatch):
    app, client, pid, aid, _ = _env(tmp_path)
    agent = FeatureAgent(app.state.projects, app.state.native_bi,
                         app.state.feature_semantic, app.state.feature_registry,
                         app.state.feature_jobs)
    monkeypatch.setattr(agent,'tools',lambda *args: {'asset_id':aid,'tools':manifest(engine_ready=True)})
    return app,client,pid,aid,agent


def _created(app,pid,agent,plan):
    first = agent.execute(pid,plan['plan_id'])
    assert first['status']=='awaiting_job',first
    assert [s['status'] for s in first['steps']]==['succeeded','awaiting_job','pending']
    sid=first['steps'][0]['result']['set_id'];job_id=first['steps'][1]['result']['job_id']
    return first,sid,job_id


def _valid_artifact(app,pid,sid,job_id):
    reg=app.state.feature_registry
    rec=reg.get(pid,sid)
    path=app.state.projects.home/'feature_lab'/pid/'artifacts'/'test_receipt'/'data.parquet'
    path.parent.mkdir(parents=True,exist_ok=True)
    # Artificial bytes with stable checksum: gate metadata/identity validation
    # only. Numeric DuckDB/Parquet format requires separate configured test.
    path.write_bytes(b'PAR1' + b'illustrative-only' + b'PAR1')
    sha=sha256_file(path);artifact_id=str(uuid4())
    metadata={'artifact_id':artifact_id,'row_count':4,'sha256':sha,'scope':'full',
        'source_sha256':rec['source_sha256'],'spec_sha256':rec['spec_sha256']}
    with app.state.projects._lock,app.state.projects._db() as db:
        db.execute("UPDATE feature_jobs SET status='succeeded',result_json=? WHERE job_id=?",
                   ('{"state":"succeeded","artifact":"synthetic"}',job_id))
        db.execute("UPDATE feature_runs SET status='succeeded' WHERE run_id=?",(job_id,))
        db.execute("""INSERT INTO feature_artifacts
         (artifact_id,run_id,project_id,sha256,relpath,row_count,schema_json,source_sha256,created_at)
         VALUES (?,?,?,?,?,?,?,?,?)""",
         (artifact_id,job_id,pid,sha,str(path.relative_to(app.state.projects.home/'feature_lab')),
          4,json.dumps(metadata),rec['source_sha256'],now()))
    return artifact_id


def test_viewer_plan_never_succeeds_without_real_artifact(tmp_path,monkeypatch):
    app,client,pid,aid,agent=_make(tmp_path,monkeypatch)
    try:
        plan=agent.plan(pid,PlanRequest(message='对 A1 和 A2 做移动平均并在 BI 显示',
            asset_id=aid,version_id='base',request_id='need-actual-artifact'))
        assert [a['tool_id'] for a in plan['plan']['actions']]==[
            'feature.set.draft_create','feature.run.create','feature.viewer.open']
        state,sid,job_id=_created(app,pid,agent,plan)
        with app.state.projects._lock,app.state.projects._db() as db:
            db.execute("UPDATE feature_jobs SET status='succeeded' WHERE job_id=?",(job_id,))
        attempt=agent.execute(pid,plan['plan_id'])
        assert attempt['status']=='failed'
        assert '完成' in attempt['steps'][-1]['result']['error'] or '通过' in attempt['steps'][-1]['result']['error']
        assert not any(s['status']=='awaiting_viewer' for s in attempt['steps'])
    finally:client.__exit__(None,None,None)


def test_viewer_render_receipt_requires_issued_request_and_genuine_panel_shape(tmp_path,monkeypatch):
    app,client,pid,aid,agent=_make(tmp_path,monkeypatch)
    try:
        plan=agent.plan(pid,PlanRequest(message='对 A1 和 A2 做移动平均并在 BI 显示',
            asset_id=aid,version_id='base',request_id='viewer-ack'))
        _,sid,job_id=_created(app,pid,agent,plan)
        art=_valid_artifact(app,pid,sid,job_id)
        waiting=agent.execute(pid,plan['plan_id'])
        assert waiting['status']=='awaiting_viewer',waiting
        assert waiting['steps'][2]['status']=='awaiting_viewer'
        result=waiting['steps'][2]['result'];assert result['artifact_id']==art
        assert agent.execute(pid,plan['plan_id'])['status']=='awaiting_viewer'
        kwargs={'render_request_id':result['render_request_id'],'artifact_id':art,
            'viewer_version':'5.6.1','workspace':{'panels':{'source':{'plugin':'Datagrid'},
                   'feat':{'plugin':'X Bar'}}},'panel_count':2}
        with pytest.raises(ValueError,match='图表回执'):
            agent.confirm_render(pid,plan['plan_id'],**{**kwargs,'render_request_id':'unknown'})
        with pytest.raises(ValueError,match='双面板'):
            agent.confirm_render(pid,plan['plan_id'],**{**kwargs,'workspace':{'panels':{'source':{}}}})
        with pytest.raises(ValueError,match='5.6.1'):
            agent.confirm_render(pid,plan['plan_id'],**{**kwargs,'viewer_version':'0.0'})
        finished=agent.confirm_render(pid,plan['plan_id'],**kwargs)
        assert finished['status']=='succeeded',finished
        assert all(s['status']=='succeeded' for s in finished['steps'])
        with app.state.projects._db() as db:
            row=db.execute('SELECT status,step_id,artifact_id FROM feature_execution_receipts WHERE plan_id=?',
                (plan['plan_id'],)).fetchone()
        assert row['status']=='rendered' and row['artifact_id']==art and row['step_id']=='2'
        with pytest.raises(ValueError,match='等待'):
            agent.confirm_render(pid,plan['plan_id'],**kwargs)
    finally:client.__exit__(None,None,None)
