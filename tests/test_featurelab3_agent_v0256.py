"""v0.25.6: step dependence, accurate queued receipts and deterministic tool search."""
from test_conversational_bi_v0231 import _env
from timepilot.feature_agent import FeatureAgent
from timepilot.feature_tool_manifest import manifest,choose_tools
from timepilot.feature_contracts import PlanRequest
from timepilot.feature_evaluation import paired_fold_comparison
import pytest


def test_tool_search_does_not_use_arbitrary_first_twelve():
    tools=manifest(engine_ready=True)
    chosen=choose_tools('请评估特征对于质量预测的模型效果并比较实验结果',tools,max_tools=8)
    assert any(t['tool_id']=='feature.evaluation.plan' for t in chosen)
    assert all(t['available'] for t in chosen)
    assert len(chosen)<=8
    assert not any(t['tool_id']=='feature.join.virtual' for t in chosen)


def test_two_step_plan_awaits_actual_job_receipt(tmp_path,monkeypatch):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        agent=FeatureAgent(app.state.projects,app.state.native_bi,
                           app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
        monkeypatch.setattr(agent,'tools',lambda *a:{'asset_id':aid,'tools':manifest(engine_ready=True)})
        request=PlanRequest(message='对 A1 和 A2 做移动平均并预览结果',asset_id=aid,
                            version_id='base',request_id='two-step-preview')
        plan=agent.plan(pid,request)
        assert plan['status']=='ready',plan
        assert [x['tool_id'] for x in plan['plan']['actions']]==[
           'feature.set.draft_create','feature.set.preview']
        assert plan['plan']['actions'][1]['args']['set_id']=='$step.0.set_id'
        first=agent.execute(pid,plan['plan_id'])
        assert first['status']=='awaiting_job',first
        assert first['steps'][0]['status']=='succeeded'
        assert first['steps'][1]['status']=='awaiting_job'
        assert len(first['observations'])==2
        job_id=first['steps'][1]['result']['job_id']
        # Simulated worker receipt: this tests control flow only, NOT real
        # numerical calculation, Parquet materialization or plot rendering.
        with app.state.projects._lock,app.state.projects._db() as db:
            db.execute("UPDATE feature_jobs SET status='succeeded',result_json=? WHERE job_id=?",
                       ('{"scope":"sample","rows_returned":3,"materialized":false}',job_id))
        completed=agent.execute(pid,plan['plan_id'])
        assert completed['status']=='succeeded'
        assert completed['steps'][1]['result']['job']['result']['materialized'] is False
        assert len(completed['observations'])==3
        assert agent.execute(pid,plan['plan_id'])['status']=='succeeded'
        with pytest.raises(ValueError,match='前序'):
            agent._resolve_args({'set_id':'$step.9.set_id'},completed['steps'],1)
    finally:c.__exit__(None,None,None)


def test_paired_results_never_compare_different_folds():
    folds=[{'metrics':{'raw_only':{'mae':2.0,'rmse':3.0},
                       'with_features':{'mae':1.5,'rmse':2.7}}},
           {'metrics':{'raw_only':{'mae':1.0,'rmse':2.0},
                       'with_features':{'mae':0.5,'rmse':1.9}}}]
    result=paired_fold_comparison(folds,'regression')
    assert result['same_fold_only']
    assert result['metrics']['mae']['n_paired_folds']==2
    assert result['metrics']['mae']['mean_improvement']==pytest.approx(0.5)


def test_replan_requires_feedback_and_keeps_old_plan(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        original=c.post(base+'/agent/plans',json={'message':'无效的描述','asset_id':aid,
                  'version_id':'base','request_id':'replan-initial'})
        assert original.status_code==201,original.text
        doc=original.json();assert doc['status']=='needs_clarification'
        next_plan=c.post(base+'/agent/plans/'+doc['plan_id']+'/replan',
                     json={'feedback':'查看数据字段'})
        assert next_plan.status_code==201,next_plan.text
        assert next_plan.json()['plan']['parent_plan_id']==doc['plan_id']
        assert next_plan.json()['plan']['replan_count']==1
        assert next_plan.json()['plan']['actions'][0]['tool_id']=='feature.schema.inspect'
        assert c.get(base+'/agent/plans/'+doc['plan_id']).json()['status']=='needs_clarification'
    finally:c.__exit__(None,None,None)
