"""Independently pinned real NAB source and result oracle.

The reference was calculated from the nine official upstream raw files via an
independent implementation. It is a **comparison target**, never model input.
Deviation fails the release gate. Historical anomaly windows are retrospective.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .industrial_dataset_v0370 import sha256, load_nab


def validate_independent_oracle(oracle: dict) -> None:
    """Detect accidentally or maliciously edited independent baseline metadata."""
    devices = oracle.get('device_source_stats', {})
    if not devices or len(devices) != oracle.get('devices') or sum(row['rows'] for row in devices.values()) != oracle.get('rows'):
        raise ValueError('独立来源参考记录数与逐设备统计不一致')
    if sum(row['window_marked_records'] for row in devices.values()) != oracle.get('total_window_marked_records'):
        raise ValueError('独立来源参考标签数量与逐设备统计不一致')
    holdout = oracle.get('predeclared_holdout_devices', {})
    if not all(holdout.get(x) in devices for x in ('validation', 'test')) or holdout.get('validation') == holdout.get('test'):
        raise ValueError('独立来源预声明留出设备无效')
    if len(oracle.get('git_object_pins', {})) != len(devices) + 1:
        raise ValueError('独立来源参考原始文件指纹清单不完整')


def verify_oracle(artifacts: Path, oracle_path: Path, *, raw_root: Path, tolerance: float = 0.004) -> dict[str, Any]:
    if not (0 < tolerance <= 0.02):
        raise ValueError('独立计算核验容差必须在 (0, 0.02]')
    folder = Path(artifacts)
    oracle = json.loads(Path(oracle_path).read_text(encoding='utf-8'))
    validate_independent_oracle(oracle)
    # Validate all nine immutable pinned source files before trusting any
    # self-declared DATA_CONTRACT provenance from the derived assets folder.
    frame_original, verified_provenance = load_nab(raw_root, verify_official=True)
    contract = json.loads((folder / 'DATA_CONTRACT.json').read_text(encoding='utf-8'))
    evaluation = json.loads((folder / 'CROSS_TIME_DEVICE_EVAL.json').read_text(encoding='utf-8'))
    source = folder / 'industrial_normalized.csv'
    problems = []
    add = problems.append
    if oracle.get('audit_type', '').find('Independent official source') < 0:
        add('基准来源声明无效')
    if contract.get('normalized_sha256') != sha256(source):
        add('归一化数据文件 SHA256 与数据契约不符')
    original = contract.get('origin', {})
    if original.get('dataset') != 'nab_ec2_cpu' or original.get('kind') != 'real_operational_cloud_telemetry':
        add('输入来源不是已审核的官方 NAB 实际遥测')
    if evaluation.get('data_source') != original.get('dataset') or evaluation.get('input_hashes') != original.get('source_file_sha256'):
        add('模型结果与输入数据来源不一致')
    if evaluation.get('point_in_time_label_availability_verified') or evaluation.get('production_prediction_claim_allowed'):
        add('真实公开数据错误地声明了生产监督预测资格')
    if evaluation.get('status') != 'passed':
        add('跨时间和跨设备模型评价未全部完成')
    original_hashes = original.get('source_file_sha256', {})
    if original_hashes != verified_provenance.source_file_sha256:
        add('归一化数据来源指纹与重新验真的官方原始文件不符')
    if len(frame_original) != len(frame):
        add('独立官方数据源与计算用归一化数据行数不一致')
    if set(original_hashes) != set(oracle.get('git_object_pins', {})):
        add('公开原始文件清单与独立核验的九个文件不一致')
    frame = pd.read_csv(source, low_memory=False)
    if len(frame) != oracle['rows'] or frame['device_id'].nunique() != oracle['devices']:
        add('真实记录数或实例数量与官方独立统计不符')
    if int(frame['event_label'].sum()) != oracle['total_window_marked_records']:
        add('标注窗口覆盖的行数与官方独立统计不符')
    for device, expected in oracle.get('device_source_stats', {}).items():
        records = frame.loc[frame['device_id'].astype(str) == device]
        if len(records) != expected['rows'] or int(records['event_label'].sum()) != expected['window_marked_records']:
            add('独立设备原始行数/标注窗口异常：' + device)
    if 'unsupervised_robust_z' not in evaluation.get('folds', {}).get('cross_time', {}).get('models', {}):
        add('缺少明确的无监督原始传感量评分基线')
    references = {
        'cross_time': {
            'validation': oracle['backtest']['cross_time']['validation'],
            'test': oracle['backtest']['cross_time']['test'],
        },
        'cross_device': {
            'validation': oracle['backtest']['cross_device']['validation'],
            'test': oracle['backtest']['cross_device']['test'],
        },
    }
    observed = {}
    for split_name, parts in references.items():
        fold = evaluation.get('folds', {}).get(split_name, {})
        if fold.get('status') != 'measured':
            add('独立验证折未产生可测结果：' + split_name)
            continue
        observed[split_name] = {}
        for phase, expected in parts.items():
            partition = fold.get('partitions', {}).get(phase, {})
            if partition.get('rows') != expected['rows'] or partition.get('positive_count') != expected['positive']:
                add(f'{split_name}/{phase} 样本数量或标签数量与独立参考不一致')
            scores = fold.get('models', {}).get('unsupervised_robust_z', {})
            observed[split_name][phase] = {}
            for variant, baseline in [('raw_only', 'raw'), ('with_features', 'past_residual')]:
                metrics = scores.get(variant, {}).get(phase, {})
                observed[split_name][phase][variant] = {
                    'average_precision': metrics.get('average_precision'),
                    'roc_auc': metrics.get('roc_auc'),
                }
                if 'tied thresholds grouped' not in metrics.get('ap_tie_policy', ''):
                    add(f'{split_name}/{phase}/{variant} 未使用并列分数一致的 AP')
                for label, reference_key in [('average_precision', 'AP'), ('roc_auc', 'ROC_AUC')]:
                    value = metrics.get(label)
                    if not isinstance(value, (int, float)) or abs(float(value) - baseline_reference(baseline, expected)[reference_key]) > tolerance:
                        add(f'{split_name}/{phase}/{variant}/{label} 与独立参考偏离大于 {tolerance}')
    # Require historical retrospective scope, not prospective claims. NAB
    # selected held-out Feb device has some April training records.
    transfer = evaluation.get('folds', {}).get('cross_device', {}).get('temporal_transfer_audit', {})
    if transfer.get('prospective_transfer_claim_allowed') is not False:
        add('NAB 跨设备试验错误地声明具备未来部署外推资格')
    actual = {
        'status': 'passed' if not problems else 'failed',
        'dataset': 'NAB official eight EC2 CPU series',
        'source_scope': 'independent retrospective reference; not factory deployment evidence',
        'source_files': len(original_hashes),
        'rows': len(frame),
        'positive_window_records': int(frame['event_label'].sum()),
        'comparison_tolerance_absolute': tolerance,
        'metrics': observed,
        'problems': problems,
        'independent_oracle_sha256': sha256(Path(oracle_path)),
        'normalized_source_sha256': sha256(source),
    }
    return actual


def baseline_reference(which: str, record: dict) -> dict:
    return record[which]
