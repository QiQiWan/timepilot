"""Verify external-data acceptance preflight without claiming an actual plant run."""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path
import pandas as pd
import pytest

MODULE_PATH=Path(__file__).parents[1]/'scripts'/'featurelab30_e2e.py'
spec=importlib.util.spec_from_file_location('tp30_acceptance',MODULE_PATH)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def args():
    return argparse.Namespace(event_time='event_time',available_at='available_at',
      prediction_time='prediction_time',label_available='label_available_at',
      target='quality_label',device='device_id',regime='operating_regime',
      measure_a='pressure_a',measure_b='pressure_b',timezone='UTC',
      rows_limit=200000,task='binary_classification')


def sample(n=180):
    clock=pd.date_range('2025-02-01',periods=n,freq='h',tz='UTC')
    return pd.DataFrame({'event_time':clock-pd.Timedelta(minutes=10),
       'available_at':clock-pd.Timedelta(minutes=5),
       'prediction_time':clock,'label_available_at':clock+pd.Timedelta(hours=1),
       'quality_label':[i%2 for i in range(n)],'device_id':[f'D{i%3}' for i in range(n)],
       'operating_regime':['idle' if i%4==0 else 'running' for i in range(n)],
       'pressure_a':[float(i%50) for i in range(n)],
       'pressure_b':[float(i%30) for i in range(n)]})


def test_valid_external_layout_accepts_real_timestamps_and_two_slices():
    f=module.verify_input(sample(),args())
    assert len(f)==180
    assert f['prediction_time'].dt.tz is not None
    assert f['device_id'].nunique()==3 and f['operating_regime'].nunique()==2


def test_late_measurement_is_blocked_before_any_model():
    f=sample();f.loc[7,'available_at']=f.loc[7,'prediction_time']+pd.Timedelta(minutes=1)
    with pytest.raises(ValueError,match='PIT'):
        module.verify_input(f,args())


def test_future_event_is_blocked():
    f=sample();f.loc[3,'event_time']=f.loc[3,'prediction_time']+pd.Timedelta(minutes=1)
    with pytest.raises(ValueError,match='PIT'):
        module.verify_input(f,args())


def test_absent_regime_is_explicitly_blocked():
    f=sample();f['operating_regime']='constant'
    with pytest.raises(ValueError,match='工况'):
        module.verify_input(f,args())


def test_future_label_and_bad_target_contract():
    f=sample();f.loc[3,'quality_label']=4
    with pytest.raises(ValueError,match='二分类'):
        module.verify_input(f,args())
    f=sample();f.loc[3,'label_available_at']=f.loc[3,'prediction_time']-pd.Timedelta(seconds=1)
    with pytest.raises(ValueError,match='标签'):
        module.verify_input(f,args())


def test_duplicate_field_mapping_is_rejected():
    a=args();a.measure_b=a.measure_a
    with pytest.raises(ValueError,match='重复'):
        module.verify_input(sample(),a)


def test_evaluator_must_prove_exact_materialized_bytes():
    base={'source_sha256':'a'*64,'spec_sha256':'b'*64,
      'evaluated_artifact_id':'id-01','evaluated_artifact_sha256':'c'*64,
      'evaluated_contract_revision':5,'folds':[{}, {}, {}]}
    inputs={'source_sha':'a'*64,'spec_sha':'b'*64,'artifact_id':'id-01',
            'artifact_sha':'c'*64,'revision':5}
    assert module.assert_same_artifact_evidence(base,**inputs)
    with pytest.raises(ValueError,match='evaluated_artifact_sha256'):
        module.assert_same_artifact_evidence({**base,'evaluated_artifact_sha256':'d'*64},**inputs)
    with pytest.raises(ValueError,match='folds'):
        module.assert_same_artifact_evidence({**base,'folds':[{}]},**inputs)
