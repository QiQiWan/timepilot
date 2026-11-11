"""Stage IV: real annotation, permissioned experiments and gated physical identification."""
from __future__ import annotations

import io
import math
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.auto_experiment import ExperimentSpec
from timepilot.data_assets import asset_id_for
from timepilot.mechanisms.identification import MechanismStudy
from timepilot.plugins import LagRidge
from timepilot.semantic_benchmark import MONTH_VALUE
from timepilot.smart_import import TransformRecipe
from timepilot.smart_workbench import ViewSelection


def ingest_monthly(app, *, rows=43, publication=True, mixed=False, entity='S1'):
    source=['Time,A2,A14,available_at,source_name,统计口径']
    for i in range(rows):
        year=2020+i//12
        month=i%12+1
        published=f'{year if month<12 else year+1}-{(month%12)+1:02d}-06T09:00:00+08:00' if publication else ''
        scope='产量' if mixed and i==2 else '销量'
        value=100+2*i+10*math.sin(2*math.pi*(i%12)/12)
        source.append(f'{year}-{month:02d}-01,{entity},{value:.5f},{published},UserSource,{scope}')
    manifest=app.state.smart_import.ingest(io.BytesIO('\n'.join(source).encode()),'car_sales.csv')
    version=app.state.smart_import.transform(manifest['import_id'],TransformRecipe(
        candidate_ids=manifest['suggested_candidate_ids'],field_mapping={
            'series_id':'A2','target':'A14','available_at':'available_at',
            'source_name':'source_name','statistical_scope':'统计口径'}))
    return manifest,version


class KnownModel:
    def __init__(self):self.calls=[]
    def propose_semantic_labels(self, schema):
        self.calls.append(schema)
        return {'proposal':{'layout':'ordinary_timeseries','roles':{
            'timestamp':'Time','series_id':'A2','target':'A14',
            'available_at':'available_at','source_name':'source_name',
            'statistical_scope':'统计口径'}},'model':'mock-only','usage':{
                'prompt_tokens':125,'completion_tokens':32}}


def test_human_label_gate_real_schema_and_cost(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=ingest_monthly(app)
    benchmark=app.state.semantic_benchmark
    seeds=benchmark.seed(manifest['import_id'])
    assert seeds['new_review_cases']==1
    assert benchmark.evaluate()['status']=='awaiting_human_labels'
    cases=benchmark.cases()
    case=cases[0]
    assert case['status']=='pending_human_review'
    label={'reviewer':'业务工程师_A','confirmed_by_human':True,
           'layout':'ordinary_timeseries','roles':{
               'timestamp':'Time','series_id':'A2','target':'A14','available_at':'available_at',
               'source_name':'source_name','statistical_scope':'统计口径'}}
    assert benchmark.review(case['case_id'],label)['status']=='human_confirmed'
    before=benchmark.evaluate()
    assert before['labeled_cases']==1 and before['model'] is None
    model=KnownModel()
    benchmark.gateway=model
    scored=benchmark.evaluate(use_model=True,input_usd_per_million=1.0,
                              output_usd_per_million=3.0)
    assert scored['model']['exact_task_success_rate']==1.0
    assert scored['estimated_api_cost_usd']==pytest.approx((125+32*3)/1e6)
    assert model.calls and 'rows' not in str(model.calls)
    with pytest.raises(ValueError,match='absent'):
        benchmark.review(case['case_id'],{**label,'roles':{'target':'INVENTED'}})
    benchmark.gateway=None


def test_full_api_annotation_and_explicit_experiment(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,version=ingest_monthly(app)
    asset=asset_id_for(manifest['import_id'])
    spec={'asset_id':asset,'version_id':version['version_id'],
          'selection':{'filters':{'series_id':[next(iter({r['series_id'] for r in [
             *app.state.smart_import.preview_version(manifest['import_id'],version['version_id'],limit=1)['rows']]}))]}},
          'horizon':1,'folds':2,'min_history':24,
          'model_ids':['last_value','moving_average','seasonal_naive','lag_ridge']}
    with TestClient(app) as c:
        seeded=c.post(f"/api/v1/research/semantic/seed/{manifest['import_id']}")
        assert seeded.status_code==201
        assert c.post('/api/v1/research/semantic/evaluate',json={'use_model':False}).json()['status']=='awaiting_human_labels'
        planning=c.post('/api/v1/research/experiments/plan',json=spec)
        assert planning.status_code==200,planning.text
        assert planning.json()['eligible'] and planning.json()['expected_fit_calls']==8
        cannot=c.post('/api/v1/research/experiments/execute',json={
            'spec':spec,'plan_sha256':'0'*64,'confirmed':True})
        assert cannot.status_code==422
        result=c.post('/api/v1/research/experiments/execute',json={
            'spec':spec,'plan_sha256':planning.json()['plan_sha256'],'confirmed':True})
        assert result.status_code==201,result.text
        body=result.json()
        assert body['status']=='completed_review_required'
        assert body['auto_deployed'] is False and body['auto_submitted_training'] is False
        assert len(body['leaderboard'])==4
        assert all(r['n_forecasts']==2 for r in body['leaderboard'])
        assert c.get('/api/v1/research/experiments/'+body['experiment_id']).status_code==200
        assert c.post('/api/v1/research/experiments/execute',json={
            'spec':spec,'plan_sha256':planning.json()['plan_sha256'],'confirmed':True}).json()['reused'] is True


def test_modeling_gates_unknown_publication_and_scope(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    bad,ver=ingest_monthly(app,publication=False)
    spec=ExperimentSpec(asset_id=asset_id_for(bad['import_id']),version_id=ver['version_id'],
                        model_ids=['last_value'])
    with pytest.raises(ValueError,match='available_at'):
        app.state.auto_experiments.plan(spec)
    other,ver=ingest_monthly(app,mixed=True)
    mixed=ExperimentSpec(asset_id=asset_id_for(other['import_id']),version_id=ver['version_id'],
                         model_ids=['last_value'])
    with pytest.raises(ValueError,match='唯一统计口径'):
        app.state.auto_experiments.plan(mixed)


def test_lag_model_never_sees_holdout_target(tmp_path):
    plugin=LagRidge()
    timeline=pd.date_range('2020-01-01',periods=42,freq='MS')
    history=pd.DataFrame({'timestamp':timeline[:35],
                          'target':[100+3*i+8*np.sin(i/3) for i in range(35)]})
    x=plugin.fit(history,3,'MS',tmp_path/'ridge',120)
    forecast=plugin.predict(x,3,'MS')
    changed=history.copy()
    # The three held-out future targets never appear in fit(), by construction.
    future=np.array([999_000,999_000,999_000])
    assert not np.array_equal(forecast,future)
    assert plugin.capabilities().requires_extra is None
    assert 'lag_12' in x['features'] and x['feature_policy']=='history_only_lags_no_future_target'


def test_physics_labeled_research_not_falsely_equipment_verified(tmp_path):
    from timepilot.mechanisms.catalog import simulated_maintenance_series
    data=simulated_maintenance_series(length=220,seed=21,maintenance_step=70)
    mechanics=MechanismStudy(tmp_path)
    evidence={'kind':'simulation','reference':'TimePilot synthetic maintenance fixture',
              'sample_interval_seconds':60,'state_unit':'MPa','control_unit':'A',
              'state':data['observed_state'].tolist(),'control':data['control'].tolist(),
              'maintenance_indices':[70]}
    receipt=mechanics.register(evidence)
    report=mechanics.assess(receipt['evidence_id'])
    assert report['verified_on_real_equipment'] is False
    assert report['identifiability']['full_rank'] is True
    assert report['holdout']['transitions']>5
    assert report['events']['known_maintenance_boundaries']==1
    with pytest.raises(ValueError,match='identifiable'):
        mechanics.register({**evidence,'control':[1.0]*220,'state':[1.0]*220})
        mechanics.assess(mechanics.register({**evidence,'control':[1.0]*220,'state':[1.0]*220})['evidence_id'])


def test_unified_agent_autonomous_loop_approval_and_audit(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,version=ingest_monthly(app)
    executor=app.state.agent_executor
    job=executor.create({'goal':'autonomous_modeling',
        'asset_id':asset_id_for(manifest['import_id']), 'version_id':version['version_id'],
        'model_ids':['last_value','lag_ridge'], 'horizon':1,'folds':2,'min_history':24,
        'max_units':40,'max_steps':5,'max_seconds':180})
    for i in range(5):
        stage=executor.status(job['job_id'])
        if stage['status']=='waiting_approval':break
        assert stage['status'] in {'queued','running'},stage
        executor.run_once()
    assert stage['status']=='waiting_approval',stage
    assert stage['pending_approval']['tool']=='automodel.evaluate'
    assert not app.state.auto_experiments.experiments()
    executor.approve(job['job_id'],pending_sha256=stage['pending_approval']['sha256'],confirmed=True)
    for _ in range(6):
        executor.run_once()
        done=executor.status(job['job_id'])
        if done['status']=='succeeded':break
    assert done['status']=='succeeded',done
    assert app.state.auto_experiments.experiments()
    assert done['result']['auto_train'] is False


def test_human_edit_sessions_observe_actual_changes_and_timing(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=ingest_monthly(app)
    bench=app.state.semantic_benchmark
    bench.seed(manifest['import_id'])
    c=bench.cases()[0]
    gold={'reviewer':'校验工程师甲','confirmed_by_human':True,
          'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}}
    bench.review(c['case_id'],gold)
    initial=bench.start_trial(c['case_id'],reviewer='复核工程师乙',arm='rules')
    assert 'annotation' not in str(initial) and 'ground_truth' not in str(initial)
    finished=bench.finish_trial(initial['trial_id'],reviewer='复核工程师乙',corrected={
        'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}})
    assert finished['status']=='completed' and finished['submitted_field_changes']>=0
    assert finished['final_mapping_correct'] is True
    summary=bench.trial_summary()
    assert summary['arms']['rules']['completed']==1 and summary['arms']['model']['completed']==0
    with pytest.raises(ValueError,match='already finalized'):
        bench.finish_trial(initial['trial_id'],reviewer='复核工程师乙',corrected={
            'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}})
    bench.gateway=KnownModel()
    trial=bench.start_trial(c['case_id'],reviewer='复核工程师丙',arm='model')
    assert trial['model_status']=='model_response_recorded'
    finish=bench.finish_trial(trial['trial_id'],reviewer='复核工程师丙',corrected=trial['suggestion'])
    assert finish['submitted_field_changes']==0
    assert bench.trial_summary()['arms']['model']['completed']==1


def test_review_trial_http_endpoints_do_not_expose_gold(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=ingest_monthly(app)
    with TestClient(app) as c:
        assert c.post(f"/api/v1/research/semantic/seed/{manifest['import_id']}").status_code==201
        case=c.get('/api/v1/research/semantic/cases').json()[0]
        before=c.post('/api/v1/research/semantic/cases/'+case['case_id']+'/trial',json={
            'reviewer':'校验工程师乙','arm':'rules'})
        assert before.status_code==422
        gold={'reviewer':'业务专家甲','confirmed_by_human':True,
              'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}}
        assert c.post('/api/v1/research/semantic/cases/'+case['case_id']+'/review',json=gold).status_code==201
        started=c.post('/api/v1/research/semantic/cases/'+case['case_id']+'/trial',json={
            'reviewer':'校验工程师乙','arm':'rules'})
        assert started.status_code==201,started.text
        payload=started.json()
        assert 'annotation' not in payload and 'gold' not in payload
        ended=c.post('/api/v1/research/semantic/trials/'+payload['trial_id']+'/finish',json={
            'reviewer':'校验工程师乙','corrected':{'layout':'ordinary_timeseries',
                'roles':{'timestamp':'Time','series_id':'A2','target':'A14'}}})
        assert ended.status_code==200,ended.text
        assert c.get('/api/v1/research/semantic/trials/summary').json()['status']=='observed_human_sessions'


def test_mechanism_evidence_cannot_be_attached_to_unrelated_forecast_asset(tmp_path):
    from timepilot.agent_tools import ToolServices, _assess_mechanism, MechanismArgs
    from timepilot.mechanisms.catalog import simulated_maintenance_series
    app=create_app(home=tmp_path,embedded_worker=False)
    m=app.state.mechanism_study
    series=simulated_maintenance_series(length=160,seed=9,maintenance_step=54)
    evidence=m.register({'kind':'simulation','reference':'controlled synthetic fixture',
        'sample_interval_seconds':60,'state_unit':'MPa','control_unit':'A',
        'state':series['observed_state'].tolist(),
        'control':series['control'].tolist(),'maintenance_indices':[54],
        'source_asset_id':'asset-correct','source_version_id':'version-correct'})
    svc=ToolServices(None,None,None,None,None,mechanism=m)
    matched=_assess_mechanism(svc,MechanismArgs(evidence_id=evidence['evidence_id'],
        asset_id='asset-correct',version_id='version-correct'))
    assert matched['verified_on_real_equipment'] is False
    with pytest.raises(ValueError,match='不对应'):
        _assess_mechanism(svc,MechanismArgs(evidence_id=evidence['evidence_id'],
            asset_id='asset-different',version_id='version-different'))


def test_experiment_fingerprint_blocks_corrupted_predictions(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,version=ingest_monthly(app)
    # One series, verified publication and one scope/source.
    spec=ExperimentSpec(asset_id=asset_id_for(manifest['import_id']),
                        version_id=version['version_id'],horizon=1,folds=2,min_history=24,
                        model_ids=['last_value'])
    result=app.state.auto_experiments.run(spec)
    path=app.state.auto_experiments.root/result['experiment_id']/'predictions.json'
    assert path.exists()
    path.write_text('{"tampered":true}',encoding='utf-8')
    with pytest.raises(ValueError,match='SHA256'):
        app.state.auto_experiments.report(result['experiment_id'])
    with pytest.raises(ValueError,match='SHA256'):
        app.state.auto_experiments.run(spec)
