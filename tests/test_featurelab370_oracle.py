"""Tie-stable AP, independent-source baseline, and strict official-data release gates."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from timepilot.industrial_eval_v0370 import _metrics_or_none, evaluate_real_industrial
from timepilot.industrial_oracle_v0370 import verify_oracle, validate_independent_oracle
from timepilot.industrial_dataset_v0370 import load_nab
from test_featurelab370_industrial import nab_fixture

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'benchmarks/featurelab_v0370/NAB_INDEPENDENT_ORACLE.json'
PIN = ROOT / 'benchmarks/featurelab_v0370/PUBLIC_NAB_PIN.json'


def test_ap_is_invariant_under_equal_score_reordering():
    scores = np.array([0.3, 0.3, 0.3, 0.8, 0.8, 0.8, 0.1, 0.1])
    y = np.array([0, 1, 1, 1, 0, 1, 0, 1])
    a = _metrics_or_none(y, scores)
    reverse = np.arange(len(y))[::-1]
    b = _metrics_or_none(y[reverse], scores[reverse])
    assert a['average_precision'] == pytest.approx(average_precision_score(y, scores))
    assert a['average_precision'] == pytest.approx(b['average_precision'], abs=1e-15)
    assert a['roc_auc'] == pytest.approx(b['roc_auc'], abs=1e-15)
    assert 'tied thresholds grouped' in a['ap_tie_policy']


def test_ap_does_not_fabricate_missing_class():
    result = _metrics_or_none(np.zeros(30), np.full(30, 0.15))
    assert result['sufficient_classes_for_ranking'] is False
    assert result['average_precision'] is None
    assert result['roc_auc'] is None


def test_official_oracle_matches_pinned_upstream_file_inventory():
    oracle = json.loads(REFERENCE.read_text(encoding='utf-8'))
    pin = json.loads(PIN.read_text(encoding='utf-8'))
    assert oracle['git_object_pins'] == pin['git_blob_sha1']
    assert oracle['devices'] == 8 and oracle['rows'] == 32_256
    assert sum(x['rows'] for x in oracle['device_source_stats'].values()) == oracle['rows']
    assert sum(x['window_marked_records'] for x in oracle['device_source_stats'].values()) == oracle['total_window_marked_records'] == 2760
    assert oracle['predeclared_holdout_devices'] == pin['predeclared_holdout_devices']


def test_public_synthetic_fixture_not_valid_as_official_oracle(tmp_path):
    fake = nab_fixture(tmp_path, devices=8, n=96)
    with pytest.raises(ValueError, match='NAB 官方文件'):
        load_nab(fake, verify_official=True)
    with pytest.raises(ValueError, match='NAB 官方文件'):
        verify_oracle(tmp_path, REFERENCE, raw_root=fake)


def test_cross_device_retrospective_transfers_cannot_claim_prospective(tmp_path):
    fake = nab_fixture(tmp_path, devices=6, n=96)
    frame, provenance = load_nab(fake, verify_official=False)
    outcome = evaluate_real_industrial(frame, provenance)
    audit = outcome['folds']['cross_device']['temporal_transfer_audit']
    assert audit['prospective_transfer_claim_allowed'] is False
    assert audit['training_precedes_both_holdouts'] in (True, False)
    assert audit['train_end'] and audit['test_start']
    assert outcome['point_in_time_label_availability_verified'] is False
    assert outcome['folds']['cross_time']['feature_value_comparison']['unsupervised_robust_z']['selection_uses_test'] is False


def test_broken_oracle_reference_file_fails_closed(tmp_path):
    bad = json.loads(REFERENCE.read_text(encoding='utf-8'))
    bad['rows'] = 0
    p = tmp_path/'bad_reference.json'
    p.write_text(json.dumps(bad), encoding='utf-8')
    assert json.loads(p.read_text())['rows'] == 0
    with pytest.raises(ValueError, match='独立来源参考记录数'):
        validate_independent_oracle(bad)


def test_selected_candidate_uses_validation_only(tmp_path):
    frame, provenance = load_nab(nab_fixture(tmp_path, devices=6, n=96), verify_official=False)
    first = evaluate_real_industrial(frame, provenance)
    chosen = first['folds']['cross_time']['feature_value_comparison']['unsupervised_robust_z']['selected_on_validation']
    # Change only final test labels, preserving the same sensor/time/history.
    # This may alter the measured AP, but may never change the selected model.
    from timepilot.industrial_eval_v0370 import _time_partition, _decision_times
    idx = _time_partition(frame, _decision_times(frame, pit_labels=False))['test']
    frame.loc[idx, 'event_label'] = 1 - frame.loc[idx, 'event_label']
    updated = evaluate_real_industrial(frame, provenance)
    now = updated['folds']['cross_time']['feature_value_comparison']['unsupervised_robust_z']['selected_on_validation']
    assert now == chosen
    assert updated['folds']['cross_time']['feature_value_comparison']['unsupervised_robust_z']['selection_uses_test'] is False
