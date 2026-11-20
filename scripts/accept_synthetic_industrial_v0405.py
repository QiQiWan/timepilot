"""v0.40.5 measured synthetic industrial acceptance.

Never reports DuckDB, PyArrow, Perspective or Mac gates as passed when absent.
No Python environments, browsers or dependencies are downloaded.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.model_prediction_query import _csv_query, _duckdb_query, _filters
from timepilot.synthetic_industrial import generate


def independent_reference(path, f):
    """Independent CSV scan and raw residual calculation, for query parity."""
    matches = []
    with Path(path).open('r', encoding='utf-8', newline='') as stream:
        for rec in csv.DictReader(stream):
            if f['model'] is not None and rec['model'] != f['model']:
                continue
            if f['fold'] is not None and int(rec['fold']) != f['fold']:
                continue
            if f['month'] is not None and rec['month'] != f['month']:
                continue
            if f['device'] is not None and rec['series_id'] != f['device']:
                continue
            ae = abs(float(rec['prediction']) - float(rec['actual']))
            if f['min_abs_error'] is not None and ae + 1e-7 < f['min_abs_error']:
                continue
            matches.append(rec)
    n = len(matches)
    mae = sum(abs(float(x['prediction']) - float(x['actual'])) for x in matches) / n if n else None
    rmse = math.sqrt(sum((float(x['prediction']) - float(x['actual'])) ** 2 for x in matches) / n) if n else None
    return {
        'rows': n, 'mae': mae, 'rmse': rmse,
        'ids': [int(x['__tp_rowid']) for x in matches[f['offset']:f['offset'] + f['limit']]],
    }


def measure_one(path, f):
    began = time.perf_counter()
    rows, metrics, backend = _csv_query(path, f)
    return {
        'seconds': time.perf_counter() - began,
        'rows': metrics['prediction_rows'],
        'page_ids': [int(x['__tp_rowid']) for x in rows],
        'mae': metrics['mae'], 'rmse': metrics['rmse'], 'backend': backend,
    }


def _quantile(numbers, fraction):
    ordered = sorted(numbers)
    return round(ordered[int(math.ceil((len(ordered) - 1) * fraction))], 4)


def run(*, rows=20000, seed=43, repeats=3, workers=2, output_dir=None,
        native_mode='auto', audit_rows=5000):
    if not 1 <= repeats <= 10 or not 1 <= workers <= 8:
        raise ValueError('Invalid bounded repeat or worker count')
    if native_mode not in ('auto', 'require'):
        raise ValueError('native_mode must be auto or require')
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ('duckdb', 'pyarrow', 'perspective')}
    report = {
        'version': '0.40.5', 'date_utc': datetime.now(timezone.utc).isoformat(),
        'environment': {'os': platform.system(), 'python': platform.python_version(),
                        'native_dependencies': modules},
        'suite': 'synthetic_predictions_and_point_in_time_audit',
        'data_origin': 'synthetic_deterministic_not_business_observations',
        'candidate_predictions_are_simulated_not_fitted': True,
        'status': 'in_progress', 'cases': [],
    }
    try:
        import psutil
        process = psutil.Process()
    except ImportError:
        process = None

    with tempfile.TemporaryDirectory(prefix='timepilot-industrial-') if output_dir is None else _nullcontext(output_dir) as folder:
        directory = Path(folder)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = generate(directory, rows=rows, seed=seed, devices=8,
                            step_minutes=15, scenario='clean')
        report['manifest'] = manifest
        path = directory / 'validation_predictions.csv'
        report['files_bytes'] = {p.name: p.stat().st_size for p in directory.iterdir() if p.is_file()}
        assert manifest['future_x_rows'] == 0 and manifest['invalid_label_rows'] == 0

        # Check real point-in-time audit and chronological fold generation against
        # a bounded sample from the same source, without claiming a million-row
        # pandas audit when audit_rows < rows.
        if audit_rows >= 50:
            import pandas as pd
            from timepilot.model_task_spec import ModelTaskInput
            from timepilot.model_task_time_audit import audit_availability, make_temporal_folds
            sampled = pd.read_csv(directory / 'industrial_telemetry.csv', nrows=min(rows, audit_rows))
            spec = ModelTaskInput.model_validate({
                'name': 'Synthetic next-temperature', 'asset_id': 'synthetic',
                'task_kind': 'tabular_regression',
                'target': {'column': 'target_next_c'},
                'features': [
                    {'column': 'temperature_c', 'available_at_column': 'x_available_at_utc'},
                    {'column': 'vibration_mm_s', 'available_at_column': 'x_available_at_utc'},
                ],
                'time': {'event_time_column': 'event_time_utc',
                         'decision_time_column': 'decision_time_utc',
                         'label_available_at_column': 'label_ready_at_utc', 'timezone': 'UTC'},
                'validation': {'kind': 'rolling_origin', 'folds': 3, 'gap': 2},
            })
            ledger, event, decision, labels = audit_availability(sampled, spec)
            folds = make_temporal_folds(sampled, spec, event=event, label_available=labels)
            assert len(folds) == 3 and ledger['status'] == 'checked'
            for train, val, cutoff, note in folds:
                assert len(train) and len(val)
                assert event.iloc[train].max() < event.iloc[val].min()
                assert (labels.iloc[train] <= cutoff).all()
                assert not set(train).intersection(val)
            report['point_in_time'] = {'audited_rows': len(sampled),
                                        'label_maturity_checked': True,
                                        'folds': len(folds),
                                        'real_time_audit_executed': True,
                                        'ledger_sha256': ledger['ledger_sha256']}

        queries = [
            {'model': 'forest', 'fold': 2, 'device': 'SMT-06',
             'min_abs_error': 0.3, 'offset': 5, 'limit': 20},
            {'model': 'linear', 'fold': 1, 'offset': 17, 'limit': 30},
            {'month': '2026-01', 'device': 'SMT-02', 'offset': 3, 'limit': 18},
            {'model': 'unknown', 'offset': 0, 'limit': 5},
        ]
        filters = [_filters(**item) for item in queries]
        for f in filters:
            oracle = independent_reference(path, f)
            actual = measure_one(path, f)
            assert oracle['rows'] == actual['rows'], (oracle, actual)
            assert oracle['ids'] == actual['page_ids'], (oracle, actual)
            if oracle['rows']:
                assert math.isclose(oracle['mae'], actual['mae'], abs_tol=1e-7)
                assert math.isclose(oracle['rmse'], actual['rmse'], abs_tol=1e-7)
            report['cases'].append({'filters': f, 'expected_rows': oracle['rows'],
                                    'query_seconds': round(actual['seconds'], 4),
                                    'mae': actual['mae'], 'rmse': actual['rmse'],
                                    'page_ids': actual['page_ids'], 'status': 'passed'})

        # Repeated latency is measured, not inferred from a single query.
        probe = filters[0]
        sequential = [measure_one(path, probe) for _ in range(repeats)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            concurrent = list(pool.map(lambda _: measure_one(path, probe), range(workers)))
        assert len({(q['rows'], tuple(q['page_ids']), q['mae'], q['rmse'])
                    for q in sequential + concurrent}) == 1
        report['performance'] = {
            'repetitions': repeats, 'simultaneous_queries': workers,
            'sequential_seconds': [round(x['seconds'], 4) for x in sequential],
            'concurrent_seconds': [round(x['seconds'], 4) for x in concurrent],
            'sequential_p50_seconds': round(statistics.median(x['seconds'] for x in sequential), 4),
            'sequential_p95_nearest_rank_seconds': _quantile([x['seconds'] for x in sequential], 0.95),
            'concurrent_p95_nearest_rank_seconds': _quantile([x['seconds'] for x in concurrent], 0.95),
            'process_rss_bytes_at_end': process.memory_info().rss if process else None,
            'latency_interpretation': 'wall-clock repeated queries; no guaranteed production SLA',
        }
        if modules['duckdb'] and modules['pyarrow']:
            import duckdb
            parquet = directory / 'validation_predictions.parquet'
            with duckdb.connect(':memory:') as db:
                db.execute('SET threads=1')
                # Server-selected paths; this script never consumes user SQL.
                src = str(path).replace("'", "''")
                dest = str(parquet).replace("'", "''")
                db.execute(f"COPY (SELECT * FROM read_csv_auto('{src}', header=true)) TO '{dest}' "
                           '(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)')
            for f, case in zip(filters, report['cases']):
                items, metrics, engine = _duckdb_query(parquet, True, f)
                assert engine == 'duckdb_parquet_pushdown'
                assert [int(x['__tp_rowid']) for x in items] == case['page_ids']
                assert metrics['prediction_rows'] == case['expected_rows']
                if metrics['prediction_rows']:
                    assert math.isclose(metrics['mae'], case['mae'], abs_tol=1e-7)
                    assert math.isclose(metrics['rmse'], case['rmse'], abs_tol=1e-7)
            report['native_parquet'] = {'status': 'passed', 'parquet_bytes': parquet.stat().st_size,
                                        'backend': 'real_duckdb_parquet'}
        else:
            report['native_parquet'] = {'status': 'blocked',
                'missing': [x for x in ('duckdb', 'pyarrow') if not modules[x]]}
        report['status'] = 'passed_source_synthetic'
        if native_mode == 'require' and report['native_parquet']['status'] != 'passed':
            report['status'] = 'blocked_native_required'
    return report


class _nullcontext:
    def __init__(self, path):
        self.path = path
    def __enter__(self):
        return self.path
    def __exit__(self, *_args):
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--rows', type=int, default=20000)
    p.add_argument('--seed', type=int, default=43)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--audit-rows', type=int, default=5000)
    p.add_argument('--data-dir', type=Path)
    p.add_argument('--native-mode', choices=['auto', 'require'], default='auto')
    p.add_argument('--out', type=Path, default=Path('reports/v0405/synthetic_acceptance.json'))
    args = p.parse_args()
    result = run(rows=args.rows, seed=args.seed, repeats=args.repeats,
                 workers=args.workers, audit_rows=args.audit_rows,
                 output_dir=args.data_dir, native_mode=args.native_mode)
    out = args.out if args.out.is_absolute() else ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'source_observations': result['manifest']['rows'],
                      'prediction_rows': result['manifest']['prediction_rows'],
                      'p50_seconds': result['performance']['sequential_p50_seconds'],
                      'native_parquet': result['native_parquet']['status'],
                      'receipt': str(out)}, ensure_ascii=False))
    return 2 if result['status'] == 'blocked_native_required' else 0


if __name__ == '__main__':
    raise SystemExit(main())
