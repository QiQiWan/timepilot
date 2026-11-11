"""Additional v0.10.0 regression: multi-worker claims, safe budgets and model fallback."""
from __future__ import annotations

import io
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from timepilot.agent_executor import DurableExecutor
from timepilot.agent_tools import default_registry
from timepilot.api import create_app
from timepilot.data_assets import asset_id_for
from timepilot.data_understanding import DataUnderstandingService, UnderstandingRequest
from timepilot.smart_import import SmartImportService, TransformRecipe

ROOT=Path(__file__).resolve().parents[1]


def sample(app):
    rows=['Time,A2,A14,available_at,source_name,统计口径']
    for month in range(1, 7):
        rows.append(f'2024-{month:02d}-01,M1,{10*month},2024-{month:02d}-01,S1,销量')
    m=app.state.smart_import.ingest(io.BytesIO('\n'.join(rows).encode('utf-8')),'check.csv')
    v=app.state.smart_import.transform(m['import_id'],TransformRecipe(
        candidate_ids=m['suggested_candidate_ids'],field_mapping={'series_id':'A2',
            'target':'A14','available_at':'available_at','source_name':'source_name',
            'statistical_scope':'统计口径'}))
    return m,v,asset_id_for(m['import_id'])


def drain(e, identifier, n=14):
    for _ in range(n):
        current=e.status(identifier)
        if current['status'] not in {'queued','running'}: return current
        e.run_once()
    pytest.fail('Executor exceeded expected number of state transitions')


def test_budget_blocks_write_before_asking_for_confirmation(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    _,v,aid=sample(app)
    agent=app.state.agent_executor
    job=agent.create({'goal':'features','asset_id':aid,'version_id':v['version_id'],
                      'lags':[1],'export_features':True,'max_units':8})
    done=drain(agent,job['job_id'])
    assert done['status']=='failed' and done['pending_approval'] is None
    assert [step['name'] for step in done['steps']]==['quality.asset','features.preview']
    assert not list((tmp_path/'agent_artifacts').rglob('*.csv'))


def test_model_optional_and_only_allowlisted_tool_choice(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    _,v,aid=sample(app)
    model_suggestions=[]
    def choose(request, allowed, evidence):
        model_suggestions.append((allowed,evidence))
        return 'bi.asset_grid'
    a=DurableExecutor(tmp_path/'agent_workflows',default_registry(),
                      app.state.agent_executor.services,model_selector=choose)
    job=a.create({'goal':'analyze','asset_id':aid,'version_id':v['version_id'],
                  'use_model':True,'max_model_calls':1})
    result=drain(a,job['job_id'])
    assert result['status']=='succeeded'
    assert [step['name'] for step in result['steps']]==['quality.asset','bi.asset_grid']
    assert result['model_status']=='used'
    assert model_suggestions[0][0]==['bi.asset_group','bi.asset_grid']
    assert set(model_suggestions[0][1]) <= {'previous_tools','quality_flags'}
    b=DurableExecutor(tmp_path/'agent_workflows',default_registry(),
                      app.state.agent_executor.services,model_selector=None)
    skipped=b.create({'goal':'analyze','asset_id':aid,'version_id':v['version_id'],
                       'use_model':True})
    completed=drain(b,skipped['job_id'])
    assert completed['status']=='succeeded'
    assert completed['model_status']=='fallback_model_unconfigured'
    assert completed['budget']['model_calls']==0


def test_atomic_lease_prevents_duplicate_step_execution(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    m,_,_=sample(app)
    registry=default_registry()
    original=registry.get('file.inspect')
    started=[]
    def slow(s,v):
        started.append(time.monotonic())
        time.sleep(.15)
        return original.run(s,v)
    registry._tools['file.inspect']=replace(original,run=slow)
    a=DurableExecutor(tmp_path/'agent_workflows',registry,app.state.agent_executor.services)
    b=DurableExecutor(tmp_path/'agent_workflows',registry,app.state.agent_executor.services)
    created=a.create({'goal':'understand','import_id':m['import_id']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(a.run_once)
        time.sleep(.02)
        two=pool.submit(b.run_once)
        one.result(timeout=5)
        two.result(timeout=5)
    done=drain(a,created['job_id'])
    assert done['status']=='succeeded'
    assert len(started)==1
    assert len([x for x in done['steps'] if x['name']=='file.inspect'])==1


def test_legacy_understanding_keeps_plan_after_model_timeout(tmp_path):
    class BrokenGateway:
        def propose_data_understanding(self, goal, schema):
            raise TimeoutError('provider unavailable')
    raw=SmartImportService(tmp_path)
    m=raw.ingest(io.BytesIO(b'Time,A2,A14\n2024-01-01,M1,10\n2024-02-01,M1,12\n'), 'a.csv')
    agent=DataUnderstandingService(raw,BrokenGateway())
    plan=agent.propose(m['import_id'],UnderstandingRequest(use_model=True))
    assert plan['advice']['model_status']=='fallback'
    assert plan['advice']['model_error_type']=='TimeoutError'
    assert agent.plan(m['import_id'],plan['plan_id'])['plan_sha256']==plan['plan_sha256']


def test_one_launcher_source_verification_is_side_effect_free():
    done=subprocess.run(['bash',str(ROOT/'TIMEPILOT.sh'),'source-check'],cwd=ROOT,
                        text=True,capture_output=True,timeout=25)
    assert done.returncode==0,done.stdout+'\n'+done.stderr
    assert '[PASS] TimePilot preflight' in done.stdout
    assert 'Active Python' in done.stdout
    assert 'conda create' not in done.stdout


def test_embedded_agent_worker_processes_jobs_without_manual_pump(tmp_path):
    from fastapi.testclient import TestClient
    app=create_app(home=tmp_path,embedded_worker=True)
    _,version,aid=sample(app)
    with TestClient(app) as client:
        response=client.post('/api/v1/agent/jobs',json={'goal':'quality','asset_id':aid,
                         'version_id':version['version_id']})
        assert response.status_code==201,response.text
        identifier=response.json()['job_id']
        deadline=time.monotonic()+8
        outcome=None
        while time.monotonic()<deadline:
            result=client.get('/api/v1/agent/jobs/'+identifier)
            assert result.status_code==200
            outcome=result.json()
            if outcome['status'] not in {'queued','running'}: break
            time.sleep(.1)
        assert outcome['status']=='succeeded',outcome
        assert [step['name'] for step in outcome['steps']]==['quality.asset']
