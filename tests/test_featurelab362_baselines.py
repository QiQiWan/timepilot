"""Frozen v0.36.2 synthetic baselines, independent QA oracles and tamper gates."""
from __future__ import annotations
import json
from pathlib import Path
import shutil

import pytest

from timepilot.featurelab361_fixtures import generate_suite, verify_suite
from timepilot.featurelab362_reference import (BASELINE_PATH, independent_reference,
                                               verify_frozen_baseline, compare_baseline)
from timepilot.featurelab362_model_matrix import evaluate_synthetic

MODEL_BASELINE = BASELINE_PATH.with_name('MODEL_BASELINE.json')

@pytest.fixture(scope='module')
def data(tmp_path_factory):
    folder = tmp_path_factory.mktemp('qa362-fixed')
    generate_suite(folder)
    return folder


def test_all_eight_datasets_match_independently_pinned_baseline(data):
    assert verify_suite(data)['status'] == 'passed'
    evidence = verify_frozen_baseline(data)
    assert evidence['status'] == 'passed', evidence['errors'][:3]
    assert evidence['observed']['quality_oracle'] == {
        'missing_temperature':24,'invalid_datetime':10,
        'out_of_range_temperature':8,'duplicate_sample_ids':12}
    assert evidence['observed']['industrial_oracle']['late_measurement_rows'] == 1
    assert evidence['observed']['industrial_oracle']['early_label_rows'] == 1
    assert evidence['observed']['industrial_oracle']['late_maintenance_count'] == 6


def test_frozen_baseline_independent_of_generated_manifest(data,tmp_path):
    alternate = tmp_path/'altered'
    shutil.copytree(data, alternate)
    path = alternate/'industrial_pit.csv'
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'EQ-01', b'EQ-07', 1))
    # A changed input and even a separately changed generated manifest must
    # never satisfy the checked-in, immutable baseline of expected values.
    assert verify_frozen_baseline(alternate)['status'] == 'failed'
    assert any('industrial_pit' in s for s in verify_frozen_baseline(alternate)['errors'])


def test_chart_oracle_full_period_and_entity_boundaries(data):
    reference = independent_reference(data)
    for device, points in reference['chart_oracle']['devices'].items():
        assert len(points) == 12
        assert sum(p['n'] for p in points) == 360
        assert points[-1]['last'] > points[0]['first']
        assert all(p['min'] <= p['mean'] <= p['max'] for p in points)
        assert max(p['max'] for p in points) > 35
    assert reference['industrial_oracle']['regime_runs']['EQ-01'] == ['A','B','A','C']


@pytest.mark.parametrize('scenario',('static_regression','static_binary','industrial_pit'))
def test_timepilot_real_fold_evaluator_remains_within_frozen_baseline(data,scenario):
    expected = json.loads(MODEL_BASELINE.read_text(encoding='utf-8'))['cases'][scenario]
    actual = evaluate_synthetic(data,scenario)
    assert actual['source_sha256'] == expected['source_sha256']
    assert actual['split_manifest_sha256'] == expected['split_manifest_sha256']
    assert actual['all_folds_disjoint'] and actual['fold_count']==3
    assert actual['train_selection_scopes']==['outer_training_fold_only']
    assert actual['label_availability_checked']==(scenario=='industrial_pit')
    assert actual['selection_frequencies']==expected['selection_frequencies']
    for variant in ('raw_only','with_features','selected_features'):
        # Numerical tolerance permits minor BLAS/sklearn changes but detects
        # material regressions. No baseline auto-update is allowed in tests.
        tolerance = 0.05 if scenario=='static_regression' else 0.005
        assert abs(actual['means'][variant]-expected['means'][variant]) <= tolerance, (scenario,variant,actual,expected)


def test_no_label_dataset_never_becomes_supervised(data):
    assert independent_reference(data)['unlabeled_oracle']['no_label_column']
    from timepilot.feature_insights import build_insight_brief
    from types import SimpleNamespace
    result = build_insight_brief(source_sha256='a'*64,contract_revision=1,
        data_analysis=None,session=None,delivery=None,
        contract=SimpleNamespace(fields=[]))
    assert not result['model'].get('comparisons')
    assert not result.get('predictive_value_evaluated',False)
