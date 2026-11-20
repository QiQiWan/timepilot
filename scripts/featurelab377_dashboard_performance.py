"""Real DuckDB/Parquet performance and peak RSS gate; no simulated SQL pass."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,sys,tempfile,time,statistics
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]

def peak_rss_mib():
    try:
        import resource
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss/(1024*1024 if sys.platform=='darwin' else 1024)
    except ImportError:
        return None  # Unsupported platform: do not claim memory-budget pass

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--rows',type=int,nargs='+',default=[506,10000,100000,1000000])
    p.add_argument('--repeats',type=int,default=2)
    p.add_argument('--max-peak-rss-mib',type=float,default=3072)
    p.add_argument('--max-dashboard-seconds',type=float,default=60)
    p.add_argument('--out',default='reports/v0377/dashboard_perf.json')
    a=p.parse_args(argv)
    out=Path(a.out);out=out if out.is_absolute() else ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True)
    report={'version':'0.37.7','at':datetime.now(timezone.utc).isoformat(),
        'python':sys.version.split()[0],'status':'blocked','engine':'real DuckDB',
        'parquet':True,'requested_rows':a.rows,'benchmarks':[],
        'budget':{'peak_rss_mib':a.max_peak_rss_mib,'query_seconds':a.max_dashboard_seconds}}
    if importlib.util.find_spec('duckdb') is None:
        report['reason']='DuckDB missing';code=2
    elif a.repeats<1 or any(n<1 or n>5_000_000 for n in a.rows):
        report.update(status='failed',reason='Invalid row budget');code=1
    else:
        import duckdb
        code=0;report['status']='passed'
        with tempfile.TemporaryDirectory(prefix='tp-dash-perf-') as tmp:
            for rows in a.rows:
                path=Path(tmp)/f'{rows}.parquet'
                with duckdb.connect(':memory:') as db:
                    # Generating in DuckDB avoids memory-heavy Python row lists.
                    escaped=str(path).replace("'","''")
                    db.execute(f"""COPY (SELECT i AS row_id,(i%6)::INTEGER AS device,
                        CASE WHEN i%101=0 THEN NULL ELSE (i%97)*.1+(i%5)*.5 END AS measurement,
                        SIN(i*.001)::DOUBLE AS temperature FROM range({int(rows)}) t(i))
                        TO '{escaped}' (FORMAT PARQUET)""")
                    scans=[];checks=[]
                    for _ in range(a.repeats):
                        start=time.perf_counter()
                        result=db.execute(f"""SELECT COUNT(*),AVG(measurement),
                            QUANTILE_CONT(measurement,.25),MEDIAN(measurement),QUANTILE_CONT(measurement,.75),
                            CORR(measurement,temperature),COUNT(DISTINCT device)
                            FROM read_parquet('{escaped}')""").fetchone()
                        elapsed=time.perf_counter()-start
                        scans.append(elapsed);checks.append(result)
                    if any(x!=checks[0] for x in checks):
                        report['status']='failed';code=1
                    if result[0]!=rows or result[-1]!=min(rows,6):
                        report['status']='failed';code=1
                    peak=peak_rss_mib()
                    item={'rows':rows,'seconds':scans,'median_seconds':statistics.median(scans),
                        'peak_rss_mib':round(peak,3) if peak is not None else None,'parquet_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'exact_rows':result[0],'same_number_repeated':all(x==checks[0] for x in checks),
                        'within_budget':max(scans)<=a.max_dashboard_seconds and peak is not None and peak<=a.max_peak_rss_mib}
                    if not item['within_budget']:
                        report['status']='failed';code=1
                    report['benchmarks'].append(item)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'exit':code,'out':str(out)},ensure_ascii=False))
    return code

if __name__=='__main__':raise SystemExit(main())
