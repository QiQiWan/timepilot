"""v0.35.2: heldout validation, label perturbation, compatibility and safety."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from timepilot.feature_contracts import FieldContract,DataContract,TaskSpecV2,EvaluationProtocol,digest
from timepilot.feature_industrial_transfer import IndustrialTransferPolicy,evaluate_industrial_transfer


def fixture():
    rng=np.random.default_rng(352)
    groups=np.repeat(['A','B','C','D'],90)
    n=len(groups)
    x=rng.normal(size=n);feat=x*x
    y=5*feat+0.1*rng.normal(size=n)
    frame=pd.DataFrame({'device':groups,'raw':x,'quadratic':feat,'target':y})
    fields=[FieldContract(field_id='fld_'+col,source_name=col,source_column_index=i,
             physical_type='VARCHAR' if col=='device' else 'DOUBLE',
             semantic_type='id' if col=='device' else 'numeric',
             role='entity_key' if col=='device' else 'label' if col=='target' else 'value',
             confirmation='confirmed') for i,col in enumerate(frame)]
    contract=DataContract(asset_id='fixed',version_id='base',source_sha256='f'*64,
          revision=2,state='confirmed',fields=fields,entity_field_ids=['fld_device'])
    task=TaskSpecV2(task_id='heldout',mode='supervised',task_type='regression',
          label_field_id='fld_target',entity_field_ids=['fld_device'],group_field_ids=['fld_device'],
          split_kind='group',static_independence_confirmed=True)
    policy=IndustrialTransferPolicy(kind='unseen_device',group_field_id='fld_device',holdout_values=['D'])
    proto=EvaluationProtocol(task=task,set_id='fixed',revision=1,models=['linear'],industrial_transfer=policy)
    matrix=frame[['raw','quadratic']].to_numpy(dtype=float)
    return frame,matrix,y,contract,task,proto


def evaluate(frame,matrix,y,c,t,p):
    return evaluate_industrial_transfer(frame,matrix,y,['raw'],['quadratic'],c,t,p,
       source_sha256=c.source_sha256,spec_sha256='s'*64)


def test_unseen_device_is_group_disjoint_and_reproducible():
    f,x,y,c,t,p=fixture()
    a=evaluate(f,x,y,c,t,p);b=evaluate(f,x,y,c,t,p)
    assert a==b and a['status']=='measured'
    assert a['train_rows']==270 and a['holdout_rows']==90
    assert a['train_holdout_disjoint'] is True
    assert a['scope']=='group_disjoint_only'
    assert a['model_metrics']['linear']['with_features']['mae']<a['model_metrics']['linear']['raw_only']['mae']


def test_final_holdout_label_changes_do_not_change_selected_train_features():
    f,x,y,c,t,p=fixture();a=evaluate(f,x,y,c,t,p)
    y2=y.copy();y2[270:]+=100
    b=evaluate(f,x,y2,c,t,p)
    assert a['selected_features']==b['selected_features']
    assert a['train_rows_sha256']==b['train_rows_sha256']
    assert a['manifest_sha256']==b['manifest_sha256']
    assert a['model_metrics']['linear']['with_features']['mae']!=b['model_metrics']['linear']['with_features']['mae']


def test_holdout_policy_rejects_unconfirmed_group_and_wrong_category():
    f,x,y,c,t,p=fixture()
    with pytest.raises(ValueError,match='不匹配'):
        wrong=p.model_copy(update={'industrial_transfer':p.industrial_transfer.model_copy(update={'kind':'unseen_regime'})})
        evaluate(f,x,y,c,t,wrong)
    with pytest.raises(ValueError,match='不足|不存在'):
        wrong=p.model_copy(update={'industrial_transfer':p.industrial_transfer.model_copy(update={'holdout_values':['NO_SUCH_DEVICE']})})
        evaluate(f,x,y,c,t,wrong)
    with pytest.raises(ValidationError,match='留出组重复'):
        IndustrialTransferPolicy(kind='unseen_device',group_field_id='fld_device',holdout_values=['D','D'])


def test_transfer_protocol_is_backward_serialization_compatible_and_disallows_search():
    f,x,y,c,t,p=fixture()
    prev=p.model_copy(update={'industrial_transfer':None})
    assert 'industrial_transfer' not in prev.model_dump(mode='json')
    assert 'industrial_transfer' in p.model_dump(mode='json')
    from timepilot.feature_contracts import AutoSearchPolicy
    with pytest.raises(ValidationError,match='留出评价禁止'):
        EvaluationProtocol(task=t,set_id='fixed',revision=1,industrial_transfer=p.industrial_transfer,
                           search=AutoSearchPolicy(enabled=True))


def test_temporal_transfer_without_cutoff_fails_closed():
    f,x,y,c,t,p=fixture()
    timed=t.model_copy(update={'split_kind':'time','prediction_time_field_id':'fld_decision_at',
        'label_available_at_field_id':'fld_label_at','static_independence_confirmed':False,
        'group_field_ids':[]})
    with pytest.raises(ValueError,match='时间截断'):
        evaluate(f,x,y,c,timed,p)


def test_outer_cv_excludes_all_final_holdout_rows_and_labels():
    from timepilot.feature_evaluation import _evaluate_matrix
    f,x,y,c,t,p=fixture()
    first=_evaluate_matrix(f,['raw'],['quadratic'],t,p,c,
        source_sha256=c.source_sha256,spec_sha256='s'*64)
    assert first['industrial_transfer_holdout']['train_holdout_disjoint'] is True
    assert first['industrial_transfer_holdout']['holdout_rows']==90
    # All K-fold rows now live inside the 270-row training pool.
    assert all(row['train_count']+row['valid_count']<=270 for row in first['folds'])
    assert all('D' not in f.iloc[fold_idx]['device'].unique() for fold_idx in
        [np.flatnonzero(f.device!='D')])
    changed=f.copy();changed.loc[changed.device=='D','target']+=5000
    second=_evaluate_matrix(changed,['raw'],['quadratic'],t,p,c,
        source_sha256=c.source_sha256,spec_sha256='s'*64)
    assert first['fold_manifest']==second['fold_manifest']
    assert first['screening_recommendations']==second['screening_recommendations']
    assert first['selection_frequency']==second['selection_frequency']
    assert first['paired_comparison']==second['paired_comparison']
    assert first['industrial_transfer_holdout']['model_metrics']!=second['industrial_transfer_holdout']['model_metrics']


def test_true_temporal_transfer_respects_both_unseen_device_and_future_cutoff():
    from timepilot.feature_evaluation import _evaluate_matrix
    f,x,y,c,t,p=fixture()
    n=len(f);f=f.copy()
    offsets=np.tile(np.arange(90),4)
    f['event_at']=pd.Timestamp('2026-01-01',tz='UTC')+pd.to_timedelta(offsets,unit='minutes')
    f['available_at']=f['event_at']
    f['decision_at']=f['event_at']
    f['label_at']=f['event_at']+pd.Timedelta(minutes=1)
    tf=list(c.fields)
    for col in ('event_at','available_at','decision_at','label_at'):
        role='event_time' if col=='event_at' else 'prediction_time' if col=='decision_at' else 'available_at'
        tf.append(FieldContract(field_id='fld_'+col,source_name=col,
            source_column_index=len(tf),physical_type='TIMESTAMP',semantic_type='datetime',role=role,
            confirmation='confirmed',timezone='UTC'))
    c2=c.model_copy(update={'fields':tf,'event_time_field_id':'fld_event_at',
         'available_at_field_id':'fld_available_at','timezone':'UTC'})
    t2=TaskSpecV2(task_id='temporal-transfer',mode='supervised',task_type='regression',
        label_field_id='fld_target',split_kind='time',entity_field_ids=['fld_device'],
        prediction_time_field_id='fld_decision_at',label_available_at_field_id='fld_label_at',n_splits=3)
    policy=IndustrialTransferPolicy(kind='unseen_device',group_field_id='fld_device',
        holdout_values=['D'],time_cutoff='2026-01-01T01:10:00Z')
    p2=EvaluationProtocol(task=t2,set_id='fixed',revision=1,industrial_transfer=policy)
    from timepilot.feature_industrial_transfer import transfer_partition
    a,b,cutoff=transfer_partition(f,c2,t2,policy)
    assert (f.device.iloc[a]!='D').all() and (f.device.iloc[b]=='D').all()
    assert (f.decision_at.iloc[a]<cutoff).all() and (f.decision_at.iloc[b]>=cutoff).all()
    assert (f.label_at.iloc[a]<=cutoff).all()
    report=_evaluate_matrix(f,['raw'],['quadratic'],t2,p2,c2,
        source_sha256=c2.source_sha256,spec_sha256='s'*64)
    assert report['industrial_transfer_holdout']['scope']=='group_and_future_time'
    assert report['industrial_transfer_holdout']['train_rows']>100
    assert all(z['train_count']<len(a) and z['valid_count']<len(a) for z in report['folds'])
