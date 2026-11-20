"""Independent DuckDB/Parquet/Pandas numerical acceptance on normalized real data."""
from __future__ import annotations

import math
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .industrial_dataset_v0370 import sha256


def verify_industrial_parquet(normalized_csv: Path, out: Path) -> dict:
    """Execute *genuine* DuckDB & PyArrow, returning blocked if unavailable."""
    try:
        import duckdb
        import pyarrow.parquet as pq
    except ImportError as exc:
        return {'status': 'blocked', 'reason': 'DuckDB/PyArrow runtime unavailable: ' + str(exc)}
    normalized_csv = Path(normalized_csv)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    parquet = out / 'industrial_normalized.parquet'
    contract_file = out / 'DATA_CONTRACT.json'
    if not contract_file.is_file():
        raise ValueError('缺少同源时间和字段契约；禁止猜测原始时间的时区')
    contract = json.loads(contract_file.read_text(encoding='utf-8'))
    if contract.get('normalized_sha256') != sha256(normalized_csv):
        raise ValueError('当前真实原始 CSV 与同源数据契约的 SHA256 不一致')
    zone = str(contract.get('origin', {}).get('timezone', ''))
    naive = ('not asserted UTC' in zone or 'not specified' in zone)
    if not naive and not zone.startswith('UTC'):
        raise ValueError('缺少明确的时区契约，无法执行物理分桶复算')
    con = duckdb.connect(':memory:')
    # A session-local timezone must never silently shift wall-clock buckets.
    con.execute("SET TimeZone='UTC'")
    try:
        con.execute('CREATE TEMP TABLE source AS SELECT * FROM read_csv_auto(?, header=true, sample_size=-1)',
                    [str(normalized_csv)])
        def source_stats():
            return con.execute('''SELECT COUNT(*) AS rows, COUNT(DISTINCT device_id) AS devices,
                SUM(TRY_CAST(event_label AS BIGINT)) AS positives,
                SUM(TRY_CAST(sensor_value AS DOUBLE)) AS sensor_sum
                FROM source''').fetchone()
        expected = source_stats()
        pq_name = "'" + str(parquet).replace("'", "''") + "'"
        con.execute('COPY (SELECT * FROM source ORDER BY device_id, observation_time) TO '
                    + pq_name + ' (FORMAT PARQUET)')
        meta = pq.ParquetFile(parquet)
        if meta.metadata.num_rows != expected[0]:
            raise AssertionError('Arrow Parquet 记录数与 DuckDB 原始记录数不一致')
        con.execute('CREATE TEMP TABLE reloaded AS SELECT * FROM read_parquet(?)', [str(parquet)])
        actual = con.execute('''SELECT COUNT(*), COUNT(DISTINCT device_id),
                SUM(TRY_CAST(event_label AS BIGINT)), SUM(TRY_CAST(sensor_value AS DOUBLE))
                FROM reloaded''').fetchone()
        if tuple(expected[:3]) != tuple(actual[:3]) or not math.isclose(
            float(expected[3]), float(actual[3]), rel_tol=1e-10, abs_tol=1e-8):
            raise AssertionError('Parquet 回读后，设备数/标签数/数值总和变化')
        # Independent reference from the CSV, not merely two SQL queries.
        ref = pd.read_csv(normalized_csv, usecols=['device_id','observation_time','event_label','sensor_value'])
        if (len(ref), ref['device_id'].nunique(), int(ref['event_label'].sum())) != tuple(expected[:3]):
            raise AssertionError('Pandas CSV 独立数值参考与 DuckDB 不一致')
        if not math.isclose(float(ref['sensor_value'].sum()), float(expected[3]), rel_tol=1e-10, abs_tol=1e-8):
            raise AssertionError('传感量总和相对独立参考发生漂移')
        # Compare real per-device/time-bucket statistics independently.
        ref['hour'] = pd.to_datetime(ref['observation_time'], errors='raise', utc=not naive).dt.floor('h')
        oracle = ref.groupby(['device_id', 'hour'], sort=True).agg(
            rows=('event_label','size'), positives=('event_label','sum'),
            mean_value=('sensor_value','mean')).reset_index()
        hour_type = 'TIMESTAMP' if naive else 'TIMESTAMPTZ'
        physical = con.execute(f'''SELECT device_id,
            date_trunc('hour', TRY_CAST(observation_time AS {hour_type})) AS hour,
            COUNT(*) AS rows, SUM(TRY_CAST(event_label AS BIGINT)) AS positives,
            AVG(TRY_CAST(sensor_value AS DOUBLE)) AS mean_value
            FROM reloaded GROUP BY 1,2 ORDER BY 1,2''').df()
        physical['hour'] = pd.to_datetime(physical['hour'], utc=not naive)
        compared = oracle.merge(physical, on=['device_id','hour'], how='outer',
                                suffixes=('_reference','_duckdb'), indicator=True)
        if len(compared) != len(oracle) or not compared['_merge'].eq('both').all():
            raise AssertionError('跨设备逐时桶输出数量或时间边界不一致')
        for field in ('rows','positives'):
            if not (compared[f'{field}_reference'].to_numpy() == compared[f'{field}_duckdb'].to_numpy()).all():
                raise AssertionError('时桶分组计数/异常记录不一致：' + field)
        if not np.allclose(compared['mean_value_reference'], compared['mean_value_duckdb'],
                           rtol=0, atol=1e-8, equal_nan=True):
            raise AssertionError('真实设备时桶均值偏离独立数值参考')
        return {
            'status': 'passed', 'backend': {'duckdb': duckdb.__version__, 'arrow': pq.__name__},
            'time_bucket_semantics': 'source-naive' if naive else 'declared-UTC',
            'rows': int(expected[0]), 'device_count': int(expected[1]),
            'positive_count': int(expected[2]), 'sensor_sum': float(expected[3]),
            'checked_device_hour_buckets': int(len(oracle)),
            'input_csv_sha256': sha256(normalized_csv),
            'physical_parquet_sha256': sha256(parquet),
            'parquet_bytes': parquet.stat().st_size,
            'independent_engines': ['pandas_csv', 'duckdb_parquet', 'pyarrow_metadata'],
        }
    finally:
        con.close()
