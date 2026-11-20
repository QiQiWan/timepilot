#!/usr/bin/env python3
"""v0.36.0 physical DuckDB/Parquet performance and numerical consistency QA.

This creates isolated, deterministic SYNTHETIC stress data in a temp directory.
It does not read/write a user's production database and is NOT an industrial
accuracy test. Values are checked against an independent SQL query over Parquet.
No dependency installation, browser download or temporary Python environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as file:
        for data in iter(lambda: file.read(1 << 20), b''):
            h.update(data)
    return h.hexdigest()


def _rss_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 1)
    except ImportError:
        return None


def run_one(size: int, dest: Path, *, max_seconds: float, max_rss_mb: int) -> dict:
    import duckdb
    from timepilot.feature_chart_semantics import series_aggregate

    start = time.perf_counter()
    path = dest / f'synthetic_{size}.parquet'
    con = duckdb.connect(':memory:')
    con.execute("PRAGMA threads=2")
    con.execute(f"PRAGMA memory_limit='{max_rss_mb}MB'")
    # Size is validated as an integer before SQL construction; literal values
    # avoid driver-specific parameter binding in CREATE TABLE / range().
    con.execute(f"""CREATE TABLE synthetic AS SELECT i::BIGINT AS __tp_rowid,
         ('equipment_' || CAST((i%4)+1 AS VARCHAR)) AS device,
         (TIMESTAMP '2026-01-01' + i*INTERVAL '1 second') AS observed_at,
         CASE WHEN i={size - 1} THEN 1000000.0 ELSE (i%37)::DOUBLE END AS measurement
         FROM range({size}) t(i)""")
    quoted = "'" + str(path).replace("'", "''") + "'"
    con.execute(f"COPY synthetic TO {quoted} (FORMAT PARQUET)")
    con.execute(f"CREATE VIEW from_physical AS SELECT * FROM read_parquet({quoted})")
    # The last synthetic row has an explicit late extreme. Verify that the
    # corresponding device retains it even when rendered in only 120 buckets.
    selected = f'equipment_{((size - 1) % 4) + 1}'
    result = series_aggregate(con, 'from_physical', order_column='observed_at',
        value_expression='measurement', original_expression='measurement',
        entity_column='device', entity_value=selected, resolution=120)
    raw = con.execute("""SELECT COUNT(*),MIN(measurement),MAX(measurement),SUM(measurement)
        FROM from_physical WHERE device=?""", [selected]).fetchone()
    selected_rows, minimum, maximum, summation = raw
    count = sum(int(point['count']) for point in result['points'])
    if count != selected_rows or result['population_count'] != selected_rows:
        raise AssertionError('chart population differs from independent Parquet SQL')
    if abs(sum(float(point['value']) * point['count'] for point in result['points']) - summation) > 0.000001 * max(1, abs(summation)):
        raise AssertionError('chart bucket means differ from full-source Parquet sum')
    if max(float(point['max']) for point in result['points']) != maximum or minimum != min(float(point['min']) for point in result['points']):
        raise AssertionError('min/max were lost during time bucket aggregation')
    if maximum != 1000000.0:
        raise AssertionError('late peak was not included in the selected device')
    hashed = _sha(path)
    elapsed = round(time.perf_counter() - start, 3)
    memory = _rss_mb()
    peak_memory = None
    try:
        import resource
        # macOS reports bytes, Linux reports KiB for ru_maxrss.
        maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_memory = round(maximum/(1048576 if sys.platform == 'darwin' else 1024), 1)
    except ImportError:
        pass
    observed = peak_memory if peak_memory is not None else memory
    if elapsed > max_seconds:
        raise AssertionError(f'{size} rows exceeded {max_seconds}s budget ({elapsed}s)')
    if observed is not None and observed > max_rss_mb:
        raise AssertionError(f'{size} rows exceeded {max_rss_mb}MB RSS budget ({observed}MB)')
    return {'status':'passed', 'input_type':'deterministic_synthetic_stress_only',
        'rows':size,'selected_device':selected,'selected_rows':selected_rows,
        'chart_points':len(result['points']),'peak_preserved':True,
        'aggregate_matched_independent_sql':True, 'elapsed_seconds':elapsed,
        'rss_mb_observed':memory,'process_peak_rss_mb':peak_memory,
        'file_bytes':path.stat().st_size,
        'parquet_sha256':hashed,'max_seconds':max_seconds,'max_rss_mb':max_rss_mb}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='TimePilot physical Parquet performance QA (synthetic stress only)')
    parser.add_argument('--sizes',default='10000,100000,1000000',help='Comma-separated row counts')
    parser.add_argument('--max-seconds-per-size',type=float,default=180)
    parser.add_argument('--max-rss-mb',type=int,default=2048)
    parser.add_argument('--out',type=Path,default=Path('reports/featurelab360/performance.json'))
    args=parser.parse_args(argv)
    values=[int(t) for t in args.sizes.split(',') if t.strip()]
    if not values or len(set(values)) != len(values) or min(values) < 1000 or max(values) > 2_000_000:
        parser.error('sizes must be unique, 1,000 to 2,000,000 rows each')
    if args.max_rss_mb < 256 or args.max_seconds_per_size <= 0:
        parser.error('invalid resource budget')
    try:
        import duckdb
        import pyarrow  # noqa: F401 - ensure physical parquet dependency
    except ImportError as exc:
        report={'status':'blocked','reason':'physical dependency missing: '+str(exc),'cases':[]}
        code=2
    else:
        cases=[]
        with tempfile.TemporaryDirectory(prefix='timepilot360_physical_') as dirname:
            for size in values:
                try:
                    cases.append(run_one(size, Path(dirname),max_seconds=args.max_seconds_per_size,
                                         max_rss_mb=args.max_rss_mb))
                except Exception as exc:
                    cases.append({'status':'failed','rows':size,'error':type(exc).__name__+': '+str(exc)[:1000]})
        report={'status':'passed' if all(x['status']=='passed' for x in cases) else 'failed',
                'cases':cases,'database':'DuckDB physical Parquet (not an in-memory chart fixture)',
                'business_generalization_proven':False}
        code=0 if report['status']=='passed' else 1
    out=args.out.expanduser().resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'cases':len(report['cases']),
                      'out':str(out)},ensure_ascii=False))
    return code


if __name__=='__main__': raise SystemExit(main())
