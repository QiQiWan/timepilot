"""v0.10.0 unified execution tests: actual API, durable ledger, budgets and write guards."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from timepilot.agent_tools import default_registry, ToolRegistry, Tool, Strict, SKILL_TOOLS
from timepilot.agent_executor import DurableExecutor, JobRequest
from timepilot.api import create_app
from timepilot.data_assets import asset_id_for
from timepilot.smart_workbench import SaveView, ViewSelection


def make_import(app):
    rows = ['Time,A2,A14,available_at,source_name,统计口径']
    for month in range(1, 6):
        rows.append(f'2024-{month:02d}-01,M1,{month*10},2024-{month:02d}-01,Alpha,销量')
    content = '\n'.join(rows).encode()
    manifest = app.state.smart_import.ingest(io.BytesIO(content), 'monthly.csv')
    imp=app.state.smart_import
    from timepilot.smart_import import TransformRecipe
    version=imp.transform(manifest['import_id'], TransformRecipe(
        candidate_ids=manifest['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14','available_at':'available_at','source_name':'source_name',
                       'statistical_scope':'统计口径'}))
    return manifest, version


def drain(executor, job_id, n=20):
    for _ in range(n):
        status=executor.status(job_id)
        if status['status'] not in {'queued','running'}:return status
        executor.run_once()
    pytest.fail('Agent was not completed or paused within step budget')


def test_tools_registry_schema_and_skill_contracts():
    reg=default_registry()
    all_tools={x['name']:x for x in reg.public()}
    assert set().union(*map(set,SKILL_TOOLS.values()))==set(all_tools)
    assert len(all_tools)>=14
    assert all_tools['forecast.draft']['permission']=='write_confirm'
    with pytest.raises(ValueError,match='未授权'):
        reg.execute('shell.exec',{},None)
    with pytest.raises(Exception):
        reg.execute('file.inspect',{'import_id':'x'*36,'shell':'rm'},None)
    with pytest.raises(Exception):
        reg.execute('features.export',{'asset_id':'anything','version_id':'x'*36,'lags':[-1]},None)


def test_http_task_understanding_persists_across_process_instances(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=make_import(app)
    with TestClient(app) as client:
        cap=client.get('/api/v1/agent/skills')
        assert cap.status_code==200
        assert cap.json()['write_tools_require_confirmation']
        body={'goal':'understand','import_id':manifest['import_id'], 'max_steps':10,
              'idempotency_key':'sample-task-one'}
        created=client.post('/api/v1/agent/jobs',json=body)
        assert created.status_code==201,created.text
        job_id=created.json()['job_id']
        assert client.post('/api/v1/agent/jobs',json=body).json()['job_id']==job_id
        assert client.post('/api/v1/agent/jobs',json={**body,'max_steps':4}).status_code==422
        status=drain(app.state.agent_executor,job_id)
        assert status['status']=='succeeded',status
        assert 'semantics.map' in [x['name'] for x in status['steps']]
        assert status['result']['auto_train'] is False
        assert status['steps'][0]['result_sha256']
        new_service=DurableExecutor(tmp_path/'agent_workflows',default_registry(),
                app.state.agent_executor.services)
        assert new_service.status(job_id)['status']=='succeeded'
        assert client.get('/api/v1/agent/jobs/'+job_id).status_code==200
        assert client.get('/agent').status_code==200
        assert client.get('/assets/agent-ui.js').status_code==200


def test_read_only_dynamic_analysis_and_safe_feature_export(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,version=make_import(app)
    eid=asset_id_for(manifest['import_id']);vid=version['version_id']
    executor=app.state.agent_executor
    job=executor.create({'goal':'analyze','asset_id':eid,'version_id':vid,'use_model':False})
    status=drain(executor,job['job_id'])
    assert status['status']=='succeeded',status
    assert [x['name'] for x in status['steps']]==['quality.asset','bi.asset_group']
    assert status['budget']['used_units'] == 6
    features=executor.create({'goal':'features','asset_id':eid,'version_id':vid,'lags':[1],
                               'export_features':True})
    paused=drain(executor,features['job_id'])
    assert paused['status']=='waiting_approval',paused
    pending=paused['pending_approval']
    assert pending['tool']=='features.export'
    assert not list((tmp_path/'agent_artifacts').rglob('*.csv'))
    with pytest.raises(ValueError,match='审批对象'):
        executor.approve(features['job_id'],pending_sha256='0'*64,confirmed=True)
    executor.approve(features['job_id'],pending_sha256=pending['sha256'],confirmed=True)
    completed=drain(executor,features['job_id'])
    assert completed['status']=='succeeded',completed
    artifact=[x for x in completed['steps'] if x['name']=='features.export'][0]['result']
    assert Path(artifact['artifact_path']).is_file()
    assert artifact['feature_complete_rows']>=1


def test_forecast_draft_requires_approval_and_never_trains(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    m,v=make_import(app)
    eid=asset_id_for(m['import_id']);vid=v['version_id']
    view=app.state.data_assets.save_view(eid,vid,SaveView(name='单来源销量',selection=ViewSelection()))
    executor=app.state.agent_executor
    job=executor.create({'goal':'forecast','asset_id':eid,'version_id':vid,'view_id':view['view_id']})
    paused=drain(executor,job['job_id'])
    assert paused['status']=='waiting_approval'
    assert not app.state.workbench.drafts(m['import_id'],vid)
    executor.approve(job['job_id'],pending_sha256=paused['pending_approval']['sha256'],confirmed=True)
    done=drain(executor,job['job_id'])
    assert done['status']=='succeeded'
    result=[x for x in done['steps'] if x['name']=='forecast.draft'][0]['result']
    assert result['status']=='needs_review'
    assert not app.state.client.store.list_runs()


def test_budget_cancel_retry_model_fallback_and_recovery(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    m,_=make_import(app);executor=app.state.agent_executor
    job=executor.create({'goal':'understand','import_id':m['import_id'], 'max_units':1})
    done=drain(executor,job['job_id'])
    assert done['status']=='failed' and '执行预算' in done['last_error']
    assert len(done['steps'])==1
    cancelled=executor.create({'goal':'understand','import_id':m['import_id']})
    executor.cancel(cancelled['job_id'])
    assert executor.status(cancelled['job_id'])['status']=='cancelled'
    assert executor.run_once() is None
    # Previous failed read can be retried but the budget is still enforced.
    executor.retry(job['job_id'])
    assert drain(executor,job['job_id'])['status']=='failed'
    # Lease expiry resumes read-only step without forgetting the previous output.
    recovered=executor.create({'goal':'understand','import_id':m['import_id']})
    with executor._db() as con:
        con.execute("UPDATE jobs SET status='running',lease_until=?,lease_owner='dead' WHERE id=?",
                    (time.time()-10,recovered['job_id']))
        con.execute("INSERT INTO steps(job_id,name,attempt,args_json,args_sha,status,units,created_at) "
                    "VALUES (?,?,?,?,?,'running',?,?)",
                    (recovered['job_id'],'file.inspect',1,'{}','hash',1,time.time()-200))
    assert drain(executor,recovered['job_id'])['status']=='succeeded'
    assert any(x['status']=='interrupted' for x in executor.status(recovered['job_id'])['steps'])
    # Uncertain interrupted writes require manual reconciliation, never auto retry.
    uncertain=executor.create({'goal':'understand','import_id':m['import_id']})
    with executor._db() as con:
        con.execute("UPDATE jobs SET status='running',lease_until=?,lease_owner='dead' WHERE id=?",
                    (time.time()-10,uncertain['job_id']))
        con.execute("INSERT INTO steps(job_id,name,attempt,args_json,args_sha,status,units,created_at) "
                    "VALUES (?,?,?,?,?,'running',?,?)",
                    (uncertain['job_id'],'series.build',1,'{}','hash',6,time.time()-200))
    executor.run_once()
    assert executor.status(uncertain['job_id'])['status']=='needs_reconciliation'
    with pytest.raises(ValueError,match='只能重试'):
        executor.retry(uncertain['job_id'])
    # No model is mandatory: timeout falls back to deterministic safe choice.
    def bad_model(*args): raise TimeoutError('provider unavailable')
    another=DurableExecutor(tmp_path/'agent_workflows',default_registry(),executor.services,
                            model_selector=bad_model)
    v=app.state.smart_import.manifest(m['import_id'])['versions'][-1]
    aid=asset_id_for(m['import_id'])
    j=another.create({'goal':'analyze','asset_id':aid,'version_id':v,'use_model':True})
    done=drain(another,j['job_id'])
    assert done['status']=='succeeded'
    assert done['model_status']=='fallback_model_error'
    assert done['budget']['model_calls']==1


def test_agent_tool_approval_cancel_api(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    m,v=make_import(app);eid=asset_id_for(m['import_id']);vid=v['version_id']
    with TestClient(app) as client:
        job=client.post('/api/v1/agent/jobs',json={'goal':'features', 'asset_id':eid,
                    'version_id':vid,'lags':[1],'export_features':True}).json()
        status=drain(app.state.agent_executor,job['job_id'])
        pending=status['pending_approval']
        assert pending
        assert client.post('/api/v1/agent/jobs/'+job['job_id']+'/approve',json={'confirmed':True,
                             'pending_sha256':'0'*64}).status_code==422
        approved=client.post('/api/v1/agent/jobs/'+job['job_id']+'/approve',json={'confirmed':True,
                             'pending_sha256':pending['sha256']})
        assert approved.status_code==200
        assert drain(app.state.agent_executor,job['job_id'])['status']=='succeeded'
        bad=client.post('/api/v1/agent/jobs',json={'goal':'understand'})
        assert bad.status_code==422
