from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pandas as pd
import numpy as np
import pytest
from timepilot.feature_split_protocol import build_folds
from timepilot.feature_evaluation import _prepare,_predict,_metrics
from timepilot.feature_contracts import (DataContract,SourceRef,FeatureDefinition,
                                         FeatureSpecV2,TaskSpecV2)
from timepilot.feature_compiler import FeatureCompiler


def test_future_leakage_precheck_compiler():
    raw=[('eq','device_id','VARCHAR','id','entity_key'),('when','event_time','TIMESTAMP','datetime','event_time'),
         ('known','available_at','TIMESTAMP','datetime','available_at'),
         ('value','A2','DOUBLE','numeric','value'),('target','label','DOUBLE','numeric','label'),
         ('label_time','label_available_at','TIMESTAMP','datetime','available_at')]
    fields=[{'field_id':'fld_'+fid,'source_name':name,'source_column_index':i,
             'physical_type':typ,'semantic_type':sem,'role':role,'confirmation':'confirmed'}
            for i,(fid,name,typ,sem,role) in enumerate(raw)]
    contract=DataContract(asset_id='a',version_id='base',source_sha256='2'*64,
         state='confirmed',revision=2,fields=fields,entity_field_ids=['fld_eq'],
         event_time_field_id='fld_when',available_at_field_id='fld_known',timezone='Asia/Shanghai')
    task=TaskSpecV2(task_id='t',mode='supervised',task_type='binary_classification',
          entity_field_ids=['fld_eq'],prediction_time_field_id='fld_when',
          label_field_id='fld_target',label_available_at_field_id='fld_label_time')
    feat=FeatureDefinition(feature_id='feat1',output_name='ma',operator_id='rolling.mean',
             input_field_ids=['fld_value'],parameters={'window':3,'order_field_id':'fld_when'})
    spec=FeatureSpecV2(name='test',source=SourceRef(asset_id='a',version_id='base',source_sha256='2'*64),
                       contract_revision=2,task=task,features=[feat])
    compiler=FeatureCompiler(contract,spec)
    sql=compiler.expression(feat)
    assert 'PARTITION BY "device_id"' in sql
    assert '"event_time"' in sql
    class Conn:
        def execute(self,sql):
            assert '"available_at" > "event_time"' in sql
            return self
        def fetchone(self):return (1,)
    with pytest.raises(ValueError,match='1 条'):compiler.validate_point_in_time(Conn())
    bad=spec.model_copy(update={'features':[feat.model_copy(update={'input_field_ids':['fld_target']})]})
    with pytest.raises(ValueError,match='目标标签'):FeatureCompiler(contract,bad)


def test_time_and_group_splits_are_non_overlapping():
    t=pd.date_range('2026-01-01',periods=120,freq='h',tz='UTC')
    df=pd.DataFrame({'time':t,'device':['A' if i<40 else 'B' if i<80 else 'C' for i in range(120)]})
    splits,manifest=build_folds(df,time_column='time',n_splits=3)
    assert len(splits)==3
    for train,val in splits:
        assert df.time.iloc[train].max()<df.time.iloc[val].min()
    with pytest.raises(ValueError):build_folds(df,time_column='time',n_splits=3,group_columns=['device'])
    # Four disjoint groups can be used if their chronological spans are disjoint.
    df['device']=[str(i//30) for i in range(120)]
    splits,_=build_folds(df,time_column='time',n_splits=3,group_columns=['device'])
    for train,val in splits:
        assert set(df.device.iloc[train]).isdisjoint(set(df.device.iloc[val]))


def test_training_only_baselines_are_deterministic():
    train=np.array([[1.],[2.],[3.],[np.nan],[5.],[6.],[7.],[8.]])
    test=np.array([[np.nan],[1000.]])
    x,y=_prepare(train,test)
    assert np.isfinite(x).all() and np.isfinite(y).all()
    assert abs(np.mean(x))<1e-8
    ytrain=np.array([0.,0.,0.,0.,1.,1.,1.,1.])
    result=_predict(x,ytrain,y,'binary_classification')
    assert 0<=min(result)<=max(result)<=1
    assert np.allclose(result,_predict(x,ytrain,y,'binary_classification'))
    score=_metrics(np.array([0.,1.]),np.array([0.1,0.9]),'binary_classification')
    assert score['recall']==1 and score['false_reject_rate']==0
