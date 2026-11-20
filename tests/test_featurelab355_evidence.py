"""v0.35.5: evidence contract and no fabricated predictive improvement."""
from __future__ import annotations
from types import SimpleNamespace

import pytest
from timepilot.feature_insights import build_insight_brief

SRC='a'*64
REV=5


def contract(labels=1):
    return SimpleNamespace(fields=[SimpleNamespace(role='label',confirmation='confirmed',classification='internal')]
                           if labels else [SimpleNamespace(role='value',confirmation='confirmed',classification='internal')])


def profile():
    return {'status':'ready','source_sha256':SRC,'contract_revision':REV,'row_count':250,
        'profile':{'fields':[{'field_id':'x1','source_name':'压力','null_rate':0.15},
                             {'field_id':'x2','source_name':'温度','null_rate':0.01}]}}


def preview(with_eval=False):
    report={'quality_rows':[{'candidate_id':'a','status':'recommended'},
                            {'candidate_id':'b','status':'review'},
                            {'candidate_id':'c','status':'unverified'}]}
    row={'session_id':'s1','source_sha256':SRC,'contract_revision':REV,
         'status':'ready','stale':False,'report':report}
    if with_eval:
        model_report={'mode':'supervised','task_type':'regression','split_kind':'kfold',
             'source_sha256':SRC,'evaluated_artifact_sha256':'b'*64,
             'evaluated_contract_revision':REV,'model_families':['linear'],
             'folds':[], 'paired_comparison':{'same_fold_only':True,'metrics':{
                'mae':{'mean_improvement':0.3,'n_paired_folds':3}}},
             'screening_recommendations':[{'variant':'with_features','objective':'mae',
                                          'positive_folds':3}],
             'selection_stability':{'mean_jaccard':0.75,'compared_pairs':3},
             'feature_group_ablation':{'groups':[{'group':'rolling','mean_loss_when_removed':0.12,
                                                    'evaluated_folds':3,'positive_loss_folds':2}]}}
        for i,(base,enhanced,selected) in enumerate([(1.2,0.8,0.7),(1.5,1.1,1.0),(1.1,1.0,0.9)]):
            model_report['folds'].append({'fold':i,'model_metrics':{'linear':{
               'raw_only':{'mae':base},'with_features':{'mae':enhanced},
               'selected_features':{'mae':selected}}}})
        row['evaluation']={'report':model_report}
    return row


def proof():
    return {'same_artifact_evaluation':True,'artifact':{'sha256':'b'*64},
            'evaluation':{'evaluation_id':'ev1','mode':'supervised','artifact_sha256':'b'*64}}


def brief(**override):
    return build_insight_brief(source_sha256=SRC,contract_revision=REV,
        data_analysis=override.get('data_analysis',profile()),
        session=override.get('session',preview()),
        delivery=override.get('delivery'),contract=override.get('contract',contract(0)))


def test_no_label_keeps_real_quality_and_hides_all_model_numbers():
    r=brief(session=preview(with_eval=True),delivery=proof())
    assert r['layers']['data_quality']['metrics']['high_missing_fields']==1
    assert r['layers']['feature_quality']['metrics']['quality_passed']==1
    assert r['layers']['feature_quality']['metrics']['unverified']==1
    assert r['model']['status']=='no_confirmed_label'
    assert r['model']['comparisons']==[]
    assert 'mean_improvement' not in r['model']
    assert all(x['claim_type']=='descriptive_observation' for x in r['findings'])
    assert all(x['evidence'][0]['source_sha256']==SRC for x in r['findings'])


def test_unverified_supervision_does_not_show_fake_gain():
    r=brief(session=preview(with_eval=True),contract=contract(1),delivery=None)
    assert r['model']['status']=='awaiting_verified_evaluation'
    assert r['model']['comparisons']==[]
    assert len(r['findings'])>=2


def test_only_matching_verified_artifact_shows_paired_values():
    r=brief(session=preview(with_eval=True),contract=contract(1),delivery=proof())
    model=r['model']
    assert model['status']=='promising_in_validation'
    assert model['fold_count']==3
    assert model['paired_folds']==3
    assert [x['mean'] for x in model['comparisons']]==[pytest.approx(1.26666667),
                 pytest.approx(0.96666667),pytest.approx(0.86666667)]
    assert model['selection_stability']['mean_jaccard']==0.75
    assert model['ablation']['groups'][0]['metric_loss_when_removed']==0.12
    assert model['evidence'][0]['artifact_sha256']=='b'*64
    assert any(x['code']=='paired-model' for x in r['findings'])


def test_mismatched_sha_or_contract_blocks_model_result():
    broken=proof();broken['artifact']['sha256']='c'*64
    assert brief(session=preview(True),delivery=broken,contract=contract(1))['model']['comparisons']==[]
    session=preview(True);session['evaluation']['report']['source_sha256']='0'*64
    assert brief(session=session,delivery=proof(),contract=contract(1))['model']['comparisons']==[]
    session=preview(True);session['evaluation']['report']['evaluated_contract_revision']=REV-1
    assert brief(session=session,delivery=proof(),contract=contract(1))['model']['comparisons']==[]


def test_stale_source_refuses_all_current_claims():
    session=preview(True);session['stale']=True
    result=brief(session=session,delivery=proof(),contract=contract(1))
    assert result['status']=='stale'
    assert not result['findings']
    assert result['model']['status']=='blocked'


def test_poisoned_profile_fingerprint_discarded():
    bad=profile();bad['source_sha256']='f'*64
    r=brief(data_analysis=bad)
    assert r['layers']['data_quality']['status']=='pending'
    assert all(x['code']!='missing:x1' for x in r['findings'])


def test_missing_outer_fold_metric_does_not_claim_improvement():
    state=preview(True)
    state['evaluation']['report']['folds'][-1]['model_metrics']['linear']['with_features'].pop('mae')
    r=brief(session=state,delivery=proof(),contract=contract(1))
    assert r['model']['status']=='insufficient_evidence'
    assert not any(x['code']=='paired-model' for x in r['findings'])
    assert r['model']['comparisons']==[]


def test_negative_folds_do_not_promote_model_gain():
    state=preview(True)
    for fold in state['evaluation']['report']['folds']:
        metrics=fold['model_metrics']['linear']
        metrics['with_features']['mae']=round(metrics['raw_only']['mae']+0.8,7)
    state['evaluation']['report']['paired_comparison']['metrics']['mae']['mean_improvement']=-0.8
    state['evaluation']['report']['screening_recommendations'][0]['positive_folds']=0
    r=brief(session=state,delivery=proof(),contract=contract(1))
    assert r['model']['status']=='no_gain'
    assert '未显示收益' in r['model']['message']


def test_candidate_and_data_findings_have_explicit_machine_evidence():
    r=brief()
    assert r['requires_model_api'] is False
    assert all(x['evidence'] and x['evidence'][0]['path'] for x in r['findings'])
    assert not any('准确率提高' in x['observation'] for x in r['findings'])


def test_internally_inconsistent_model_report_rejected():
    state=preview(True)
    state['evaluation']['report']['paired_comparison']['metrics']['mae']['mean_improvement']=100.0
    result=brief(session=state,delivery=proof(),contract=contract(1))
    assert result['model']['status']=='insufficient_evidence'


def test_selected_variant_missing_fold_does_not_show_partial_three_arm_comparison():
    state=preview(True)
    state['evaluation']['report']['folds'][-1]['model_metrics']['linear']['selected_features'].pop('mae')
    r=brief(session=state,delivery=proof(),contract=contract(1))
    assert r['model']['status']=='insufficient_evidence'
    assert r['model']['comparisons']==[]


def test_incorrect_positive_fold_count_blocks_claim():
    state=preview(True)
    state['evaluation']['report']['screening_recommendations'][0]['positive_folds']=2
    r=brief(session=state,delivery=proof(),contract=contract(1))
    assert r['model']['status']=='insufficient_evidence'
    assert r['model']['comparisons']==[]
