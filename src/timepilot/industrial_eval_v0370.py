"""Predeclared industrial cross-time / unseen-device holdouts with truthful PIT status.

No validation/test row influences imputation, scaling, training or feature
construction. Public retrospective anomaly annotations do NOT establish
training label availability in a historical production deployment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from .feature_contracts import digest
from .feature_evaluation import _fit_predict, _metrics
from .industrial_dataset_v0370 import DatasetProvenance, sha256


def _decision_times(frame: pd.DataFrame, *, pit_labels: bool) -> pd.Series:
    # Public NAB/MetroPT timestamps are source-naive; UTC must not be fabricated.
    # User-provided factory timestamps were explicitly localized and converted to UTC.
    times = pd.to_datetime(frame['observation_time'], errors='coerce', format='mixed', utc=pit_labels)
    if times.isna().any():
        raise ValueError('观测/预测时点存在无效日期')
    return times


def validate_no_future_features(frame: pd.DataFrame, *, pit_labels: bool) -> dict:
    """Verify input availability; never infer a label receipt time from event time."""
    times = _decision_times(frame, pit_labels=pit_labels)
    available = pd.to_datetime(frame['measurement_available_at'], errors='coerce', format='mixed', utc=pit_labels)
    if available.isna().any() or (available > times).any():
        raise ValueError('观测特征晚于决策时点可用，禁止执行评价')
    if frame.duplicated(['device_id', 'observation_time']).any():
        raise ValueError('设备/观测时点重复，切分依据不确定')
    y = frame['event_label'].to_numpy(dtype=float)
    if not np.isfinite(y).all() or not np.isin(y, [0, 1]).all():
        raise ValueError('结果标签必须使用明确的 0/1 编码')
    receipt = pd.to_datetime(frame['label_available_at'], errors='coerce', format='mixed', utc=pit_labels)
    if pit_labels:
        if receipt.isna().any() or (receipt < times).any():
            raise ValueError('正式 PIT 任务必须逐行核实标签可用时间，且不得早于观测时间')
        positive = frame['event_label'].eq(1)
        onset = pd.to_datetime(frame.loc[positive, 'label_event_at'], errors='coerce', format='mixed', utc=True)
        if onset.isna().any() or (receipt.loc[positive] < onset).any():
            raise ValueError('正式标签不得在事件发生以前可用')
    else:
        if receipt.notna().any():
            raise ValueError('公开事后标注不得混填伪造的标签可用时间')
    return {
        'feature_receipt_verified': bool(pit_labels),
        'feature_receipt_note': ('user-declared measurement arrival timestamps' if pit_labels else
            'original observation timestamps only; physical ingestion latency unknown'),
        'supervised_label_receipt_verified': bool(pit_labels),
        'annotation_provenance': 'timestamped retrospective' if not pit_labels else 'explicit factory label receipt',
        'device_count': int(frame['device_id'].nunique()),
        'start': times.min().isoformat(), 'end': times.max().isoformat(),
        'positive_count': int(sum(y)), 'total_rows': len(frame),
    }


def _time_partition(frame: pd.DataFrame, when: pd.Series) -> dict[str, np.ndarray]:
    unique = pd.Series(when.unique()).sort_values().reset_index(drop=True)
    if len(unique) < 24:
        raise ValueError('跨时间验证至少需要24个不同的实际观测时点')
    # Fixed 60 / 20 / 20 chronological boundaries; equal-time rows never split.
    first = unique.iloc[int(len(unique) * 0.6)]
    second = unique.iloc[int(len(unique) * 0.8)]
    return {
        'train': np.where(when < first)[0],
        'validation': np.where((when >= first) & (when < second))[0],
        'test': np.where(when >= second)[0],
    }


def _device_partition(frame: pd.DataFrame, when: pd.Series,
                      holdout_devices: tuple[str, str] | None = None) -> dict[str, np.ndarray]:
    groups = sorted(frame['device_id'].astype(str).unique())
    if len(groups) < 4:
        raise ValueError('跨设备独立评价至少需要4台设备，且每组必须有有效观测')
    # String ordering, chosen before labels/metrics are read; no result-driven
    # device selection. Independent held-out device never enters feature fit.
    validation, test = holdout_devices if holdout_devices is not None else (groups[-2], groups[-1])
    if validation == test or validation not in groups or test not in groups:
        raise ValueError('预声明留出设备不存在或重复')
    group = frame['device_id'].astype(str)
    return {
        'train': np.where(~group.isin((validation, test)))[0],
        'validation': np.where(group == validation)[0],
        'test': np.where(group == test)[0],
    }


def _metrics_or_none(y: np.ndarray, pred: np.ndarray) -> dict:
    base = _metrics(y, pred, 'binary_classification')
    from sklearn.metrics import roc_auc_score, average_precision_score
    # Industrial telemetry often has tied readings/scores. The global legacy
    # metric sums per-row precisions after a stable sort and can therefore vary
    # with CSV row order for identical scores. AP must aggregate score ties as
    # a whole threshold group (sklearn's documented average-precision rule).
    full_classes = len(np.unique(y)) == 2
    ap = float(average_precision_score(y, pred)) if full_classes else None
    base['average_precision'] = ap
    base['pr_auc'] = ap
    base['ap_tie_policy'] = 'sklearn_average_precision_score; tied thresholds grouped'
    base['roc_auc'] = float(roc_auc_score(y, pred)) if full_classes else None
    base['positive_count'] = int((y == 1).sum())
    base['sample_count'] = int(len(y))
    base['prevalence'] = round(float(y.mean()), 8)
    base['sufficient_classes_for_ranking'] = bool(len(np.unique(y)) == 2)
    return base


def _unsupervised_score(train: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Train-only robust scale; labels never enter fitting or threshold selection."""
    with np.errstate(invalid='ignore'):
        median = np.nanmedian(train, axis=0)
        scale = np.nanmedian(np.abs(train - median), axis=0) * 1.4826
    median = np.nan_to_num(median, nan=0.0)
    scale = np.where(np.isfinite(scale) & (scale > 1e-5), scale, 1.0)
    z = np.abs(valid - median) / scale
    z = np.where(np.isfinite(z), z, 0.0)
    z_max = z.max(axis=1)
    # Predeclared 3-MAD threshold; never calibrated on validation/test labels.
    return 1.0 / (1.0 + np.exp(-np.clip(z_max - 3.0, -35, 35)))


def _run_split(frame: pd.DataFrame, *, kind: str, features: list[str],
               pit_labels: bool, model_families: Sequence[str], seed: int,
               holdout_devices: tuple[str, str] | None) -> dict:
    when = _decision_times(frame, pit_labels=pit_labels)
    partition = (_time_partition(frame, when) if kind == 'cross_time' else
                 _device_partition(frame, when, holdout_devices))
    a, b, c = (np.asarray(partition[name], dtype=int) for name in ('train', 'validation', 'test'))
    if pit_labels and kind == 'cross_device':
        # In a genuine PIT experiment the evaluation of unseen devices must
        # also happen after the labels used to train the model were available.
        # First 70% historical observations on seen devices are training;
        # unseen devices contribute only later observations to their holdouts.
        unique = pd.Series(when.unique()).sort_values().reset_index(drop=True)
        if len(unique) < 24:
            raise ValueError('跨设备未来验证缺少足够的独立观测时点')
        cutoff = unique.iloc[int(len(unique) * 0.7)]
        a = a[(when.iloc[a] < cutoff).to_numpy()]
        b = b[(when.iloc[b] >= cutoff).to_numpy()]
        c = c[(when.iloc[c] >= cutoff).to_numpy()]
    if not len(a) or not len(b) or not len(c):
        raise ValueError('独立训练/验证/测试切分不能为空')
    if set(a) & set(b) or set(a) & set(c) or set(b) & set(c):
        raise AssertionError('训练/验证/测试行存在重叠')
    train_before = len(a)
    if pit_labels:
        receipt = pd.to_datetime(frame['label_available_at'], utc=True, format='mixed', errors='coerce')
        # Training label may be consumed only if its actual receipt preceded
        # BOTH validation and test inference periods; no retroactive label use.
        cutoff = min(when.iloc[b].min(), when.iloc[c].min())
        a = a[(receipt.iloc[a] < cutoff).to_numpy()]
    if len(a) < 40 or min(len(b), len(c)) < 15:
        raise ValueError('组/时间/标签收据隔离后样本不足 (train>=40, val/test>=15)')
    if kind == 'cross_time' and not (when.iloc[a].max() < when.iloc[b].min() < when.iloc[c].min()):
        raise AssertionError('跨时间模型折未满足严格递增')
    dev = frame['device_id'].astype(str)
    if kind == 'cross_device':
        if set(dev.iloc[a]) & set(dev.iloc[b]) or set(dev.iloc[a]) & set(dev.iloc[c]) or set(dev.iloc[b]) & set(dev.iloc[c]):
            raise AssertionError('设备独立隔离失败')
    y = frame['event_label'].to_numpy(dtype=int)
    def part_info(idx):
        return {'rows': int(len(idx)), 'positive_count': int(y[idx].sum()),
                'devices': sorted(dev.iloc[idx].unique().tolist()),
                'start': when.iloc[idx].min().isoformat(),
                'end': when.iloc[idx].max().isoformat(),
                'rows_sha256': digest(idx.tolist())}
    splits = {name: part_info(idx) for name, idx in zip(('train', 'validation', 'test'), (a, b, c))}
    # Record insufficient class coverage without fabricating AP/AUC scores.
    if pit_labels and len(np.unique(y[a])) != 2:
        return {'status': 'insufficient_evidence', 'reason': 'PIT 监督训练样本中没有完整的正负标签',
                'partitions': splits, 'removed_late_training_labels': train_before - len(a)}
    raw = [f for f in features if not f.endswith(('_lag1', '_past3_mean'))]
    if not raw or len(raw) == len(features):
        raise ValueError('需要明确的原始传感量和严格过去窗口的派生特征')
    variants = {'raw_only': raw, 'with_features': features}
    report = {}
    for family in (model_families if pit_labels else ('unsupervised_robust_z',)):
        scores = {}
        for variant, columns in variants.items():
            if not pit_labels and variant == 'with_features':
                # Past-only residuals; unseen-device histories were built
                # within each individual device and use no future values.
                primary = raw[0]
                prior = primary + '_past3_mean'
                x = (frame[primary] - frame[prior]).to_numpy(dtype=float)[:, None]
            else:
                x = frame[columns].apply(pd.to_numeric, errors='coerce').to_numpy(dtype=float)
            if pit_labels:
                val_pred = _fit_predict(x[a], y[a], x[b], 'binary_classification', family, seed=seed)
                test_pred = _fit_predict(x[a], y[a], x[c], 'binary_classification', family, seed=seed)
            else:
                val_pred = _unsupervised_score(x[a], x[b])
                test_pred = _unsupervised_score(x[a], x[c])
            scores[variant] = {'validation': _metrics_or_none(y[b], val_pred),
                               'test': _metrics_or_none(y[c], test_pred)}
        report[family] = scores
    # Candidate selection is frozen on validation AP. Final-test scores are
    # reported for post-hoc assessment only; never used to choose features.
    comparisons = {}
    for family, variants in report.items():
        raw_val = variants['raw_only']['validation']['average_precision']
        enriched_val = variants['with_features']['validation']['average_precision']
        raw_test = variants['raw_only']['test']['average_precision']
        enriched_test = variants['with_features']['test']['average_precision']
        if raw_val is None or enriched_val is None:
            choice = None
        else:
            choice = 'with_features' if enriched_val > raw_val else 'raw_only'
        comparisons[family] = {
            'selected_on_validation': choice,
            'selection_uses_test': False,
            'validation_ap_delta_candidate_minus_raw':
                None if raw_val is None or enriched_val is None else float(enriched_val - raw_val),
            'test_ap_delta_candidate_minus_raw':
                None if raw_test is None or enriched_test is None else float(enriched_test - raw_test),
            'candidate_degraded_on_final_test':
                None if raw_test is None or enriched_test is None else bool(enriched_test < raw_test),
            'selected_test_ap': variants[choice]['test']['average_precision'] if choice else None,
            'posthoc_only_no_test_tuning': True,
        }
    fully_measurable = all(len(np.unique(y[idx])) == 2 for idx in (b, c))
    training_end = when.iloc[a].max()
    validation_start = when.iloc[b].min()
    test_start = when.iloc[c].min()
    # Unseen-device holdout by itself does not guarantee a historical
    # deployment simulation. With NAB the held-out February device predates
    # some April training devices; mark this explicitly in release evidence.
    chronological_transfer = bool(training_end < min(validation_start, test_start))
    return {
        'status': 'measured' if fully_measurable else 'insufficient_class_coverage',
        'partitions': splits,
        'removed_late_training_labels': train_before - len(a),
        'feature_causality': 'lag1 and past3 exclude current/future and partition by device',
        'split_scope': ('future_time_only' if kind == 'cross_time' else
                       'unseen_device_and_future_time' if pit_labels else 'unseen_device_only'),
        'train_and_test_disjoint': True,
        'temporal_transfer_audit': {
            'train_end': training_end.isoformat(),
            'validation_start': validation_start.isoformat(),
            'test_start': test_start.isoformat(),
            'training_precedes_both_holdouts': chronological_transfer,
            'chronological_split_protocol_satisfied': bool(pit_labels and chronological_transfer),
            'prospective_transfer_claim_allowed': False,  # Requires independently audited source/MES clocks
            'note': ('Non-chronological retrospective device transfer; cannot claim future deployment' 
                     if kind == 'cross_device' and not chronological_transfer else
                     'Chronological split checked, source timestamp/label provenance restrictions still apply'),
        },
        'features_selected_without_test': True,
        'no_test_tuning': True,
        'training_uses_annotations': bool(pit_labels),
        'models': report,
        'feature_value_comparison': comparisons,
    }


def evaluate_real_industrial(frame: pd.DataFrame, provenance: DatasetProvenance,
                             *, seed: int = 3700, models: Sequence[str] = ('linear',),
                             max_model_rows: int = 2_000_000,
                             holdout_devices: tuple[str, str] | None = None) -> dict:
    if len(frame) > max_model_rows:
        raise ValueError('模型评价输入超过上限，请明确调整计算预算，禁止隐式抽样')
    if not models or not set(models).issubset({'linear', 'forest'}):
        raise ValueError('算法必须为已审核的 linear 或 forest')
    pit = provenance.label_available_at == 'explicit_per_row_verified_by_input_contract'
    qa = validate_no_future_features(frame, pit_labels=pit)
    features = provenance.feature_columns
    if any(f not in frame or f in ('event_label', 'label_event_at', 'label_available_at') for f in features):
        raise ValueError('模型输入包含禁用标签或缺失字段')
    results = {}
    for kind in ('cross_time', 'cross_device'):
        try:
            results[kind] = _run_split(frame, kind=kind, features=features,
                                       pit_labels=pit, model_families=models, seed=seed,
                                       holdout_devices=holdout_devices)
        except (ValueError, AssertionError) as exc:
            results[kind] = {'status': 'blocked', 'reason': str(exc)}
    measured = [x for x in results.values() if x['status'] == 'measured']
    status = 'passed' if len(measured) == 2 else ('partial' if measured else 'blocked')
    return {
        'status': status, 'version': '0.37.0', 'data_source': provenance.dataset,
        'kind': provenance.kind,
        'assessment_type': ('PIT historical backtest' if pit else 'unsupervised anomaly detection; retrospective label scoring'),
        'production_prediction_claim_allowed': False,
        'point_in_time_label_availability_verified': pit,
        'timestamp_verification_scope': ('Business-provided timestamps passed consistency checks; external MES/PLC clock audit not performed'
                                         if pit else 'Source timestamps and retrospective labels only; ingestion/availability receipts unknown'),
        'predeclared_holdout_devices': list(holdout_devices) if holdout_devices else None,
        'time_zone_contract': provenance.timezone,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_checks': qa,
        'input_hashes': provenance.source_file_sha256,
        'folds': results,
        'limitations': ([
            '公开异常窗口为事后研究标注，没有标签收据时间：严禁报告为历史时点可训练的在线监督模型',
            'NAB 是云服务器运行遥测；不是汽车产线工艺质量数据',
        ] if not pit else [
            '工厂时间戳通过提供方声明验证，仍需对采集/报工系统的时钟偏差现场核验',
        ]),
    }


def write_evaluation(report: dict, out_dir: Path) -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    path = out / 'CROSS_TIME_DEVICE_EVAL.json'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    return path
