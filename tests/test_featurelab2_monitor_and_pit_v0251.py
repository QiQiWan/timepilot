"""FeatureLab v2 temporal, monitor, schema and job-recovery contract tests.

No mock query is reported as a DuckDB numerical acceptance test.
"""
from __future__ import annotations
from uuid import uuid4
import numpy as np
import pytest
from timepilot.feature_monitor import compare_profiles
from timepilot.feature_selection import train_fold_diagnostics
from timepilot.feature_contracts import (DataContract,FeatureSpecV2,FeatureDefinition,
                                         SourceRef,TaskSpecV2)
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_temporal_profile import temporal_diagnostics
from test_conversational_bi_v0231 import _env


def profile(sha,fields,*,scope='full'):
    return {'asset_id':'asset-1','source_sha256':sha,'scope':scope,'rows_scanned':100,
            'fields':fields}


def test_monitor_comparison_has_exact_scope_and_source():
    left=profile('a'*64,[{'field_id':'fld_a','source_name':'A1','null_rate':0.01,
                           'mean':10.0,'std':2.0}])
    right=profile('b'*64,[{'field_id':'fld_b','source_name':'A1','null_rate':0.1,
                           'mean':20.0,'std':2.0}])
    result=compare_profiles(left,right)
    assert result['scope']=='full' and result['warning_count']==2
    assert {x['kind'] for x in result['warnings']}=={'missing_rate_shift','mean_shift_descriptive'}
    with pytest.raises(ValueError,match='全量'):
        compare_profiles(left,{**right,'scope':'sample'})
    with pytest.raises(ValueError,match='相同'):
        compare_profiles(left,{**right,'asset_id':'other'})


def test_feature_diagnostics_uses_training_rows_only():
    train=np.array([[1,2,0],[2,4,0],[3,6,0],[4,8,0],[5,10,0]],dtype=float)
    result=train_fold_diagnostics(train,['x','x_copy','constant'])
    assert result['scope']=='training_fold_only'
    assert result['kept_fields']==['x']
    assert 'constant' in result['constant_or_invalid']
    assert result['redundant'][0]['kept_field']=='x'
    assert not result['selection_applied_to_model']


def test_event_and_business_availability_are_separate_times():
    fields=[
        dict(field_id='fld_eq',source_name='device',source_column_index=0,physical_type='VARCHAR',
             semantic_type='id',role='entity_key',confirmation='confirmed'),
        dict(field_id='fld_event',source_name='event_at',source_column_index=1,physical_type='TIMESTAMP',
             semantic_type='datetime',role='event_time',confirmation='confirmed'),
        dict(field_id='fld_available',source_name='known_at',source_column_index=2,physical_type='TIMESTAMP',
             semantic_type='datetime',role='available_at',confirmation='confirmed'),
        dict(field_id='fld_decision',source_name='decide_at',source_column_index=3,physical_type='TIMESTAMP',
             semantic_type='datetime',role='prediction_time',confirmation='confirmed'),
        dict(field_id='fld_label',source_name='target',source_column_index=4,physical_type='DOUBLE',
             semantic_type='numeric',role='label',confirmation='confirmed'),
        dict(field_id='fld_label_available',source_name='target_known_at',source_column_index=5,
             physical_type='TIMESTAMP',semantic_type='datetime',role='available_at',confirmation='confirmed'),
        dict(field_id='fld_value',source_name='A1',source_column_index=6,physical_type='DOUBLE',
             semantic_type='numeric',role='value',confirmation='confirmed')]
    contract=DataContract(asset_id='asset-1',version_id='base',source_sha256='3'*64,
        revision=2,state='confirmed',fields=fields,entity_field_ids=['fld_eq'],
        event_time_field_id='fld_event',available_at_field_id='fld_available',timezone='Asia/Shanghai')
    task=TaskSpecV2(task_id='prediction',mode='supervised',task_type='regression',
        entity_field_ids=['fld_eq'],prediction_time_field_id='fld_decision',
        label_field_id='fld_label',label_available_at_field_id='fld_label_available')
    spec=FeatureSpecV2(name='causal',source=SourceRef(asset_id='asset-1',version_id='base',source_sha256='3'*64),
        contract_revision=2,task=task,features=[FeatureDefinition(feature_id='avg',output_name='avg',
            operator_id='rolling.mean',input_field_ids=['fld_value'],
            parameters={'window':3,'order_field_id':'fld_event'})])
    compiler=FeatureCompiler(contract,spec)
    class NoInvalid:
        def execute(self,sql):
            self.sql=sql
            assert ('"known_at" > "decide_at"' in sql or
                    'latest_source_available > decision_at' in sql)
            assert '"known_at" > "event_at"' not in sql
            return self
        def fetchone(self):return (0,)
    assert compiler.validate_point_in_time(NoInvalid())['invalid_rows']==0
    class HasLate(NoInvalid):
        def fetchone(self):return (2,)
    with pytest.raises(ValueError,match='2 条'):
        compiler.validate_point_in_time(HasLate())
    class HistoricallyLate(NoInvalid):
        def __init__(self):self.calls=0
        def fetchone(self):
            self.calls+=1
            return (0,) if self.calls==1 else (2,)
    with pytest.raises(ValueError,match='窗口包含 2 条'):
        compiler.validate_point_in_time(HistoricallyLate())


def test_temporal_profile_is_bounded_by_confirmed_semantics():
    fields=[dict(field_id='fld_1',source_name='Time',source_column_index=0,physical_type='BIGINT')]
    contract=DataContract(asset_id='a',version_id='base',source_sha256='4'*64,fields=fields)
    assert temporal_diagnostics(None,contract)['status']=='needs_review'


def test_jobs_and_profiles_restorable_in_project(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        resp=c.post(base+'/profile-jobs',json={'asset_id':aid,'version_id':'base','request_id':'profile-once'})
        assert resp.status_code==202,resp.text
        job_id=resp.json()['job_id']
        records=c.get(base+'/profile-jobs',params={'asset_id':aid}).json()['items']
        assert any(x['job_id']==job_id for x in records)
        assert c.get(base+'/profiles',params={'asset_id':aid}).json()['items']==[]
        assert c.post(base+'/profile-jobs',json={'asset_id':aid,'request_id':'profile-once'}).json()['job_id']==job_id
        assert c.get(f'/api/v1/projects/{uuid4()}/feature-lab/profile-jobs',params={'asset_id':aid}).status_code in (403,404)
        with app.state.projects._db() as db:
            version=db.execute("SELECT version FROM project_schema ORDER BY version DESC LIMIT 1").fetchone()[0]
            from timepilot.project_migrations import TARGET_SCHEMA
            assert version==TARGET_SCHEMA
            assert db.execute('SELECT COUNT(*) FROM feature_monitor_snapshots').fetchone()[0]==0
    finally:c.__exit__(None,None,None)
