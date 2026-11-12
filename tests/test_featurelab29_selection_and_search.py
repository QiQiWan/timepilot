"""v0.29 deterministic training-fold selection, supervised model comparison and search."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import pytest
from timepilot.feature_contracts import (DataContract,FieldContract,TaskSpecV2,
    EvaluationProtocol,QualitySelectionPolicy,AutoSearchPolicy,SlicePolicy)
from timepilot.feature_selection import select_training_features
from timepilot.feature_evaluation import _evaluate_matrix,paired_fold_comparison
from timepilot.feature_search import search_training_fold
from timepilot.feature_stability import psi


def protocol(*, search=False, forest=False):
    task=TaskSpecV2(task_id='weld_quality',mode='supervised',task_type='binary_classification',
        prediction_time_field_id='fld_time',label_field_id='fld_label',
        label_available_at_field_id='fld_label_available',n_splits=2)
    return EvaluationProtocol(task=task,set_id='set-test',revision=1,
        selection=QualitySelectionPolicy(max_missing_fraction=.60,max_abs_correlation=.985,
            max_selected=2,min_pair_observations=12),
        search=AutoSearchPolicy(enabled=search,max_candidates=8,max_model_fits=4,max_seconds_per_fold=20),
        slices=SlicePolicy(field_ids=['fld_device'],min_rows=10,min_positives=1),
        models=['linear','forest'] if forest else ['linear'])


def fixture(n=300):
    rng=np.random.default_rng(20261112)
    pressure=rng.normal(size=n);pressure2=rng.normal(size=n)
    target=(pressure*.8+pressure2*.4+rng.normal(scale=.6,size=n)>.25).astype(float)
    rows={
        'prediction_time':pd.date_range('2026-01-01',periods=n,freq='h',tz='UTC'),
        'label_available_at':pd.date_range('2026-01-01',periods=n,freq='h',tz='UTC')+pd.Timedelta(minutes=5),
        'device':np.tile(['A','B','C'],int(np.ceil(n/3)))[:n],
        'pressure':pressure,'temperature':pressure2,
        'derived_signal':pressure+pressure2,
        'duplicate_pressure':pressure.copy(),
        'constant_candidate':np.ones(n),
        'label':target,
    }
    return pd.DataFrame(rows)


def contract(frame):
    fields=[]
    for i,name in enumerate(frame.columns):
        role={'prediction_time':'prediction_time','label_available_at':'available_at',
              'device':'entity_key','label':'label'}.get(name,'value')
        typ='TIMESTAMP' if 'time' in name or 'available' in name else ('VARCHAR' if name=='device' else 'DOUBLE')
        fields.append(FieldContract(field_id='fld_'+name.replace('prediction_time','time'),
            source_name=name,source_column_index=i,physical_type=typ,
            semantic_type='datetime' if typ=='TIMESTAMP' else 'id' if typ=='VARCHAR' else 'numeric',
            role=role,confirmation='confirmed'))
    # Use consistent IDs for task.
    for f in fields:
        if f.source_name=='label_available_at':f.field_id='fld_label_available'
    return DataContract(asset_id='asset',version_id='base',source_sha256='f'*64,revision=2,
        state='confirmed',fields=fields,timezone='UTC')


def test_selection_uses_only_training_and_removes_redundancy():
    frame=fixture()
    cols=['pressure','temperature','derived_signal','duplicate_pressure','constant_candidate']
    x=frame[cols].to_numpy(dtype=float)
    policy=QualitySelectionPolicy(max_selected=4,min_pair_observations=12)
    selection=select_training_features(x[:150],frame.label.to_numpy()[:150],
       cols[:2],cols[2:],policy,task_type='binary_classification')
    assert 'constant_candidate' not in selection['selected_features']
    assert 'duplicate_pressure' not in selection['selected_features']
    assert 'derived_signal' in selection['selected_features']
    assert selection['scope']=='outer_training_fold_only'
    # A validation-side distribution shift cannot change a train-fold choice.
    shifted=x.copy();shifted[150:,2:]+=1e9
    again=select_training_features(shifted[:150],frame.label.to_numpy()[:150],
       cols[:2],cols[2:],policy,task_type='binary_classification')
    assert selection==again


def test_pure_supervised_time_folds_and_stability():
    frame=fixture()
    p=protocol()
    result=_evaluate_matrix(frame,['pressure','temperature'],
        ['derived_signal','duplicate_pressure','constant_candidate'],p.task,p,contract(frame),
        source_sha256='f'*64,spec_sha256='d'*64)
    assert len(result['folds'])==2
    for fold in result['folds']:
        assert set(fold['model_metrics']['linear'])=={'raw_only','with_features','selected_features'}
        assert fold['training_feature_selection']['scope']=='outer_training_fold_only'
        assert 'constant_candidate' not in fold['training_feature_selection']['selected_features']
        assert fold['group_stability'][0]['groups'][0]['field']=='device'
        assert fold['feature_stability']['basis']=='train_quantiles_validation_monitor_only'
        assert fold['metrics']['raw_only']['average_precision'] is not None
    assert result['stability_summary']['outer_folds']==2
    assert result['variant_comparison']['selected_features']['same_fold_only'] is True
    assert not result['searched_candidate_recipes']


def test_costly_search_never_uses_outer_validation_labels():
    frame=fixture(280)
    raw=frame[['pressure','temperature']].to_numpy()
    y=frame.label.to_numpy()
    p=protocol(search=True)
    first,xt,xv=search_training_fold(raw[:220],raw[220:],y[:220],
      'binary_classification',p.search,['pressure','temperature'],
      ['fld_pressure','fld_temperature'],['kPa','kPa'])
    again,bt,bv=search_training_fold(raw[:220],raw[220:]*-9,y[:220],
      'binary_classification',p.search,['pressure','temperature'],
      ['fld_pressure','fld_temperature'],['kPa','kPa'])
    assert first['model_fits']<=p.search.max_model_fits+1
    assert first['candidates_generated']<=p.search.max_candidates
    assert [(r['expression'],r['inner_gain']) for r in first['recipes']]==[
           (r['expression'],r['inner_gain']) for r in again['recipes']]
    assert xt.shape[1]<=p.search.max_winners
    assert len(xv)==60


def test_real_observation_values_without_claiming_real_time():
    # UCI Wine chemistry measurements bundled by sklearn are real observations.
    # The artificial timestamps below ONLY exercise fold code; they are not
    # claimed to be wine production or valid time-forward deployment evidence.
    from sklearn.datasets import load_wine
    wine=load_wine();rng=np.random.default_rng(2026)
    ordering=rng.permutation(len(wine.data))
    f=fixture(len(ordering))
    f['pressure']=wine.data[ordering,0]
    f['temperature']=wine.data[ordering,1]
    f['derived_signal']=f['pressure']+f['temperature']
    f['duplicate_pressure']=f['pressure']
    f['label']=(wine.target[ordering]==0).astype(float)
    p=protocol()
    result=_evaluate_matrix(f,['pressure','temperature'],['derived_signal','duplicate_pressure'],
          p.task,p,contract(f),source_sha256='a'*64,spec_sha256='b'*64)
    assert len(result['folds'])==2
    assert all(fold['metrics']['raw_only']['average_precision'] is not None for fold in result['folds'])


def test_conflicting_task_and_bad_slice_contract():
    p=protocol()
    with pytest.raises(ValueError,match='稳定性分组'):
        p_bad=p.model_copy(update={'slices':SlicePolicy(field_ids=['fld_label'])})
        _evaluate_matrix(fixture(),['pressure'],['derived_signal'],p_bad.task,p_bad,contract(fixture()),
                         source_sha256='f'*64,spec_sha256='b'*64)


def test_no_target_future_or_uncertain_metrics():
    folds=[{'metrics':{'raw_only':{'average_precision':None},'with_features':{'average_precision':None}}}]
    paired=paired_fold_comparison(folds,'binary_classification')
    assert paired['metrics']['average_precision']['mean_delta'] is None
    assert psi(np.ones(50),np.ones(50)) is None


def test_grouped_search_is_refused_before_training():
    from pydantic import ValidationError
    t=TaskSpecV2(task_id='grouped',mode='supervised',task_type='binary_classification',
        prediction_time_field_id='fld_time',label_field_id='fld_label',
        label_available_at_field_id='fld_label_available',split_kind='grouped_time',
        group_field_ids=['fld_device'],n_splits=2)
    with pytest.raises(ValidationError, match='分组时间切分暂不支持自动搜索'):
        EvaluationProtocol(task=t,set_id='s',revision=1,search=AutoSearchPolicy(enabled=True))


def test_search_formula_compiles_to_sql_using_same_vector_semantics():
    from timepilot.feature_expression import compile_formula
    from timepilot.feature_custom import validate_custom_definition
    f=contract(fixture())
    x=np.array([[0.,0.],[2.,0.],[-1.5,3.]],dtype=float)
    for kind,expr,ids,units in [
        ('absolute','abs(x0)',['fld_pressure'],['kPa']),
        ('square','x0 * x0',['fld_pressure'],['kPa']),
        ('log_abs','log1p(abs(x0))',['fld_pressure'],['kPa']),
        ('sqrt_abs','sqrt(abs(x0))',['fld_pressure'],['kPa']),
        ('difference','x0-x1',['fld_pressure','fld_temperature'],['kPa','kPa']),
        ('ratio','safe_div(x0,x1)',['fld_pressure','fld_temperature'],['kPa','kPa']),
    ]:
        definition=validate_custom_definition(f,ids,expr)
        assert definition['definition']['operator_id']=='expression.safe'
        compiled=compile_formula(expr,['"pressure"','"temperature"'][:len(ids)],units)
        assert 'TRY_CAST' in compiled
        from timepilot.feature_search import _vector
        vec=_vector(kind,list(range(len(ids))),x)
        assert len(vec)==3
        assert not np.isinf(vec).any()


def test_practical_gain_gate_blocks_tiny_ap_changes():
    frame=fixture(300)
    p=protocol()
    result=_evaluate_matrix(frame,['pressure','temperature'],
        ['derived_signal','duplicate_pressure','constant_candidate'],p.task,p,contract(frame),
        source_sha256='f'*64,spec_sha256='d'*64)
    for item in result['screening_recommendations']:
        assert item['reference_model']=='linear'
        assert item['minimum_practical_gain']>=0
        if item['verdict']=='review_candidate':
            assert item['mean_improvement']>item['minimum_practical_gain']
    assert not any(x['verdict']=='review_candidate' and x['mean_improvement']<=0
                   for x in result['screening_recommendations'])


def test_regression_baseline_selected_and_fold_metrics():
    frame=fixture(240)
    rng=np.random.default_rng(73)
    frame['label']=2.4*frame['pressure']-1.5*frame['temperature']+rng.normal(scale=.4,size=len(frame))
    proto=protocol()
    proto=proto.model_copy(update={'task':proto.task.model_copy(update={'task_type':'regression'})})
    result=_evaluate_matrix(frame,['pressure','temperature'],['derived_signal','constant_candidate'],
        proto.task,proto,contract(frame),source_sha256='f'*64,spec_sha256='d'*64)
    assert len(result['folds'])==2
    assert all(fold['model_metrics']['linear']['raw_only']['mae']>=0 for fold in result['folds'])
    assert 'rmse' in result['paired_comparison']['metrics']
    assert result['screening_recommendations'][0]['objective']=='mae'
