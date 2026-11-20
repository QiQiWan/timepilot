"""v0.38.9 source-bound, reviewable supervised task recommendations."""
from __future__ import annotations

from timepilot.model_task_designer import VERSION, describe_fields


def field(name, *, numeric=0, datetime=0, distinct=30, roles=()):
    return {'name':name, 'sample_numeric_rate':numeric,
            'sample_datetime_rate':datetime,'sample_distinct':distinct,
            'suggested_roles':[{'role':role,'confidence':0.93} for role in ((*roles, 'target_numeric') if numeric>=0.95 else roles)]}


def profile():
    return {'source_rows':1500,'sample_rows':1400,
            'fields':[field('车型',roles=('entity',)),field('统计日期',datetime=1,roles=('time',)),
                      field('销量',numeric=1),field('单车价格',numeric=1),
                      field('批次号',numeric=1),field('终检不良',numeric=1,distinct=2),
                      field('future_inventory',numeric=1)],
            'recommendation':{'time':'统计日期','entity':'车型','target':'销量',
                              'frequency':{'frequency':'D'}}}


def test_goal_disambiguates_y_without_auto_confirming_it():
    result=describe_fields(profile(),'预测未来销量')
    assert result['version']==VERSION
    assert result['targets']['preferred_numeric']=='销量'
    role={x['name']:x for x in result['role_candidates']}
    assert role['销量']['target_confidence']>role['单车价格']['target_confidence']
    assert not role['销量']['confirmed']
    assert result['tasks'][0]['id']=='continuous_forecast'
    assert result['tasks'][1]['id']=='tabular_regression'


def test_id_time_label_and_future_results_not_auto_x():
    result=describe_fields(profile(),'预测销量')
    x=result['suggested_features']
    assert '单车价格' in x
    for blocked in ['车型','统计日期','批次号','终检不良','future_inventory']:
        assert blocked not in x
    role={x['name']:x for x in result['role_candidates']}
    assert role['批次号']['role']=='entity'
    assert role['终检不良']['role']=='label'
    assert '事后' in role['future_inventory']['warnings'][0]


def test_ambiguous_numeric_columns_do_not_choose_fake_target():
    p={'fields':[field('A1',numeric=1),field('A2',numeric=1)],
       'recommendation':{'time':None,'entity':None}}
    result=describe_fields(p,'做个预测')
    assert result['targets']['preferred_numeric'] is None
    assert not result['tasks'][0]['runnable']
    assert result['tasks'][1]['route']=='featurelab'


def test_classification_with_time_and_label_routes_to_reviewed_flow():
    result=describe_fields(profile(),'识别终检不良')
    assert result['targets']['preferred_label']=='终检不良'
    temporal=next(t for t in result['tasks'] if t['id']=='classification')
    static=next(t for t in result['tasks'] if t['id']=='tabular_classification')
    assert temporal['route']=='modeling' and temporal['runnable']
    assert static['route']=='featurelab'


def test_business_goal_never_invents_a_column_or_changes_source():
    p=profile(); names=[x['name'] for x in p['fields']]
    result=describe_fields(p,'<script>alert(1)</script> 预测梦想收益')
    assert [x['name'] for x in result['role_candidates']]==names
    assert result['targets']['preferred_numeric'] is None
    assert p['recommendation']['target']=='销量'


def test_empty_profile_and_long_goal_are_bounded():
    result=describe_fields({'fields':[], 'recommendation':{}},'x'*1000)
    assert len(result['business_goal'])==180
    assert result['role_candidates']==[]
    assert all(not t['runnable'] for t in result['tasks'])


def test_existing_api_proposal_semantics_are_preserved():
    # This is an additive response; no new executable backend task kind exists.
    from timepilot.modeling_workflow import ModelingWorkbench
    class Stub:
        def profile(self,pid,aid,version):return profile()
    output=ModelingWorkbench.suggestions(Stub(),'demo','asset-1',business_goal='预测销量')
    assert output['proposals'][0]['task_kind']=='continuous_forecast'
    assert output['task_designer']['targets']['preferred_numeric']=='销量'
    assert 'tabular_regression' not in [x['task_kind'] for x in output['proposals']]
