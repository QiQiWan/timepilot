"""Fail-closed restricted raw/candidate fields in real supervised evaluators."""
from __future__ import annotations
import pandas as pd
import pytest
from timepilot.feature_contracts import TaskSpecV2,EvaluationProtocol
from timepilot.feature_evaluation import _evaluate_matrix
from timepilot.featurelab362_model_matrix import _contract


def sample():
    n=96
    frame=pd.DataFrame({'raw': [float(i%11) for i in range(n)],
        'safe_candidate':[float(i%11)*1.4 for i in range(n)],
        'after_outcome__LEAKAGE_TRAP':[float(i*2) for i in range(n)],
        'target':[float(i*2) for i in range(n)]})
    contract=_contract(frame,'a'*64,'target')
    task=TaskSpecV2(task_id='qa-restricted',mode='supervised',task_type='regression',
        label_field_id='fld_target',split_kind='kfold',static_independence_confirmed=True)
    proto=EvaluationProtocol(task=task,set_id='qa-test',revision=1,models=['linear'])
    return frame,contract,task,proto


def test_leakage_trap_cannot_enter_raw_baseline():
    frame,contract,task,proto=sample()
    with pytest.raises(ValueError,match='restricted|受限'):
        _evaluate_matrix(frame,['raw','after_outcome__LEAKAGE_TRAP'],
            ['safe_candidate'],task,proto,contract,source_sha256=contract.source_sha256,
            spec_sha256='b'*64)


def test_leakage_trap_cannot_enter_generated_feature_list():
    frame,contract,task,proto=sample()
    with pytest.raises(ValueError,match='restricted|受限'):
        _evaluate_matrix(frame,['raw'],['after_outcome__LEAKAGE_TRAP'],
            task,proto,contract,source_sha256=contract.source_sha256,spec_sha256='b'*64)
