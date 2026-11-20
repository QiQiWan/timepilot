"""Strict physical DuckDB/Parquet performance receipts for 10k/100k/1M.

Records genuine query samples, p50/p95 and process memory. This is a SQL
microbenchmark on deterministic synthetic data, not a substitute for the
full FastAPI + Perspective + industrial MES end-to-end acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def percentile_nearest(values, pct):
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * pct) - 1] if ordered else None


def peak_rss_mib():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 * 1024 if sys.platform == 'darwin' else 1024)
    except ImportError:
        try:
            import psutil
            peak = getattr(psutil.Process().memory_info(), 'peak_wset', None)
            return peak / 1024**2 if peak is not None else None
        except ImportError:
            return None


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--out', default='reports/v0381/performance.json')
    parser.add_argument('--rows', nargs='+', type=int, default=[10000,100000,1000000])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--max-query-p95-seconds', type=float, default=60)
    parser.add_argument('--max-rss-mib', type=float, default=3072)
    parser.add_argument('--require-target', action='store_true')
    args=parser.parse_args(argv)
    out=Path(args.out)
    if not out.is_absolute():out=ROOT/out
    out.parent.mkdir(parents=True, exist_ok=True)
    report={
        'version':'0.38.1','at':datetime.now(timezone.utc).isoformat(),
        'source_type':'deterministic_synthetic_duckdb_generated_parquet',
        'scope':'physical_sql_microbenchmark_only',
        'python':sys.version.split()[0],'platform':platform.platform(),
        'rows':args.rows,'repeats':args.repeats,
        'budget':{'max_query_p95_seconds':args.max_query_p95_seconds,'max_peak_rss_mib':args.max_rss_mib},
        'status':'blocked','benchmarks':[]}
    code=2
    missing=[]
    if importlib.util.find_spec('duckdb') is None:missing.append('duckdb')
    if importlib.util.find_spec('pyarrow') is None:missing.append('pyarrow')
    if args.require_target and sys.version_info[:3]!=(3,12,12):missing.append('CPython_3.12.12')
    if missing:
        report['missing']=missing
    elif args.repeats<3 or any(x<1 or x>2_000_000 for x in args.rows) or args.max_rss_mib<128:
        report.update(status='failed',reason='invalid benchmark parameters');code=1
    else:
        import duckdb
        report['duckdb_version']=duckdb.__version__
        report['status']='passed';code=0
        with tempfile.TemporaryDirectory(prefix='tp-v0381-physical-') as workspace:
            for n in args.rows:
                raw_path=Path(workspace)/f'physical_{n}.parquet'
                quoted=str(raw_path).replace("'", "''")
                samples={key:[] for key in ('profile','categories','distribution','correlation')}
                with duckdb.connect(':memory:') as db:
                    db.execute(f'''COPY (SELECT i::BIGINT AS row_id, (i % 8)::INTEGER AS device,
                        CASE WHEN i%37=0 THEN NULL ELSE (i%97)*.1 END AS measurement,
                        (20 + sin(i*.001)*7)::DOUBLE AS temperature
                        FROM range({int(n)}) AS t(i)) TO '{quoted}' (FORMAT PARQUET)''')
                    queries={
                        'profile':'''SELECT COUNT(*), COUNT(measurement),COUNT(DISTINCT device),
                            AVG(measurement),QUANTILE_CONT(measurement,.25),MEDIAN(measurement),
                            QUANTILE_CONT(measurement,.75),CORR(measurement,temperature)
                            FROM read_parquet(?)''',
                        'categories':'''SELECT device,COUNT(*),AVG(measurement) FROM read_parquet(?)
                            GROUP BY device ORDER BY device''',
                        'distribution':'''SELECT FLOOR(temperature/2.0)::INTEGER bucket, COUNT(*)
                            FROM read_parquet(?) GROUP BY bucket ORDER BY bucket''',
                        'correlation':'''SELECT device,COUNT(measurement),CORR(measurement,temperature)
                            FROM read_parquet(?) GROUP BY device ORDER BY device'''
                    }
                    references={}
                    for _ in range(args.repeats):
                        for kind,query in queries.items():
                            begin=time.perf_counter()
                            answer=db.execute(query,[str(raw_path)]).fetchall()
                            seconds=time.perf_counter()-begin
                            samples[kind].append(seconds)
                            if kind in references and references[kind]!=answer:
                                report.update(status='failed',reason=f'nonreproducible_{kind}');code=1
                            references.setdefault(kind,answer)
                    if int(references['profile'][0][0]) != n or len(references['categories'])!=8:
                        report.update(status='failed',reason='synthetic physical row mismatch');code=1
                rss=peak_rss_mib()
                result={'rows':n,'parquet_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                        'parquet_bytes':raw_path.stat().st_size,'peak_rss_mib':rss,
                        'query_samples':{kind:{'samples_seconds':xs,
                           'p50_seconds':statistics.median(xs),'p95_seconds':percentile_nearest(xs,.95)}
                           for kind,xs in samples.items()}}
                result['budget_pass']=rss is not None and rss<=args.max_rss_mib and all(
                    percentile_nearest(xs,.95) <= args.max_query_p95_seconds for xs in samples.values())
                if not result['budget_pass']:
                    report.update(status='failed',reason='performance_budget_exceeded_or_unmeasured');code=1
                report['benchmarks'].append(result)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'out':str(out)},ensure_ascii=False))
    return code


if __name__ == '__main__':raise SystemExit(main())
