"""Real DuckDB/Parquet benchmark with repetitions and honest p50/p95 evidence.

No mock BI services, no unsafe default writes into the user's runtime.
Run on the target machine with its installed Python/DuckDB:
    python scripts/benchmark_native_bi.py --rows 100000 1000000 10000000

Writes machine-local timings, query correctness, peak process RSS, test file
sizes and concurrent query observations to a JSON file. Fail-closed when the
real engine is missing; thresholds are explicit optional command arguments.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import sys
import time
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def rss_mb():
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kibibytes.
    return round(rss/(1048576 if sys.platform=='darwin' else 1024),1)


def distribution(values):
    ordered=sorted(values)
    n=len(ordered)
    return {'runs':n,'p50_ms':round(statistics.median(values)*1000,2),
            'p95_ms':round(ordered[min(n-1,max(0,int(n*.95+.999)-1))]*1000,2),
            'max_ms':round(max(values)*1000,2)}


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--rows',nargs='+',type=int,default=[100000,1000000,10000000])
    p.add_argument('--repeats',type=int,default=5)
    p.add_argument('--concurrency',type=int,default=4)
    p.add_argument('--max-query-p95-ms',type=float,default=None)
    p.add_argument('--keep',action='store_true')
    p.add_argument('--out',type=Path,default=ROOT/'reports'/'production_native_bi_benchmark.json')
    args=p.parse_args()
    try:
        import duckdb
    except ImportError:
        report={'status':'blocked','blocker':'DuckDB binary not installed in the active Python',
                'python':sys.version.split()[0], 'requested_rows':args.rows,
                'instruction':'Install existing requirements.txt in the target Python 3.12 environment; rerun without mock data services.'}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('[BLOCKED] DuckDB not installed. Report:',args.out.resolve(),file=sys.stderr)
        return 2
    if not all(10000<=n<=100000000 for n in args.rows):
        raise ValueError('Each row count must be 10000..100000000')
    if not 2<=args.repeats<=20 or not 1<=args.concurrency<=16:
        raise ValueError('2 <= repeats <= 20 and 1 <= concurrency <= 16 required')
    from timepilot.api import create_app
    from timepilot.native_bi import QuerySpec, FilterSpec, MetricSpec
    report={'status':'running','environment':{
        'python':sys.version.split()[0], 'duckdb':duckdb.__version__,
        'platform':platform.platform(), 'cpu_count':os.cpu_count(),
        'memory_peak_rss_mb_initial':rss_mb()},'tests':[],
        'metric_note':'Observed local timings, not absolute hardware-independent SLOs',
        'threshold_max_query_p95_ms':args.max_query_p95_ms}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='timepilot_real_bench_')).resolve()
    started=time.perf_counter()
    try:
        for n in args.rows:
            path=folder/f'generated_{n}.parquet'
            con=duckdb.connect()
            t0=time.perf_counter()
            # 20 devices, timestamps and mixed numerical/categorical source.
            sql=(f"COPY (SELECT i AS id, 'Device_' || CAST(i%20 AS VARCHAR) AS device_id, "
                 f"CAST((i%1000)*0.1 AS DECIMAL(18,2)) AS temperature, "
                 f"TIMESTAMP '2025-01-01 00:00:00' + CAST(i/20 AS BIGINT)*INTERVAL '1 minute' AS observed_at, "
                 f"CASE WHEN i%90173=0 THEN 'maintenance' ELSE '' END AS maintenance_event, "
                 f"CASE WHEN i%36000<18000 THEN 'production' ELSE 'idle' END AS regime "
                 f"FROM range({n}) t(i)) TO '{str(path)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            con.execute(sql)
            generate_s=round(time.perf_counter()-t0,3);con.close()
            app=create_app(home=folder/f'run_{n}',embedded_worker=False)
            engine=app.state.native_bi
            t1=time.perf_counter()
            asset=engine.import_from_path(path,name=f'bench_{n}',lineage={'type':'industrial_benchmark','rows':n})
            import_s=round(time.perf_counter()-t1,3)
            assert asset['row_count']==n
            spec_filter=QuerySpec(asset_id=asset['asset_id'],columns=['device_id','temperature'],
                filters=[FilterSpec(column='device_id',op='eq',value='Device_1')],limit=100)
            spec_group=QuerySpec(asset_id=asset['asset_id'],group_by=['device_id'],
                metrics=[MetricSpec(operation='sum',column='temperature',alias='total_temperature')],limit=100)
            def check_filter():
                r=engine.query(spec_filter)
                assert len(r['rows'])<=100
                assert all(x['device_id']=='Device_1' for x in r['rows'])
            def check_group():
                r=engine.query(spec_group)
                assert r['returned']==20
                assert all(float(x['total_temperature'])>=0 for x in r['rows'])
            def measure(call):
                call()  # discard first warm-up
                times=[]
                for _ in range(args.repeats):
                    tick=time.perf_counter();call();times.append(time.perf_counter()-tick)
                return distribution(times)
            filtered=measure(check_filter)
            grouped=measure(check_group)
            def query(i):
                tick=time.perf_counter();
                (check_filter if i%2 else check_group)()
                return time.perf_counter()-tick
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                concurrent=distribution(list(pool.map(query,range(args.concurrency*args.repeats))))
            outcome={'rows':n,'parquet_mb':round(path.stat().st_size/1048576,2),
                     'generate_seconds':generate_s,'native_import_seconds':import_s,
                     'filter':filtered,'aggregate':grouped,'concurrent_requests':concurrent,
                     'concurrency':args.concurrency,'peak_process_rss_mb':rss_mb(),
                     'file_integrity':engine.asset(asset['asset_id'])['sha256']==asset['sha256']}
            assert outcome['file_integrity'],'Native BI registration integrity changed'
            if args.max_query_p95_ms is not None:
                outcome['threshold_pass']=max(filtered['p95_ms'],grouped['p95_ms'],
                                              concurrent['p95_ms'])<=args.max_query_p95_ms
            report['tests'].append(outcome)
            args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(outcome,ensure_ascii=False),flush=True)
        report['status']='passed' if all(x.get('threshold_pass',True) for x in report['tests']) else 'failed'
    except Exception as exc:
        report['status']='failed';report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['elapsed_seconds']=round(time.perf_counter()-started,3)
        report['environment']['memory_peak_rss_mb_final']=rss_mb()
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if not args.keep:shutil.rmtree(folder,ignore_errors=True)
    print('Result:',args.out.resolve())
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
