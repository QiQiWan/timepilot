"""Measured performance release budget and hardware-matched comparison policy."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from featurelab370_performance import enforce_budgets


def report():
    return {
        'status':'passed', 'dataset_type':'synthetic',
        'host': {'python':'3.12.12','platform':'Linux-generic','cpus_logical':8},
        'run_settings':{'seed':3700},
        'scales':{
            '10000': {'summary':{
                'max_peak_rss_bytes':200*1024**2,
                'stages':{'read_csv':{'median_seconds':0.2},
                          'cross_time_and_device_fit':{'median_seconds':2.0}}
            }}
        }
    }


def test_performance_release_gate_within_budget():
    r = enforce_budgets(report(), max_peak_rss_mib=400, max_model_seconds=10)
    assert r['status']=='passed'
    assert not r['performance_budgets']['failures']


def test_performance_release_gate_fails_peak_memory():
    r=enforce_budgets(report(),max_peak_rss_mib=100)
    assert r['status']=='failed'
    assert 'RSS' in r['performance_budgets']['failures'][0]


def test_performance_release_gate_fails_model_time():
    r=enforce_budgets(report(),max_model_seconds=1.5)
    assert r['status']=='failed'


def test_comparison_only_on_identical_technical_environment():
    base=report()
    new=report();new['host']['python']='3.13.5'
    r=enforce_budgets(new,baseline=base)
    assert r['status']=='blocked'
    assert '机器/Python 不同' in r['performance_budgets']['blocked'][0]


def test_same_environment_degradation_detected():
    old=report();new=copy.deepcopy(old)
    new['scales']['10000']['summary']['stages']['cross_time_and_device_fit']['median_seconds']=4.2
    r=enforce_budgets(new,baseline=old,max_regression_factor=1.6)
    assert r['status']=='failed'
    assert r['performance_budgets']['ratios']['10000']['cross_time_and_device_fit']==2.1


def test_identical_report_comparison_is_permitted():
    old=report();new=copy.deepcopy(old)
    r=enforce_budgets(new,baseline=old)
    assert r['status']=='passed'
    assert r['performance_budgets']['comparison_same_environment']


def test_incomplete_historical_baseline_is_blocked():
    old=report();old['scales']={}
    r=enforce_budgets(report(),baseline=old)
    assert r['status']=='blocked'


def test_invalid_budgets_are_rejected():
    with pytest.raises(ValueError):
        enforce_budgets(report(),max_regression_factor=0.4)
    with pytest.raises(ValueError):
        enforce_budgets(report(),max_peak_rss_mib=-1)


def test_partial_real_worker_is_not_labeled_crash(tmp_path):
    import json
    import subprocess
    out = tmp_path / 'measured.json'
    run = subprocess.run([sys.executable, str(ROOT/'scripts/featurelab370_performance.py'),
        '--rows','1000','--repeats','1','--out',str(out)], capture_output=True,
        text=True, timeout=60)
    assert run.returncode == 2, run.stdout + run.stderr
    result = json.loads(out.read_text(encoding='utf-8'))
    assert result['status'] == 'partial'
    assert result['scales']['1000']['runs'][0]['model_evaluation_status'] == 'partial'
    assert result['scales']['1000']['runs'][0]['cross_device_status'] == 'blocked'
