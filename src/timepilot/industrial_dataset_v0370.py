"""v0.37.0 industrial telemetry adapters and auditable label-time contracts.

Public anomaly windows denote retrospective annotations. They have *no known*
label-availability timestamp and must never be relabeled as online outcomes.
Factory CSV inputs may opt into PIT evaluation ONLY with explicit time fields.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd


NAB_REPO = 'https://github.com/numenta/NAB'
NAB_ROOT = 'https://raw.githubusercontent.com/numenta/NAB/master'
NAB_PREFIX = 'realAWSCloudwatch/ec2_cpu_utilization_'
METROPT_URL = 'https://archive.ics.uci.edu/dataset/791/metropt+3+dataset'
METROPT_WINDOWS = [
    ('2020-04-18 00:00:00', '2020-04-18 23:59:00'),
    ('2020-05-29 23:30:00', '2020-05-30 06:00:00'),
    ('2020-06-05 10:00:00', '2020-06-07 14:30:00'),
    ('2020-07-15 14:30:00', '2020-07-15 19:00:00'),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _times(values: pd.Series, *, explicit_timezone: str | None) -> pd.Series:
    """Public timestamps stay source-native when timezone was not published."""
    parsed = pd.to_datetime(values, errors='coerce', format='mixed')
    if parsed.isna().any():
        raise ValueError('时间字段存在无法解析的观测值，无法建立时间隔离')
    if explicit_timezone:
        if parsed.dt.tz is None:
            parsed = parsed.dt.tz_localize(explicit_timezone, ambiguous='raise', nonexistent='raise')
        parsed = parsed.dt.tz_convert('UTC')
    else:
        # Do not invent a UTC timezone for public naive timestamps.
        if parsed.dt.tz is not None:
            raise ValueError('已有时区的时间字段须提供明确的时区契约')
    return parsed


def _windows_label(times: pd.Series, windows: list[tuple[str, str]]) -> tuple[pd.Series, pd.Series]:
    result = pd.Series(np.zeros(len(times), dtype=np.int8), index=times.index)
    onset = pd.Series([None] * len(times), index=times.index, dtype='object')
    for a, b in windows:
        start = pd.Timestamp(a)
        end = pd.Timestamp(b)
        if end < start:
            raise ValueError('异常窗口的终止时间不得早于开始时间')
        mask = (times >= start) & (times <= end)
        result.loc[mask] = 1
        onset.loc[mask] = start.isoformat()
    return result, onset


@dataclass(frozen=True)
class DatasetProvenance:
    dataset: str
    kind: str
    source: str
    label_semantics: str
    label_available_at: str
    timezone: str
    license_notice: str
    source_file_sha256: dict[str, str]
    rows: int
    devices: int
    positives: int
    columns: list[str]
    feature_columns: list[str]
    run_scope: str = 'offline_retrospective_experimental'


def _finalize(frame: pd.DataFrame, *, name: str, source: str, kind: str,
              label_semantics: str, availability: str, timezone: str,
              notice: str, originals: dict[str, str]) -> tuple[pd.DataFrame, DatasetProvenance]:
    required = ['device_id', 'observation_time', 'sensor_value', 'event_label',
                'label_event_at', 'label_available_at', 'measurement_available_at']
    if not set(required).issubset(frame):
        raise ValueError('工业输入缺少核心字段：' + str(sorted(set(required)-set(frame))))
    if frame[required[:3]].isna().any().any():
        raise ValueError('设备、观测时间与主要传感量不得为空')
    if frame['device_id'].astype(str).str.strip().eq('').any():
        raise ValueError('设备标识不得为空')
    if frame.duplicated(['device_id', 'observation_time']).any():
        raise ValueError('设备与观测时间重复；请先定义样本粒度或去重规则')
    labels = pd.to_numeric(frame['event_label'], errors='coerce')
    if labels.isna().any() or not labels.isin([0, 1]).all():
        raise ValueError('二分类标签必须为完整且已定义的 0/1')
    if ((labels == 1) & frame['label_event_at'].isna()).any():
        raise ValueError('正样本缺少对应的标签事件时点')
    signals = ['sensor_value', *[c for c in frame if c.startswith('signal_')]]
    for col in signals:
        a = pd.to_numeric(frame[col], errors='coerce')
        if a.notna().sum() == 0 or np.isinf(a.to_numpy(dtype=float)).any():
            raise ValueError('工业信号缺少有效有限观测值：' + col)
        frame[col] = a.astype('float64')
    frame = frame.sort_values(['device_id', 'observation_time'], kind='stable').reset_index(drop=True)
    # Entirely causal: does not include the current or a future reading in history.
    feature_cols = list(signals)
    for col in signals:
        prior = frame.groupby('device_id', sort=False)[col].shift(1)
        frame[f'{col}_lag1'] = prior
        frame[f'{col}_past3_mean'] = (prior.groupby(frame['device_id'], sort=False)
                                        .transform(lambda s: s.rolling(3, min_periods=1).mean()))
        feature_cols.extend([f'{col}_lag1', f'{col}_past3_mean'])
    # Preserve the physical source's timestamp representation, never recast the
    # public annotation window as a measured incident/discovery timestamp.
    origin = DatasetProvenance(
        dataset=name, kind=kind, source=source, label_semantics=label_semantics,
        label_available_at=availability, timezone=timezone,
        license_notice=notice, source_file_sha256=originals,
        rows=len(frame), devices=int(frame['device_id'].nunique()),
        positives=int(labels.sum()), columns=list(frame.columns), feature_columns=feature_cols)
    return frame, origin


def load_nab(nab_root: Path, *, min_devices: int = 4,
             verify_official: bool = True) -> tuple[pd.DataFrame, DatasetProvenance]:
    """Read *unmodified* NAB EC2 CPU data and its official anomaly-window JSON."""
    root = Path(nab_root)
    labels_file = root / 'labels' / 'combined_windows.json'
    if not labels_file.is_file():
        raise FileNotFoundError('缺少 NAB 官方 labels/combined_windows.json；禁止自动编造异常标签')
    annotations = json.loads(labels_file.read_text(encoding='utf-8'))
    pinned = None
    if verify_official:
        # Only the exact public corpus audited by source blob IDs can be
        # called a physical real-data acceptance. Local lookalikes fail shut.
        lock_path = Path(__file__).resolve().parents[2] / 'benchmarks' / 'featurelab_v0370' / 'PUBLIC_NAB_PIN.json'
        if not lock_path.is_file():
            raise FileNotFoundError('NAB 公开真实数据签名清单缺失')
        pinned = json.loads(lock_path.read_text(encoding='utf-8'))['git_blob_sha1']
        def check(relative: str, path: Path):
            raw = path.read_bytes()
            candidate = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if pinned.get(relative) != candidate:
                raise ValueError('NAB 官方文件 Git Blob 指纹不一致：' + relative)
        check('labels/combined_windows.json', labels_file)
    files = sorted((root / 'data' / 'realAWSCloudwatch').glob('ec2_cpu_utilization_*.csv'))
    files = [p for p in files if p.is_file()]
    if verify_official:
        expected = {Path(relative).name for relative in pinned if relative.startswith('data/')}
        present = {path.name for path in files}
        if present != expected:
            raise ValueError('NAB 官方真实数据集必须包含锁定的全部设备：' + str(sorted(expected - present)))
    if len(files) < min_devices:
        raise ValueError(f'NAB EC2 CPU 至少需要 {min_devices} 个实际设备数据文件，发现 {len(files)} 个')
    hashes = {'labels/combined_windows.json': sha256(labels_file)}
    readings = []
    for path in files:
        rel = 'realAWSCloudwatch/' + path.name
        if rel not in annotations:
            raise ValueError('缺少数据文件对应的官方异常窗口：' + rel)
        if pinned is not None:
            check('data/' + rel, path)
        raw = pd.read_csv(path)
        if list(raw.columns) != ['timestamp', 'value']:
            raise ValueError('NAB 源文件字段契约变更：' + str(path.name))
        times = _times(raw['timestamp'], explicit_timezone=None)
        labels, onsets = _windows_label(times, annotations[rel])
        if not times.is_monotonic_increasing or times.duplicated().any():
            raise ValueError('NAB 原始设备时间无序/重复：' + path.name)
        if len(raw) < 24:
            raise ValueError('NAB 实际设备样本数量不足：' + path.name)
        readings.append(pd.DataFrame({
            'device_id': path.stem.removeprefix('ec2_cpu_utilization_'),
            'observation_time': times.astype(str),
            'sensor_value': pd.to_numeric(raw['value'], errors='coerce'),
            'event_label': labels,
            'label_event_at': onsets,
            'label_available_at': None,
            'measurement_available_at': times.astype(str),
        }))
        hashes['data/' + rel] = sha256(path)
    return _finalize(pd.concat(readings, ignore_index=True), name='nab_ec2_cpu',
        source=NAB_REPO if verify_official else 'test-only synthetic NAB-shaped fixture',
        kind='real_operational_cloud_telemetry' if verify_official else 'synthetic_test_fixture',
        label_semantics='NAB retrospective anomaly windows, NOT certified machine failure timestamps',
        availability='unknown; no verified PIT supervised training claim',
        timezone='official source-naive; not asserted UTC',
        notice='NAB repository MIT; follow dataset attribution and publisher terms',
        originals=hashes)


def load_metropt(csv_file: Path) -> tuple[pd.DataFrame, DatasetProvenance]:
    """UCI MetroPT-3 operating compressor; documented incident windows are retrospective."""
    csv_file = Path(csv_file)
    # Projection pushdown: only load supported numeric channels rather than
    # the entire large raw CSV; peak resident memory drops on 1.5M rows.
    header = set(pd.read_csv(csv_file, nrows=0).columns)
    if 'timestamp' not in header:
        raise ValueError('MetroPT-3 缺少 timestamp 字段')
    features = [c for c in ['Motor_current', 'Oil_temperature', 'TP2', 'TP3',
                             'H1', 'DV_pressure', 'Reservoirs'] if c in header]
    raw = pd.read_csv(csv_file, usecols=['timestamp', *features])
    if 'Motor_current' not in features or len(features) < 3:
        raise ValueError('MetroPT-3 核心模拟信号缺失；请提供官方源 CSV')
    timestamps = _times(raw['timestamp'], explicit_timezone=None)
    labels, onsets = _windows_label(timestamps, METROPT_WINDOWS)
    frame = pd.DataFrame({
        'device_id': 'metropt3_apu', 'observation_time': timestamps.astype(str),
        'sensor_value': raw['Motor_current'], 'event_label': labels,
        'label_event_at': onsets, 'label_available_at': None,
        'measurement_available_at': timestamps.astype(str),
    })
    for col in features:
        if col != 'Motor_current':
            frame['signal_' + col] = raw[col]
    return _finalize(frame, name='metropt3_apu', source=METROPT_URL,
        kind='real_in_service_train_air_compressor',
        label_semantics='UCI-reported compressor air-leak intervals, retrospectively joined; no row labels supplied',
        availability='unknown; company report issue times not provided',
        timezone='not specified in UCI data; no UTC assertion',
        notice='UCI MetroPT-3, CC BY 4.0, cite original repository and authors',
        originals={csv_file.name: sha256(csv_file)})


def load_factory_csv(csv_file: Path, schema_path: Path) -> tuple[pd.DataFrame, DatasetProvenance]:
    """Business-furnished *verified* labels with timestamps and explicit timezone.

    Requires outcome availability for every positive AND negative label. Source
    columns not explicitly allowlisted as sensors are never model inputs.
    """
    config = json.loads(Path(schema_path).read_text(encoding='utf-8'))
    required = ['device_id', 'observation_time', 'measurement_available_at',
                'label', 'label_event_at', 'label_available_at', 'sensors', 'timezone']
    if any(not config.get(k) for k in required):
        raise ValueError('真实工业标签契约缺少：' + ','.join(k for k in required if not config.get(k)))
    if not isinstance(config['sensors'], list) or not 1 <= len(config['sensors']) <= 8:
        raise ValueError('传感量显式白名单必须包含 1 至 8 列')
    if len(config['sensors']) != len(set(config['sensors'])):
        raise ValueError('传感量白名单重复')
    allowed = [config[k] for k in required[:6]] + config['sensors']
    if len(allowed) != len(set(allowed)):
        raise ValueError('标签、时间、设备字段不得进入传感器特征白名单')
    available = set(pd.read_csv(csv_file, nrows=0).columns)
    if not set(allowed).issubset(available):
        raise ValueError('工厂原始数据缺少契约字段：' + str(sorted(set(allowed) - available)))
    # Projection pushdown: avoid reading unrelated factory/MES fields into memory.
    raw = pd.read_csv(csv_file, usecols=allowed)
    timezone = str(config['timezone'])
    dates = {k: _times(raw[config[k]], explicit_timezone=timezone)
             for k in ['observation_time','measurement_available_at','label_available_at']}
    if (dates['measurement_available_at'] > dates['observation_time']).any():
        raise ValueError('有传感量在预测/决策时点尚不可用：未来特征泄漏')
    if (dates['label_available_at'] < dates['observation_time']).any():
        raise ValueError('标签可用时间早于观测时间；请确认业务口径')
    outcomes = pd.to_numeric(raw[config['label']], errors='coerce')
    positive = outcomes.eq(1)
    event_raw = raw[config['label_event_at']]
    events = _times(event_raw[positive], explicit_timezone=timezone)
    if (dates['label_available_at'][positive] < events).any():
        raise ValueError('标签可用时间早于已确认的标签事件时间')
    if (events < dates['observation_time'][positive]).any() and config.get('label_task') == 'future_failure':
        raise ValueError('未来故障任务的事件发生时间早于预测时点')
    frame = pd.DataFrame({
        'device_id': raw[config['device_id']].astype('string'),
        'observation_time': dates['observation_time'].astype(str),
        'sensor_value': raw[config['sensors'][0]],
        'event_label': outcomes,
        'label_event_at': pd.Series([None] * len(raw), dtype='object'),
        'label_available_at': dates['label_available_at'].astype(str),
        'measurement_available_at': dates['measurement_available_at'].astype(str),
    })
    frame.loc[positive, 'label_event_at'] = events.astype(str)
    for i, col in enumerate(config['sensors'][1:], start=1):
        frame['signal_' + str(i)] = raw[col]
    result, info = _finalize(frame, name=str(config.get('dataset_name', 'factory_explicit_contract')),
        source='user-provided:' + Path(csv_file).name,
        kind='user_supplied_industrial_measurements',
        label_semantics=str(config.get('label_task', 'event_classification')) + '; confirmed event and availability timestamps',
        availability='explicit_per_row_verified_by_input_contract', timezone='UTC (converted from ' + timezone + ')',
        notice='private factory file; do not redistribute without permission',
        originals={Path(csv_file).name: sha256(csv_file), Path(schema_path).name: sha256(schema_path)})
    return result, info


def write_normalized(frame: pd.DataFrame, provenance: DatasetProvenance, out: Path) -> dict:
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    dest = out / 'industrial_normalized.csv'
    staging = out / '.industrial_normalized.csv.part'
    try:
        frame.to_csv(staging, index=False, float_format='%.12g', chunksize=50_000)
        staging.replace(dest)
    finally:
        staging.unlink(missing_ok=True)
    contract = {
        'schema_version': '0.37.0', 'origin': asdict(provenance),
        'normalized_sha256': sha256(dest),
        'fields': [
            {'name': col, 'role': ('entity_key' if col == 'device_id' else
                'event_time' if col == 'observation_time' else
                'label' if col == 'event_label' else
                'available_at' if col in ('measurement_available_at','label_available_at') else
                'restricted_audit_time' if col == 'label_event_at' else 'value'),
             'model_input_allowed': col in provenance.feature_columns}
            for col in frame.columns],
        'label_availability_verified': provenance.label_available_at == 'explicit_per_row_verified_by_input_contract',
        'validation_scope': ('pit_supervision_eligible' if provenance.label_available_at ==
            'explicit_per_row_verified_by_input_contract' else 'retrospective_only'),
    }
    (out / 'DATA_CONTRACT.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return contract
