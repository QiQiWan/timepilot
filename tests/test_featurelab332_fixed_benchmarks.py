"""v0.33.2 reproducible *synthetic* holdout contracts and leakage guards.

The committed CSV files are deterministic fixtures, not evidence of actual
factory/model performance. These tests run without DuckDB; the companion
real DuckDB suite checks materialized feature computations separately.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from timepilot.feature_contracts import (
    DataContract, FieldContract, FeatureDefinition, FeatureSpecV2,
    SourceRef, TaskSpecV2, EvaluationProtocol, QualitySelectionPolicy, digest,
)
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_evaluation import _evaluate_matrix
from timepilot.feature_split_protocol import build_folds, build_static_folds
from timepilot.feature_safety import ensure_pit_evidence
from timepilot.feature_auto_delivery import confirmed_task, supervision_options

ROOT = Path(__file__).parent/'fixtures'/'featurelab_v0332'
MANIFEST=json.loads((ROOT/'MANIFEST.json').read_text(encoding='utf-8'))


def fixed(name):
    raw=(ROOT/name).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==MANIFEST[name]['sha256']
    frame=pd.read_csv(ROOT/name)
    assert len(frame)==MANIFEST[name]['rows']
    return frame


def contract(frame, *, asset='a', source_sha='f'*64, entity=(), time=False):
    time_names={'event_at','available_at','decision_at','label_at'}
    fields=[]
    for col,name in enumerate(frame.columns):
        role=('label' if name=='target' else 'entity_key' if name in entity else
              'event_time' if name=='event_at' else 'available_at' if name in ('available_at','label_at') else
              'prediction_time' if name=='decision_at' else 'value')
        typ='TIMESTAMP' if name in time_names else 'VARCHAR' if name in entity else 'DOUBLE'
        semantic='datetime' if typ=='TIMESTAMP' else 'id' if typ=='VARCHAR' else 'numeric'
        fields.append(FieldContract(field_id='fld_'+name, source_name=name,
            source_column_index=col,physical_type=typ,semantic_type=semantic,
            role=role,confirmation='confirmed',
            timezone='UTC' if typ=='TIMESTAMP' else None))
    return DataContract(asset_id=asset,version_id='base',source_sha256=source_sha,
        revision=2,state='confirmed',fields=fields,
        entity_field_ids=['fld_'+x for x in entity],
        event_time_field_id='fld_event_at' if time else None,
        available_at_field_id='fld_available_at' if time else None,
        timezone='UTC' if time else None)


def make_protocol(kind='regression',split='kfold',groups=(),seed=3302):
    task=TaskSpecV2(task_id='fixed-'+split,mode='supervised',task_type=kind,
        label_field_id='fld_target',split_kind=split,
        static_independence_confirmed=(split in ('kfold','stratified','group')),
        entity_field_ids=['fld_'+g for g in groups],
        group_field_ids=['fld_'+g for g in groups] if split in ('group','grouped_time') else [],
        prediction_time_field_id='fld_decision_at' if split in ('time','grouped_time') else None,
        label_available_at_field_id='fld_label_at' if split in ('time','grouped_time') else None,
        n_splits=3)
    return EvaluationProtocol(task=task,set_id='fixed-v0332',revision=1,seed=seed,
        selection=QualitySelectionPolicy(max_selected=4,min_pair_observations=16),
        models=['linear'])


def eval_fixed(frame,protocol,base,output,con):
    return _evaluate_matrix(frame,base,output,protocol.task,protocol,con,
         source_sha256=con.source_sha256,spec_sha256='d'*64)


def test_static_regression_fixed_data_is_reproducible_with_fold_only_selection():
    f=fixed('static_regression.csv');c=contract(f);p=make_protocol()
    a=eval_fixed(f,p,['feature_a','feature_b'],
                 ['feature_b_squared','duplicate_a','constant_candidate'],c)
    b=eval_fixed(f,p,['feature_a','feature_b'],
                 ['feature_b_squared','duplicate_a','constant_candidate'],c)
    assert a['split_kind']=='kfold'
    assert len(a['folds'])==3
    assert a['split_manifest_sha256']==b['split_manifest_sha256']
    assert a['paired_comparison']==b['paired_comparison']
    assert a['selection_frequency']==b['selection_frequency']
    assert a['label_availability_checked'] is False
    assert a['selection_stability']['method']=='pairwise_jaccard_training_selection'
    assert 0<=a['selection_stability']['mean_jaccard']<=1
    assert all(z['training_cutoff'] is None for z in a['folds'])
    for man,fold in zip(a['fold_manifest'],a['folds']):
        assert man['split_kind']=='kfold' and man['train_rows_sha256']==fold['train_rows_sha256']
        assert man['valid_rows_sha256']==fold['valid_rows_sha256']
        assert 'constant_candidate' not in fold['training_feature_selection']['selected_features']
        assert 'duplicate_a' not in fold['training_feature_selection']['selected_features']
        assert set(fold['metrics'])=={'raw_only','with_features','selected_features'}
        assert set(fold['metrics']['raw_only'])=={'mae','rmse'}
    assert a['selection_frequency'][0]['selected_in_folds']<=3
    assert a['stability_summary']['outer_folds']==3
    assert a['paired_comparison']['metrics']['mae']['n_paired_folds']==3
    assert 0<a['folds'][0]['metrics']['raw_only']['mae']
    assert a['folds'][0]['metrics']['with_features']['mae']>=0


def test_fixed_binary_uses_stratification_and_real_0_1_metrics():
    f=fixed('static_binary.csv');c=contract(f);p=make_protocol('binary_classification','stratified')
    r=eval_fixed(f,p,['sensor_a','sensor_b'],['interaction','duplicate_a','constant_candidate'],c)
    assert r['split_kind']=='stratified'
    assert r['label_contract']['class_counts']['1']>40
    assert len(r['folds'])==3
    assert r['selection_stability']['compared_pairs']==3
    for fold in r['folds']:
        q=fold['validation_label_quality']
        assert q['positive_count']>=10 and q['negative_count']>=10
        assert q['sample_count']==fold['valid_count']
        for variant in ('raw_only','with_features','selected_features'):
            m=fold['metrics'][variant]
            assert 0<=m['average_precision']<=1
            assert 0<=m['recall']<=1
            assert m['tp']+m['fn']==q['positive_count']
            assert m['fp']+m['tn']==q['negative_count']
        assert fold['training_feature_selection']['scope']=='outer_training_fold_only'
    assert r['paired_comparison']['metrics']['average_precision']['n_paired_folds']==3


def test_group_static_holdout_never_shares_device_ids():
    f=fixed('static_binary.csv').copy();f['site']=['site_'+str(x//30) for x in range(len(f))]
    c=contract(f,entity=('site',))
    p=make_protocol('binary_classification','group',groups=('site',))
    r=eval_fixed(f,p,['sensor_a','sensor_b'],['interaction','constant_candidate'],c)
    folds,man=build_static_folds(f,f['target'].to_numpy(),split_kind='group',
                                 n_splits=3,seed=p.seed,group_columns=['site'])
    for a,b in folds:
        assert set(f.site.iloc[a]).isdisjoint(set(f.site.iloc[b]))
    assert all(m['group_disjoint_verified'] for m in r['fold_manifest'])
    assert r['split_manifest_sha256']==digest(r['fold_manifest'])
    assert r['label_availability_checked'] is False


def test_multiple_devices_time_folds_use_actual_times_and_filtered_train_hash():
    f=fixed('multi_device_time.csv');c=contract(f,entity=('device',),time=True)
    p=make_protocol('regression','time',groups=('device',))
    r=eval_fixed(f,p,['pressure','temperature'],['pressure_mean_3'],c)
    assert r['split_kind']=='time' and r['label_availability_checked'] is True
    assert len(r['folds'])==3
    times=pd.to_datetime(f['decision_at'],utc=True)
    label_at=pd.to_datetime(f['label_at'],utc=True)
    folds,original=build_folds(f,time_column='decision_at',n_splits=3)
    for i,(outer_train,valid) in enumerate(folds):
        cutoff=times.iloc[outer_train].max()
        actual=outer_train[(label_at.iloc[outer_train]<=cutoff).to_numpy()]
        m=r['fold_manifest'][i]
        assert m['train_count']==len(actual)
        assert m['train_rows_sha256']==digest(actual.tolist())
        assert m['train_rows_sha256']!=original[i]['train_rows_sha256']
        assert set(actual).isdisjoint(set(valid))
        assert times.iloc[actual].max()<times.iloc[valid].min()
        assert r['folds'][i]['training_cutoff']==cutoff.isoformat()
        assert r['folds'][i]['group_stability'][0]['groups'][0]['field']=='device'
        assert r['folds'][i]['feature_stability']['basis']=='train_quantiles_validation_monitor_only'
    assert r['paired_comparison']['same_fold_only'] is True


def test_changing_outer_validation_labels_cannot_change_training_selection():
    f=fixed('static_regression.csv');c=contract(f);p=make_protocol()
    original=eval_fixed(f,p,['feature_a','feature_b'],['feature_b_squared','duplicate_a'],c)
    folds,_=build_static_folds(f,f['target'].to_numpy(),split_kind='kfold',n_splits=3,seed=p.seed)
    f2=f.copy();f2.loc[folds[0][1],'target']+=1000
    changed=eval_fixed(f2,p,['feature_a','feature_b'],['feature_b_squared','duplicate_a'],c)
    assert original['fold_manifest']==changed['fold_manifest']
    assert original['folds'][0]['training_feature_selection']==changed['folds'][0]['training_feature_selection']
    assert original['folds'][0]['metrics']['raw_only']['mae']!=changed['folds'][0]['metrics']['raw_only']['mae']
    assert original['folds'][0]['training_cutoff'] is None


def test_no_attestation_or_invalid_split_is_rejected():
    with pytest.raises(ValidationError,match='独立性'):
        TaskSpecV2(task_id='bad',mode='supervised',task_type='regression',
                   label_field_id='fld_target',split_kind='kfold')
    with pytest.raises(ValidationError,match='分组'):
        TaskSpecV2(task_id='bad',mode='supervised',task_type='regression',
                   label_field_id='fld_target',split_kind='kfold',entity_field_ids=['fld_device'],
                   static_independence_confirmed=True)
    with pytest.raises(ValidationError,match='时间'):
        TaskSpecV2(task_id='bad',mode='supervised',task_type='regression',
                   label_field_id='fld_target',split_kind='kfold',prediction_time_field_id='fld_fake',
                   static_independence_confirmed=True)
    with pytest.raises(ValueError,match='未分箱'):
        c=contract(fixed('static_regression.csv'))
        task=make_protocol('regression','stratified').task
        from timepilot.feature_safety import ensure_supervised_contract
        ensure_supervised_contract(c,task)


def test_static_feature_compiler_blocks_targets_rolling_and_unconfirmed_sources():
    f=fixed('static_regression.csv');c=contract(f);task=make_protocol().task
    def spec(feature):
        return FeatureSpecV2(name='fixed',source=SourceRef(asset_id=c.asset_id,version_id=c.version_id,
            source_sha256=c.source_sha256),contract_revision=c.revision,task=task,
            features=[feature])
    def binary(fields,operator='arithmetic.binary',parameters=None):
        return FeatureDefinition(feature_id='f',output_name='engineered',operator_id=operator,
            operator_version='2.0',input_field_ids=fields,parameters=parameters or {'operator':'*'})
    compiled=FeatureCompiler(c,spec(binary(['fld_feature_b','fld_feature_b'])))
    proof=compiled.validate_point_in_time(None)
    assert proof['status']=='verified' and proof['point_in_time_applicable'] is False
    assert proof['validation_kind']=='static_row_local'
    ensure_pit_evidence(compiled.spec,task,{
        'source_sha256':c.source_sha256,'pit':proof},c)
    badproof={**proof,'validation_kind':'temporal_point_in_time'}
    with pytest.raises(ValueError,match='逐行'):
        ensure_pit_evidence(compiled.spec,task,{'source_sha256':c.source_sha256,'pit':badproof},c)
    with pytest.raises(ValueError,match='目标标签'):
        FeatureCompiler(c,spec(binary(['fld_target','fld_feature_b'])))
    with pytest.raises(ValueError,match='逐行'):
        FeatureCompiler(c,spec(binary(['fld_feature_b'],'rolling.mean',{'window':3,'order_field_id':'fld_feature_a'})))
    with pytest.raises(ValueError,match='普通输入'):
        bad=c.model_copy(deep=True)
        next(field for field in bad.fields if field.field_id=='fld_feature_b').confirmation='unconfirmed'
        FeatureCompiler(bad,spec(binary(['fld_feature_b','fld_feature_b'])))


def test_auto_supervision_options_static_and_existing_temporal_hash_are_compatible():
    f=fixed('static_binary.csv');c=contract(f)
    options=supervision_options(c,'classification')
    assert options['static_eligible'] is True and options['eligible'] is False
    assert options['static_default_split']=='stratified'
    t=confirmed_task(c,objective='classification',session_id='test332',
       label_field_id='fld_target',split_kind='stratified',static_independence_confirmed=True)
    assert t.static_independence_confirmed is True
    assert t.prediction_time_field_id is None
    with pytest.raises(ValueError,match='确认'):
        confirmed_task(c,objective='classification',session_id='test332',
           label_field_id='fld_target',split_kind='stratified')
    old=TaskSpecV2(task_id='legacy',mode='supervised',task_type='binary_classification',
        label_field_id='fld_target',prediction_time_field_id='fld_decision',
        label_available_at_field_id='fld_label_at')
    assert 'static_independence_confirmed' not in old.model_dump()
    assert old.model_dump_json()==TaskSpecV2.model_validate_json(old.model_dump_json()).model_dump_json()
    assert digest(old)==digest(TaskSpecV2.model_validate_json(old.model_dump_json()))


def test_fold_manifest_changes_with_seed_and_group_shortage_fail_closed():
    f=fixed('static_regression.csv');y=f['target'].to_numpy()
    _,m1=build_static_folds(f,y,split_kind='kfold',n_splits=3,seed=1)
    _,m2=build_static_folds(f,y,split_kind='kfold',n_splits=3,seed=2)
    assert digest(m1)!=digest(m2)
    grouped=f.assign(device='one_device')
    with pytest.raises(ValueError,match='数小于验证折数'):
        build_static_folds(grouped,y,split_kind='group',n_splits=3,
                           group_columns=['device'])


def test_auto_search_is_blocked_until_static_inner_folds_are_group_safe():
    from timepilot.feature_contracts import AutoSearchPolicy
    with pytest.raises(ValidationError,match='自动搜索'):
        EvaluationProtocol(task=make_protocol().task,set_id='x',revision=1,
            search=AutoSearchPolicy(enabled=True))


def test_cannot_hide_known_entity_groups_by_omitting_task_group_fields():
    from timepilot.feature_safety import ensure_supervised_contract
    frame=fixed('static_binary.csv').copy()
    frame['site']=['site_'+str(i//30) for i in range(len(frame))]
    c=contract(frame,entity=('site',))
    # An attacker may send a statically valid task with no entity keys.
    # The server must still enforce every group declared by the data contract.
    forged=TaskSpecV2(task_id='omit-entity',mode='supervised',task_type='binary_classification',
        label_field_id='fld_target',split_kind='kfold',static_independence_confirmed=True)
    with pytest.raises(ValueError,match='实体范围'):
        ensure_supervised_contract(c,forged)


def test_temporal_future_validation_labels_cannot_modify_previous_fit():
    frame=fixed('multi_device_time.csv');c=contract(frame,entity=('device',),time=True)
    protocol=make_protocol('regression','time',groups=('device',))
    first=eval_fixed(frame,protocol,['pressure','temperature'],['pressure_mean_3'],c)
    folds,_=build_folds(frame,time_column='decision_at',n_splits=3)
    altered=frame.copy()
    altered.loc[folds[-1][1],'target']+=999
    again=eval_fixed(altered,protocol,['pressure','temperature'],['pressure_mean_3'],c)
    assert first['fold_manifest']==again['fold_manifest']
    assert first['folds'][0]['training_feature_selection']==again['folds'][0]['training_feature_selection']
    assert first['folds'][0]['model_metrics']==again['folds'][0]['model_metrics']
    assert first['folds'][-1]['metrics']['raw_only']['mae']!=again['folds'][-1]['metrics']['raw_only']['mae']
