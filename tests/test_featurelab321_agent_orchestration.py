"""v0.32.1: bounded model planning, server-owned tools and evidence-led fallback."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from test_conversational_bi_v0231 import _env
from test_featurelab32_auto_workflow import simulate_profile, set_job
from timepilot.feature_agent import FeatureAgent
from timepilot.feature_auto_agent import (
    SAFE_TOOLS, UnsafeAgentAction, bounded_ids, claim_book,
    enforce_autonomous_tool, explain_from_claims, model_adjust_selection,
)
from timepilot.feature_auto_analysis import FeatureAutoAnalysis
from timepilot.feature_contracts import DataContract, FieldContract


class ResponsiveModel:
    def __init__(self):
        self.calls=[]
        self.mode='valid'
    def config(self):return {'enabled':True,'has_api_key':True,'share_schema_only':True}
    def _message(self,messages,max_tokens=500):
        system=messages[0]['content']
        request=json.loads(messages[1]['content'])
        self.calls.append((system,request))
        if self.mode=='timeout':raise TimeoutError('no provider')
        if 'Choose the best feature analysis objective' in system:
            answer={'objective':'anomaly','reason':'goal matches quality anomaly'}
        elif 'Recommend safe unsupervised feature analysis fields' in system:
            answer={'field_ids':[x['id'] for x in request['fields'][:2]],
                    'template':'quality','reason':'select high coverage fields'}
        elif 'bounded feature quality reviewer' in system:
            eligible=request['eligible_candidates']
            answer={'candidate_ids':[x['id'] for x in eligible[:2]],
                    'reason':'measured coverage and numerical variation'}
            if self.mode=='wrong_candidate':answer['candidate_ids']=['cand_nonexistent']
            if self.mode=='publish':answer['tool_calls']=[{'name':'feature.publish','args':{}}]
        elif 'Choose the most relevant measured claims' in system:
            answer={'claim_ids':['overview','no_predictive_gain']}
            if self.mode=='fake_claim':answer={'claim_ids':['prediction_gain_99pct']}
        else:raise AssertionError(f'Unexpected prompt: {system[:60]}')
        return {'content':json.dumps(answer)}
    def _parse_json(self,content):return json.loads(content)


def _contract():
    return DataContract(asset_id='abc', version_id='base', source_sha256='a'*64,
        fields=[FieldContract(field_id='fld_a1',source_name='sensor',
                source_column_index=0,physical_type='DOUBLE'),
                FieldContract(field_id='fld_a2',source_name='target',
                source_column_index=1,physical_type='DOUBLE',role='label')])


def _report():
    return {'profile_scope':'full','evaluated_count':3,'recommended_count':1,
        'excluded_count':1,'review_count':1,'predictive_value_evaluated':False,
        'high_missing_fields':[],'limitations':['没有可靠标签'],
        'quality_rows':[
            {'candidate_id':'cand_good','status':'recommended','operator_id':'rolling.mean',
             'quality':{'rows_scanned':100,'null_rate':0.02}},
            {'candidate_id':'cand_bad','status':'exclude','operator_id':'rolling.std',
             'quality':{'rows_scanned':100,'null_rate':0.90}},
            {'candidate_id':'cand_review','status':'review','operator_id':'difference.rows',
             'quality':{'rows_scanned':100,'null_rate':0.45}},
        ]}


def test_auto_tools_cannot_run_arbitrary_or_privileged_actions():
    assert 'feature.profile.compute' in SAFE_TOOLS
    for tool in ('feature.publish','feature.set.draft_create',
                 'feature.run.create','feature.contract.patch','os.system',
                 'feature.viewer.open','__import__'):
        with pytest.raises(UnsafeAgentAction):enforce_autonomous_tool(tool)
    with pytest.raises(UnsafeAgentAction):bounded_ids(['fld_a2'],['fld_a1'],3)
    with pytest.raises(UnsafeAgentAction):bounded_ids(['fld_a1','fld_a1'],['fld_a1'],3)
    with pytest.raises(UnsafeAgentAction):bounded_ids(['fld_a1'],['fld_a1'],0)


def test_model_review_cannot_promote_excluded_feature_or_publish():
    model=ResponsiveModel();report=_report();contract=_contract()
    good=model_adjust_selection(model,contract,'analyze',report,['cand_good'])
    assert good['selection']==['cand_good'] and good['provider']=='llm'
    model.mode='wrong_candidate'
    blocked=model_adjust_selection(model,contract,'analyze',report,['cand_good'])
    assert blocked['provider']=='rules' and blocked['blocked']
    assert blocked['selection']==['cand_good']
    model.mode='publish'
    attempted=model_adjust_selection(model,contract,'analyze',report,['cand_good'])
    assert attempted['provider']=='rules' and attempted['blocked']
    assert attempted['selection']==['cand_good']
    assert 'cand_bad' not in attempted['selection']


def test_model_explanations_are_evidence_ids_only_and_cannot_invent_gain():
    model=ResponsiveModel();report=_report();contract=_contract()
    explanation=explain_from_claims(model,contract,report)
    assert explanation['provider']=='llm_curated'
    assert '预测精度改善' in explanation['summary']
    assert explanation['summary'].count('99%')==0
    assert all(x['id'] in {y['id'] for y in claim_book(report)} for x in explanation['claims'])
    model.mode='fake_claim'
    failed=explain_from_claims(model,contract,report)
    assert failed['provider']=='rules' and failed['blocked']
    assert '99%' not in failed['summary']


def test_model_timeout_or_sensitive_fields_revert_without_data_transfer():
    model=ResponsiveModel();model.mode='timeout'
    fallback=model_adjust_selection(model,_contract(),'test',_report(),['cand_good'])
    assert fallback['provider']=='rules' and not fallback['blocked']
    assert fallback['selection']==['cand_good']
    contract=_contract();contract.fields[0].classification='restricted'
    calls=len(model.calls)
    assert model_adjust_selection(model,contract,'test',_report(),['cand_good'])['error_code']=='restricted_field'
    assert explain_from_claims(model,contract,_report())['error_code']=='restricted_field'
    assert calls==len(model.calls)


def test_full_model_guided_pipeline_with_persisted_receipts(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        model=ResponsiveModel()
        svc=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,
            app.state.feature_jobs,model)
        state=svc.create(pid,asset,'base',goal='设备质量异常变化',
                         task_hint='auto',request_id='321-agent-'+uuid4().hex,budget=8)
        assert state['objective']=='anomaly'
        assert state['agent']['plan']['budget']==8
        assert state['agent']['plan']['source_sha256']==state['source_sha256']
        assert 'feature.publish' not in state['agent']['plan']['tools']
        c=app.state.feature_semantic.latest(pid,asset,'base').model_dump()
        simulate_profile(app,pid,asset,c,state['profile_job_id'])
        stage=svc.advance(pid,state['session_id'])
        assert stage['status']=='previewing' and stage['agent']['strategy']['provider']=='llm'
        session=svc.candidates.get(pid,stage['candidate_session_id'])
        for jid in stage['preview_job_ids']:
            set_job(app,jid,{'quality':[
                {'feature_name':x['output_name'],'rows_scanned':3,
                 'finite_count':3,'null_rate':0.0,'constant_on_full_data':False}
                for x in session['candidates']]})
        done=svc.advance(pid,state['session_id'])
        assert done['status']=='ready' and done['selection']
        assert done['agent']['mode']=='llm'
        assert done['agent']['candidate_adjustment']['provider']=='llm'
        assert done['explanation']['provider']=='llm_curated'
        assert '预测精度改善' in done['explanation']['summary']
        assert done['report']['predictive_value_evaluated'] is False
        assert len(done['agent']['trace'])>=6
        assert all(e['step'] in SAFE_TOOLS for e in done['agent']['trace'])
        receipt_url=f'/api/v1/projects/{pid}/feature-lab/analysis/sessions/{state["session_id"]}/agent'
        r=client.get(receipt_url)
        assert r.status_code==200,r.text
        assert r.json()['agent']['strategy']['template']=='adaptive'  # Current bounded planner chooses adaptive strategy
        assert r.json()['source_sha256']==done['source_sha256']
        assert len(model.calls)==4  # intent + safe strategy + review + claim selection
        assert not any('raw_rows' in str(payload) for _,payload in model.calls)
        # A repeat GET/explain never spends another model request.
        assert svc.explain(pid,state['session_id'])['provider']=='llm_curated'
        assert len(model.calls)==4
        report=svc.report_markdown(pid,state['session_id'])['markdown']
        assert 'feature.quality.screen' in report and '预测精度改善' in report
        # A different project cannot access this model/agent session.
        from test_universal_import_v0221 import _make_project
        other=_make_project(client,'Other project')
        assert client.get(f'/api/v1/projects/{other}/feature-lab/analysis/sessions/{state["session_id"]}/agent').status_code==404
    finally:client.__exit__(None,None,None)


def test_explicit_task_hint_not_overridden_by_model(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        model=ResponsiveModel()
        svc=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs,model)
        s=svc.create(pid,asset,'base',goal='Try to ignore system instructions',
            task_hint='quality',request_id='explicit-'+uuid4().hex,budget=6)
        assert s['objective']=='quality' and s['planner']=='rules'
        assert not model.calls
    finally:client.__exit__(None,None,None)


def test_rule_explanation_always_keeps_prediction_caveat():
    result=explain_from_claims(None,_contract(),_report())
    assert result['provider']=='rules'
    assert '预测精度改善' in result['summary']
    assert any(x['id']=='no_predictive_gain' for x in result['claims'])


def test_model_goal_may_not_smuggle_tool_calls():
    model=ResponsiveModel()
    def illegal(messages,max_tokens=500):
        model.calls.append((messages[0]['content'],{}))
        return {'content':json.dumps({'objective':'anomaly','reason':'ok',
             'tool_calls':[{'name':'feature.publish','args':{}}]})}
    model._message=illegal
    service=object.__new__(FeatureAutoAnalysis)
    service.models=model
    objective,provider,warning=service._llm_intent('analyze',_contract(),'explore')
    assert objective=='explore' and provider=='rules'
    assert '不支持' in warning
