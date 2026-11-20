"""v0.37.0 industrial source, label-time, leakage and holdout gates."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from timepilot.industrial_dataset_v0370 import (load_nab, load_metropt, load_factory_csv,
                                                  write_normalized, METROPT_WINDOWS)
from timepilot.industrial_eval_v0370 import (_time_partition, _device_partition,
                                               _unsupervised_score, evaluate_real_industrial,
                                               validate_no_future_features)


def factory_fixture(tmp_path: Path, *, devices=6, periods=140):
    rng = np.random.default_rng(3700)
    records=[]
    base=pd.Timestamp('2024-03-01',tz='UTC')
    for d in range(devices):
        for j in range(periods):
            time=base+pd.Timedelta(minutes=5*j)
            sensor=float(rng.standard_normal()+0.5*np.sin(j/7))
            y=int(j%11 in (2,3,4) or sensor>1.15)
            records.append({'asset':f'asset-{d:02d}', 'when':time.isoformat(),
                'measurement_at':time.isoformat(), 'target':y,
                'event_at':time.isoformat() if y else '',
                'target_at':(time+pd.Timedelta(minutes=1)).isoformat(),
                'current_a':sensor,'current_b':float(rng.standard_normal()),
                'future_leak':1.0 if y else 0.0})
    csv=tmp_path/'factory.csv'
    pd.DataFrame(records).to_csv(csv,index=False)
    schema={
        'dataset_name':'fixture_verified_time', 'timezone':'UTC',
        'device_id':'asset', 'observation_time':'when',
        'measurement_available_at':'measurement_at', 'label':'target',
        'label_event_at':'event_at', 'label_available_at':'target_at',
        'sensors':['current_a','current_b'], 'label_task':'instant_quality',
    }
    conf=tmp_path/'schema.json'
    conf.write_text(json.dumps(schema),encoding='utf-8')
    return csv,conf


def nab_fixture(tmp_path: Path, *, devices=6, n=96):
    base = pd.Timestamp('2014-02-01 00:00:00')
    root = tmp_path/'NAB'
    (root/'labels').mkdir(parents=True)
    (root/'data'/'realAWSCloudwatch').mkdir(parents=True)
    marks={}
    for d in range(devices):
        name=f'ec2_cpu_utilization_{d:06x}.csv'
        rel='realAWSCloudwatch/'+name
        times=[base+pd.Timedelta(minutes=j*5) for j in range(n)]
        values=[float(0.1+((j+d)%7)/60.0+(0.9 if j in range(32,40) or j in range(65,72) or j in range(85,90) else 0)) for j in range(n)]
        pd.DataFrame({'timestamp':[t.strftime('%Y-%m-%d %H:%M:%S') for t in times], 'value':values}).to_csv(root/'data'/rel,index=False)
        marks[rel]=[(times[k].strftime('%Y-%m-%d %H:%M:%S'),times[k+6].strftime('%Y-%m-%d %H:%M:%S')) for k in (32,65,83)]
    (root/'labels'/'combined_windows.json').write_text(json.dumps(marks),encoding='utf-8')
    return root


def test_factory_contract_and_holdouts(tmp_path):
    csv,schema=factory_fixture(tmp_path)
    frame, provenance=load_factory_csv(csv,schema)
    assert provenance.rows==840 and provenance.devices==6
    assert provenance.label_available_at=='explicit_per_row_verified_by_input_contract'
    assert 'future_leak' not in frame
    assert not any(c in provenance.feature_columns for c in ('event_label','label_available_at','label_event_at'))
    proof=write_normalized(frame,provenance,tmp_path/'normalized')
    assert proof['label_availability_verified'] is True
    result=evaluate_real_industrial(frame,provenance)
    assert result['status']=='passed',result
    for name in ('cross_time','cross_device'):
        part=result['folds'][name]
        assert part['status']=='measured'
        assert part['train_and_test_disjoint']
        assert part['no_test_tuning']
        assert part['temporal_transfer_audit']['prospective_transfer_claim_allowed'] is False
        assert part['feature_value_comparison']['linear']['selection_uses_test'] is False
        assert part['training_uses_annotations']
        assert part['partitions']['test']['rows'] >= 15


def test_nab_retrospective_never_claims_pit(tmp_path):
    root=nab_fixture(tmp_path)
    frame,info=load_nab(root, verify_official=False)
    assert info.rows==576 and info.devices==6 and info.positives>0
    assert 'unknown' in info.label_available_at
    assert frame['label_available_at'].isna().all()
    outcome=evaluate_real_industrial(frame,info)
    assert outcome['point_in_time_label_availability_verified'] is False
    assert outcome['dataset_checks']['feature_receipt_verified'] is False
    assert '+00:00' not in outcome['folds']['cross_time']['partitions']['train']['start']
    assert outcome['production_prediction_claim_allowed'] is False
    assert outcome['assessment_type'].startswith('unsupervised')
    assert outcome['folds']['cross_time']['training_uses_annotations'] is False
    assert outcome['folds']['cross_device']['training_uses_annotations'] is False
    assert 'unsupervised_robust_z' in outcome['folds']['cross_time']['models']
    assert write_normalized(frame,info,tmp_path/'normalized')['validation_scope']=='retrospective_only'


def test_unknown_label_receipt_rejected_for_pit(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    frame,info=load_factory_csv(csv,conf)
    frame.loc[10,'label_available_at']=None
    with pytest.raises(ValueError,match='标签可用时间'):
        validate_no_future_features(frame,pit_labels=True)


def test_future_measurement_rejected(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    raw=pd.read_csv(csv)
    raw.loc[0,'measurement_at']='2025-01-01T00:00:00+00:00'
    raw.to_csv(csv,index=False)
    with pytest.raises(ValueError,match='未来特征泄漏'):
        load_factory_csv(csv,conf)


def test_label_receipt_precedes_event_rejected(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    raw=pd.read_csv(csv)
    i=int(np.flatnonzero(raw['target'].to_numpy())[0])
    raw.loc[i,'target_at']='2024-02-01T00:00:00+00:00'
    raw.to_csv(csv,index=False)
    with pytest.raises(ValueError,match='标签可用时间'):
        load_factory_csv(csv,conf)


def test_future_leak_label_as_sensor_rejected(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    cfg=json.loads(conf.read_text(encoding='utf-8'))
    cfg['sensors']=['current_a',cfg['label']]
    conf.write_text(json.dumps(cfg),encoding='utf-8')
    with pytest.raises(ValueError,match='白名单'):
        load_factory_csv(csv,conf)


def test_duplicated_observation_rejected(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    raw=pd.read_csv(csv)
    raw.loc[1,['when','measurement_at','target_at','event_at']]=raw.loc[0,['when','measurement_at','target_at','event_at']]
    raw.to_csv(csv,index=False)
    with pytest.raises(ValueError,match='重复'):
        load_factory_csv(csv,conf)


def test_no_cross_device_feature_contamination(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    frame,info=load_factory_csv(csv,conf)
    first=frame.groupby('device_id').head(1)
    assert first['sensor_value_lag1'].isna().all()
    assert first['sensor_value_past3_mean'].isna().all()
    # Perturb the future of a device; past features for every device stay fixed.
    dev=frame['device_id'].iloc[0]
    mask=(frame['device_id']==dev) & (frame['observation_time']>frame.loc[15,'observation_time'])
    # Directly test the declared, device-partitioned lag on the modified raw input.
    orig=frame[['device_id','observation_time','sensor_value']].copy()
    orig.loc[mask,'sensor_value']+=1000
    prior=orig.groupby('device_id',sort=False)['sensor_value'].shift(1)
    assert np.allclose(prior.loc[~mask].fillna(0), frame.loc[~mask,'sensor_value_lag1'].fillna(0))


def test_nab_missing_labels_fails_closed(tmp_path):
    root=nab_fixture(tmp_path)
    (root/'labels'/'combined_windows.json').unlink()
    with pytest.raises(FileNotFoundError,match='禁止自动编造'):
        load_nab(root, verify_official=False)


def test_nab_source_time_is_naive_not_false_utc(tmp_path):
    frame,info=load_nab(nab_fixture(tmp_path), verify_official=False)
    assert 'not asserted UTC' in info.timezone
    assert '+' not in frame['observation_time'].iloc[0]


def test_metropt_single_device_blocks_transfer(tmp_path):
    csv=tmp_path/'MetroPT3(AirCompressor).csv'
    t=pd.date_range('2020-04-17', periods=400, freq='h')
    pd.DataFrame({'timestamp':t.astype(str),'Motor_current':np.linspace(0,4,len(t)),
                  'TP2':np.ones(len(t)), 'Oil_temperature':np.arange(len(t))}).to_csv(csv,index=False)
    frame,info=load_metropt(csv)
    assert info.devices==1
    assert 'UCI' in info.license_notice
    report=evaluate_real_industrial(frame,info)
    assert report['folds']['cross_device']['status']=='blocked'
    assert '4台设备' in report['folds']['cross_device']['reason']


def test_robust_train_only_invariance():
    train=np.array([[0.0],[1.0],[0.5],[1.1],[2.0]])
    val=np.array([[0.9],[5.0]])
    score=_unsupervised_score(train,val)
    changed=_unsupervised_score(train,np.vstack([val,[[100000.0]]]))
    assert np.array_equal(score,changed[:2])


def test_label_availability_filters_late_training(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    frame,p=load_factory_csv(csv,conf)
    # Late label receipts are excluded rather than moved earlier.
    frame.loc[0,'label_available_at']='2025-12-01 00:00:00+00:00'
    result=evaluate_real_industrial(frame,p)
    assert result['folds']['cross_time']['removed_late_training_labels']>=1


def test_time_and_device_splits_deterministic(tmp_path):
    csv,conf=factory_fixture(tmp_path)
    frame,p=load_factory_csv(csv,conf)
    a=evaluate_real_industrial(frame,p)
    b=evaluate_real_industrial(frame,p)
    for split in ('cross_time','cross_device'):
        assert a['folds'][split]['partitions']==b['folds'][split]['partitions']
        assert a['folds'][split]['models']==b['folds'][split]['models']


def test_nab_imitation_refused_as_real(tmp_path):
    root=nab_fixture(tmp_path, devices=8)
    with pytest.raises(ValueError,match='NAB 官方'):
        load_nab(root)
