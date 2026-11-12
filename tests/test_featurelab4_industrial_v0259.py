"""v0.25.9: source-scoped, typed industrial maintenance features and target labels."""
from __future__ import annotations
import pytest
import numpy as np
from timepilot.feature_contracts import DataContract,FeatureSpecV2,FeatureDefinition,SourceRef,TaskSpecV2
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_operator_registry import discover
from timepilot.feature_label_contract import validate_target
from timepilot.feature_split_protocol import build_folds
import pandas as pd


def data_contract(flag_type='BOOLEAN'):
    cols=[('device','VARCHAR','id','entity_key'),('event_at','TIMESTAMP','datetime','event_time'),
          ('available_at','TIMESTAMP','datetime','available_at'),
          ('prediction_at','TIMESTAMP','datetime','prediction_time'),
          ('maintenance_flag',flag_type,'event' if flag_type=='BOOLEAN' else 'numeric','value'),
          ('temp','DOUBLE','numeric','value'),('target','DOUBLE','numeric','label'),
          ('label_available','TIMESTAMP','datetime','available_at')]
    return DataContract(asset_id='source-1',version_id='base',source_sha256='1'*64,
       revision=2,state='confirmed',timezone='Asia/Shanghai',
       entity_field_ids=['fld_device'],event_time_field_id='fld_event_at',
       available_at_field_id='fld_available_at',
       fields=[{'field_id':'fld_'+k,'source_name':k,'source_column_index':i,
              'physical_type':typ,'semantic_type':sem,'role':role,'confirmation':'confirmed'}
              for i,(k,typ,sem,role) in enumerate(cols)])


def spec_for(contract,supervised=False):
    task=(TaskSpecV2(task_id='quality',mode='supervised',task_type='binary_classification',
          entity_field_ids=['fld_device'],prediction_time_field_id='fld_prediction_at',
          label_field_id='fld_target',label_available_at_field_id='fld_label_available')
          if supervised else None)
    return FeatureSpecV2(name='维护特征',source=SourceRef(asset_id=contract.asset_id,
        version_id=contract.version_id,source_sha256=contract.source_sha256),
        contract_revision=2,task=task,features=[FeatureDefinition(feature_id='since-maint',
        output_name='seconds_since_maintenance',operator_id='event.since',
        input_field_ids=['fld_maintenance_flag'],
        parameters={'order_field_id':'fld_event_at'})])


def test_maintenance_sql_uses_confirmed_boolean_and_partitioned_event_time():
    c=data_contract()
    compiled=FeatureCompiler(c,spec_for(c))
    sql=compiled.expression(compiled.order[0]);assert 'DATE_DIFF' in sql
    assert 'PARTITION BY "device"' in sql and 'ORDER BY "event_at"' in sql
    assert 'CASE WHEN "maintenance_flag" IS TRUE' in sql
    assert 'UNBOUNDED PRECEDING' in sql
    available=[o for o in discover(physical_types=['DOUBLE','BOOLEAN','TIMESTAMP'],has_time=True)
       if o['operator_id']=='event.since']
    assert available[0]['available']
    absent=[o for o in discover(physical_types=['DOUBLE','TIMESTAMP'],has_time=True)
       if o['operator_id']=='event.since']
    assert not absent[0]['available']
    with pytest.raises(ValueError,match='BOOLEAN'):
        FeatureCompiler(data_contract('DOUBLE'),spec_for(data_contract('DOUBLE')))
    c2=data_contract()
    data=spec_for(c2).model_dump(mode='json')
    data['features'][0]['parameters']['order_field_id']='fld_missing'
    with pytest.raises(ValueError,match='必须引用源字段'):
        FeatureCompiler(c2,FeatureSpecV2.model_validate(data))


def test_binary_label_domain_and_group_time_split():
    for invalid in ([0,2],[-1,1],[0,np.nan],['unverified','0']):
        with pytest.raises(ValueError,match='标签'):
            validate_target(invalid,'binary_classification')
    good,meta=validate_target([0,0,1,1],'binary_classification')
    assert meta['class_counts']=={'0':2,'1':2}
    # Complete time groups in each fold, no duplicate time crossing.
    timeline=pd.date_range('2026-10-01',periods=40,freq='h',tz='UTC')
    data=pd.DataFrame({'prediction_at':timeline,'device':['A']*40})
    folds,manifest=build_folds(data,time_column='prediction_at',n_splits=3)
    assert len(folds)==3
    for train,valid in folds:
        assert set(train).isdisjoint(set(valid))
        assert timeline[train].max()<timeline[valid].min()
    assert all('train_rows_sha256' in item for item in manifest)
