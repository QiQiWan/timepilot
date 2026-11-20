"""v0.37.0 isolated repeatable CPU/RSS performance evidence.

No hidden subsampling. Synthetic loads are called synthetic. Real loads require
an explicitly supplied normalized real-data CSV and source contract.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def _generate(n: int, seed: int):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    if n < 1000:
        raise ValueError('性能基准至少1000行')
    ndev = 16
    per_device = (n + ndev - 1) // ndev
    did = np.repeat(np.arange(ndev), per_device)[:n]
    step = np.tile(np.arange(per_device), ndev)[:n]
    raw = rng.standard_normal(n) + 0.2 * np.sin(step / 37)
    label = (raw > 1.1).astype(int)
    times = pd.Timestamp('2024-01-01', tz='UTC') + pd.to_timedelta(step * 5, unit='min')
    return pd.DataFrame({
        'device_id': np.array([f'D{d:02d}' for d in did], dtype=object),
        'observation_time': times.astype(str),
        'sensor_value': raw,
        'event_label': label,
        'label_event_at': np.where(label == 1, times.astype(str), None),
        'label_available_at': (times + pd.Timedelta(minutes=1)).astype(str),
        'measurement_available_at': times.astype(str),
    })


def _worker(rows: int, input_file: Path | None, seed: int, require_physical: bool) -> dict:
    import psutil
    import pandas as pd
    import numpy as np
    from timepilot.industrial_dataset_v0370 import DatasetProvenance
    from timepilot.industrial_eval_v0370 import evaluate_real_industrial
    from timepilot.industrial_dataset_v0370 import sha256
    rss = psutil.Process().memory_info().rss
    steps = []
    def step(name, fn):
        a = time.perf_counter()
        value = fn()
        steps.append({'stage': name, 'seconds': round(time.perf_counter() - a, 6),
                      'rss_now_bytes': psutil.Process().memory_info().rss})
        return value
    with tempfile.TemporaryDirectory(prefix='timepilot_v0370_perf_') as tmp:
        dest = Path(tmp) / 'telemetry.csv'
        if input_file is None:
            scope = 'synthetic_deterministic'
            data = step('generate', lambda: _generate(rows, seed))
            step('write_input', lambda: data.to_csv(dest, index=False))
        else:
            scope = 'real_source_existing_file'
            source = Path(input_file)
            contract_file = source.parent / 'DATA_CONTRACT.json'
            if not contract_file.is_file():
                raise ValueError('真实数据性能验收必须有同目录 DATA_CONTRACT.json 来源证据')
            contract = json.loads(contract_file.read_text(encoding='utf-8'))
            if contract.get('normalized_sha256') != sha256(source):
                raise ValueError('实际输入文件 SHA256 与真实数据契约不一致')
            if not source.exists():
                raise FileNotFoundError(source)
            # File is copied without content modification; output hash is explicit.
            import shutil
            step('copy_input', lambda: shutil.copyfile(source, dest))
            del source
        csv_sha = sha256(dest)
        data = step('read_csv', lambda: pd.read_csv(dest))
        actual_rows = len(data)
        if input_file is None and actual_rows != rows:
            raise AssertionError('生成行数与读取行数不符')
        def feature():
            data['observation_time'] = pd.to_datetime(data['observation_time'], utc=True)
            if data.duplicated(['device_id', 'observation_time']).any():
                raise ValueError('设备与时点重复')
            data.sort_values(['device_id', 'observation_time'], kind='stable', inplace=True)
            prior = data.groupby('device_id', sort=False)['sensor_value'].shift(1)
            data['sensor_value_lag1'] = prior
            data['sensor_value_past3_mean'] = prior.groupby(data['device_id'], sort=False).transform(
                lambda s: s.rolling(3, min_periods=1).mean())
            return True
        step('causal_features', feature)
        stages = {}
        try:
            import duckdb
            import pyarrow.parquet as pq
            target = Path(tmp) / 'physical.parquet'
            def parquet():
                connection = duckdb.connect(':memory:')
                try:
                    connection.register('telemetry', data)
                    connection.execute(f"COPY (SELECT * FROM telemetry) TO '{target}' (FORMAT PARQUET)")
                    assert pq.ParquetFile(target).metadata.num_rows == actual_rows
                    return {'parquet_bytes': target.stat().st_size, 'sha256': sha256(target)}
                finally:
                    connection.close()
            stages['physical'] = step('physical_parquet', parquet)
            physical_status = 'measured'
        except ImportError as exc:
            physical_status = 'blocked'
            stages['physical'] = {'status': 'blocked', 'reason': str(exc)}
            if require_physical:
                raise RuntimeError('真实 DuckDB/Arrow 缺失，物理性能门禁阻断') from exc
        # Evaluation fully uses available rows, no silent sample/cap.
        def model():
            p = DatasetProvenance(
                dataset='bench_input', kind=scope, source=str(input_file or 'generator'),
                label_semantics='benchmark labels only',
                label_available_at='explicit_per_row_verified_by_input_contract', timezone='UTC',
                license_notice='synthetic or caller-provided source', source_file_sha256={'csv': csv_sha},
                rows=actual_rows, devices=int(data['device_id'].nunique()),
                positives=int(data['event_label'].sum()), columns=list(data.columns),
                feature_columns=['sensor_value','sensor_value_lag1','sensor_value_past3_mean'])
            if input_file is not None:
                p = DatasetProvenance(**contract['origin'])
            return evaluate_real_industrial(data, p, models=('linear',), max_model_rows=max(actual_rows, 1))
        outcome = step('cross_time_and_device_fit', model)
        result = {'status': 'passed' if outcome['status'] == 'passed' else 'partial',
                  'mode': scope, 'data_source': outcome['data_source'], 'rows': actual_rows,
                  'csv_sha256': csv_sha,
                  'model_evaluation_status': outcome['status'],
                  'cross_time_status': outcome['folds']['cross_time']['status'],
                  'cross_device_status': outcome['folds']['cross_device']['status'],
                  'physical_status': physical_status,
                  'stages': steps, 'physical_artifacts': stages}
        # Linux reports bytes; macOS reports bytes; normalize where supported.
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result['peak_rss_bytes'] = int(usage * 1024) if sys.platform.startswith('linux') else int(usage)
        result['initial_rss_bytes'] = rss
        result['incremental_rss_peak_bytes'] = max(0, result['peak_rss_bytes'] - rss)
        return result


def _summarize(records):
    names = sorted({x['stage'] for r in records for x in r['stages']})
    by_stage = {}
    for name in names:
        vals = [x['seconds'] for r in records for x in r['stages'] if x['stage'] == name]
        by_stage[name] = {'median_seconds': round(statistics.median(vals), 6),
                          'max_seconds': round(max(vals), 6),
                          'min_seconds': round(min(vals), 6),
                          'repetitions': len(vals)}
    return {'stages': by_stage,
            'median_peak_rss_bytes': int(statistics.median(r['peak_rss_bytes'] for r in records)),
            'max_peak_rss_bytes': max(r['peak_rss_bytes'] for r in records),
            'max_rss_above_baseline_bytes': max(r['incremental_rss_peak_bytes'] for r in records)}



def enforce_budgets(report: dict, *, max_peak_rss_mib: float | None = None,
                    max_model_seconds: float | None = None,
                    baseline: dict | None = None,
                    max_regression_factor: float = 1.6) -> dict:
    """Fail closed for over-budget memory/fit time and comparable regressions.

    Both comparisons only use measured samples (no extrapolation). A prior
    report can only serve as a numeric speed baseline on the same environment.
    """
    failures: list[str] = []
    blocked: list[str] = []
    if max_peak_rss_mib is not None and max_peak_rss_mib <= 0:
        raise ValueError('内存预算必须为正数')
    if max_model_seconds is not None and max_model_seconds <= 0:
        raise ValueError('模型预算必须为正数')
    if not 1 <= max_regression_factor <= 20:
        raise ValueError('性能退化因子范围为 [1,20]')
    if baseline is not None:
        host = report['host']; older = baseline.get('host', {})
        if any(host.get(k) != older.get(k) for k in ('python', 'platform', 'cpus_logical')):
            blocked.append('性能历史对照机器/Python 不同，禁止直接比较加速或退化百分比')
        if report.get('dataset_type') != baseline.get('dataset_type') or report['run_settings']['seed'] != baseline.get('run_settings', {}).get('seed'):
            blocked.append('性能历史对照数据来源或随机种子不同')
    comparison = {}
    for key, group in report.get('scales', {}).items():
        if 'summary' not in group:
            continue
        summary = group['summary']
        peak = summary['max_peak_rss_bytes'] / 1024**2
        fit = summary['stages'].get('cross_time_and_device_fit', {}).get('median_seconds')
        if max_peak_rss_mib is not None and peak > max_peak_rss_mib:
            failures.append(f'{key} 行进程 RSS 峰值 {peak:.1f} MiB 超过 {max_peak_rss_mib:.1f} MiB')
        if max_model_seconds is not None and fit is not None and fit > max_model_seconds:
            failures.append(f'{key} 行模型评价 {fit:.3f} 秒超过 {max_model_seconds:.3f} 秒')
        if baseline is None or blocked:
            continue
        previous = baseline.get('scales', {}).get(key, {}).get('summary')
        if not previous:
            blocked.append(f'历史对照缺少相同规模 {key} 的真实测量')
            continue
        ratio = {}
        for stage, now in summary['stages'].items():
            old = previous.get('stages', {}).get(stage)
            if old is None or old.get('median_seconds', 0) <= 0:
                blocked.append(f'{key}/{stage} 历史阶段数据缺失')
                continue
            speed = now['median_seconds'] / old['median_seconds']
            ratio[stage] = round(speed, 4)
            if speed > max_regression_factor:
                failures.append(f'{key}/{stage} 性能耗时退化 {speed:.2f}x (上限 {max_regression_factor:.2f}x)')
        old_rss = previous.get('max_peak_rss_bytes', 0)
        if old_rss <= 0:
            blocked.append(f'{key} 历史 RSS 缺失')
        else:
            memory_ratio = summary['max_peak_rss_bytes'] / old_rss
            ratio['rss'] = round(memory_ratio, 4)
            if memory_ratio > max_regression_factor:
                failures.append(f'{key} 内存 RSS 退化 {memory_ratio:.2f}x')
        comparison[key] = ratio
    report['performance_budgets'] = {
        'max_peak_rss_mib': max_peak_rss_mib,
        'max_model_seconds': max_model_seconds,
        'max_regression_factor': max_regression_factor,
        'comparison_same_environment': baseline is not None and not bool(blocked),
        'ratios': comparison,
        'failures': failures, 'blocked': blocked,
    }
    if failures:
        report['status'] = 'failed'
    elif blocked and report['status'] == 'passed':
        report['status'] = 'blocked'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', nargs='+', type=int, default=[10_000, 50_000, 200_000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--seed', type=int, default=3700)
    parser.add_argument('--real-csv', type=Path)
    parser.add_argument('--require-physical', action='store_true')
    parser.add_argument('--max-peak-rss-mib', type=float, help='Fail if observed process peak RSS exceeds this budget')
    parser.add_argument('--max-model-seconds', type=float, help='Fail if observed median evaluation time exceeds this budget')
    parser.add_argument('--baseline-report', type=Path, help='Historical numeric baseline from same host, Python and data')
    parser.add_argument('--max-regression-factor', type=float, default=1.6)
    parser.add_argument('--out', type=Path, default=Path('reports/v0370/performance.json'))
    parser.add_argument('--worker', action='store_true')
    a = parser.parse_args(argv)
    if a.worker:
        result = _worker(a.rows[0], a.real_csv, a.seed, a.require_physical)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'passed' else 2
    if not 1 <= a.repeats <= 10 or any(not 1000 <= n <= 2_000_000 for n in a.rows):
        parser.error('rows: 1000..2000000; repeats: 1..10')
    import psutil
    report = {
        'version': '0.37.0', 'status': 'passed',
        'dataset_type': 'real' if a.real_csv else 'synthetic',
        'host': {'python': sys.version.split()[0], 'platform': platform.platform(),
                 'processor': platform.processor(), 'cpus_logical': psutil.cpu_count(),
                 'memory_total_bytes': psutil.virtual_memory().total},
        'run_settings': {'rows': a.rows, 'repeats': a.repeats, 'seed': a.seed,
                         'physical_required': a.require_physical}, 'scales': {},
    }
    for n in ([-1] if a.real_csv else a.rows):
        items = []
        for repeat in range(a.repeats):
            arguments = [sys.executable, str(Path(__file__).resolve()),
                         '--worker', '--rows', str(n if n > 0 else 1000),
                         '--seed', str(a.seed)]
            if a.real_csv:
                arguments.extend(['--real-csv', str(a.real_csv.resolve())])
            if a.require_physical:
                arguments.append('--require-physical')
            start = time.perf_counter()
            proc = subprocess.run(arguments, text=True, capture_output=True, timeout=420)
            # Exit code 2 can mean a truthful *partial* model evaluation
            # (e.g. a tiny sample has too few held-out device records). Such
            # a run is measurable but cannot satisfy release, and must not be
            # misclassified as an opaque worker crash with an empty traceback.
            partial = None
            if proc.returncode == 2 and proc.stdout.strip():
                try:
                    candidate = json.loads(proc.stdout)
                    if candidate.get('status') == 'partial':
                        partial = candidate
                except (ValueError, TypeError):
                    pass
            if proc.returncode and partial is None:
                report['status'] = 'blocked' if 'DuckDB/Arrow' in proc.stderr else 'failed'
                report['scales'][str(n)] = {'error': proc.stderr[-1000:],
                                            'stdout': proc.stdout[-600:], 'returncode': proc.returncode}
                break
            item = partial if partial is not None else json.loads(proc.stdout)
            item['full_process_seconds'] = round(time.perf_counter() - start, 6)
            items.append(item)
        if items:
            if len(set(x['csv_sha256'] for x in items)) != 1:
                report['status'] = 'failed'
                raise AssertionError('重复基准中的输入数据指纹发生变化')
            report['scales'][str(n)] = {
                'summary': _summarize(items),
                'csv_sha256': items[0]['csv_sha256'],
                'runs': items,
            }
            if any(x['status'] != 'passed' for x in items):
                report['status'] = 'partial'
    baseline = json.loads(a.baseline_report.read_text(encoding='utf-8')) if a.baseline_report else None
    enforce_budgets(report, max_peak_rss_mib=a.max_peak_rss_mib,
                    max_model_seconds=a.max_model_seconds,
                    baseline=baseline, max_regression_factor=a.max_regression_factor)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'out': str(a.out),
                      'scales': {k: v.get('summary', v.get('error')) for k, v in report['scales'].items()}},
                     ensure_ascii=False, indent=2))
    return {'passed': 0, 'partial': 2, 'blocked': 2, 'failed': 1}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
