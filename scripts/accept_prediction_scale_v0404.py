"""v0.40.4 reproducible 100k/240k/1m validation-query workload.

The source baseline explicitly measures CSV fallback only. The native physical
receipt is BLOCKED unless real DuckDB, PyArrow, Perspective, compiled WASM,
Python 3.12.12 and macOS are present. No dependency is downloaded or faked.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.model_prediction_assets import FIELDS,_sha
from timepilot.model_prediction_query import _csv_query,_duckdb_query,_filters


def rows_to_csv(path, size):
    count=0
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS)
        writer.writeheader()
        for i in range(size):
            model=['baseline','forest','linear'][i%3]
            fold=(i//3)%3+1
            month=f'2026-{(i//9)%3+1:02d}'
            series=f'D{(i//27)%4}'
            residual=round((i%5)*0.1,3)
            item={'__tp_rowid':i,'model':model,'fold':fold,
                 'source_row_sha256':'a'*64,'source_row_index':i,
                 'series_id':series,'event_time_utc':'2026-02-01T00:00:00+00:00',
                 'decision_time_utc':'2026-01-31T00:00:00+00:00',
                 'label_mature_utc':'2026-02-02T00:00:00+00:00',
                 'month':month,'actual':4.0,'prediction':4.0+residual,
                 'residual':residual,'absolute_error':residual,
                 'predicted_label':None,'actual_label':None}
            writer.writerow(item)
            if model=='forest' and fold==2 and month=='2026-02' and series=='D2' and residual>=.2:
                count+=1
    return count


def run(*, sizes=(100000,240000,1000000), source_baseline=False, out=None):
    sizes=tuple(int(x) for x in sizes)
    if not sizes or any(n<100 or n>1000000 for n in sizes):raise ValueError('Invalid benchmark row counts')
    modules={name:importlib.util.find_spec(name) is not None for name in ('duckdb','pyarrow','perspective')}
    bundle=ROOT/'src/timepilot/static/native-bi/perspective-build.json'
    system={'platform':platform.system(),'python':platform.python_version(),
            'dependencies':modules,'native_bundle_built':bundle.is_file(),
            'target_mac_python':platform.system()=='Darwin' and platform.python_version()=='3.12.12'}
    document={'version':'0.40.4','utc':datetime.now(timezone.utc).isoformat(),
              'mode':'source_csv_baseline' if source_baseline else 'native_physical',
              'environment':system,'workloads':[],'status':'in_progress'}
    try:
        from psutil import Process
        process=Process()
    except ImportError:
        process=None
    with tempfile.TemporaryDirectory(prefix='timepilot-v0404-query-') as folder:
        for size in sizes:
            path=Path(folder)/f'predictions_{size}.csv'
            before=time.monotonic()
            expected=rows_to_csv(path,size)
            written=time.monotonic()
            filters=_filters(model='forest',fold=2,month='2026-02',device='D2',
                             min_abs_error=.2,offset=5,limit=100)
            csv_rows,csv_metrics,csv_backend=_csv_query(path,filters)
            csv_end=time.monotonic()
            if csv_metrics['prediction_rows']!=expected:
                raise AssertionError(f'CSV query count mismatch {size}: {csv_metrics["prediction_rows"]}!={expected}')
            if csv_rows and any(not float(row['absolute_error'])>=.2 for row in csv_rows):
                raise AssertionError('Invalid filtered CSV prediction row')
            check={'rows':size,'csv_bytes':path.stat().st_size,
                   'csv_sha256':_sha(path),'expected_filtered_rows':expected,
                   'csv_backend':csv_backend,
                   'csv_write_seconds':round(written-before,3),
                   'csv_query_seconds':round(csv_end-written,3),
                   'csv_page_row_ids':[int(x['__tp_rowid']) for x in csv_rows[:8]],
                   'filtered_micro_metrics':csv_metrics,
                   'process_rss_bytes':process.memory_info().rss if process else None}
            if modules['duckdb'] and modules['pyarrow']:
                import duckdb
                parquet=path.with_suffix('.parquet')
                quoted=lambda x:"'"+str(x).replace("'","''")+"'"
                t0=time.monotonic()
                with duckdb.connect(':memory:') as con:
                    con.execute("SET threads=1")
                    con.execute("COPY (SELECT * FROM read_csv_auto("+quoted(path)+", header=true)) "
                        "TO "+quoted(parquet)+" (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)")
                native_rows,native_metrics,native_backend=_duckdb_query(parquet,True,filters)
                check['duckdb_parquet_seconds']=round(time.monotonic()-t0,3)
                check['parquet_bytes']=parquet.stat().st_size
                check['query_backend']=native_backend
                if native_metrics!=csv_metrics or [int(x['__tp_rowid']) for x in native_rows]!=[int(x['__tp_rowid']) for x in csv_rows]:
                    raise AssertionError(f'Parquet/native query differs from verified CSV for {size}')
                check['native_query_metrics_parity']=True
            document['workloads'].append(check)
            if out:Path(out).parent.mkdir(parents=True,exist_ok=True);Path(out).write_text(
                json.dumps(document,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    required=all(modules.values()) and system['target_mac_python'] and system['native_bundle_built']
    document['status']='source_performance_only' if source_baseline else 'native_query_passed_browser_pending' if required else 'blocked'
    if not required:
        document['missing_physical_evidence']=[
            *[f'python_module_{name}' for name,found in modules.items() if not found],
            *([] if system['target_mac_python'] else ['macOS_Python_3.12.12']),
            *([] if system['native_bundle_built'] else ['native_perspective_wasm_bundle']),
            'actual_Chrome_Perspective_WSS_end_to_end_not_executed']
    if out:Path(out).write_text(json.dumps(document,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':document['status'],'workloads':[
        {'rows':x['rows'],'csv_query_seconds':x['csv_query_seconds'],'native_query_metrics_parity':x.get('native_query_metrics_parity',False)}
        for x in document['workloads']],'missing_physical_evidence':document.get('missing_physical_evidence',[])},ensure_ascii=False))
    return 0 if source_baseline or required else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-baseline',action='store_true')
    parser.add_argument('--sizes',type=int,nargs='+',default=[100000,240000,1000000])
    parser.add_argument('--out',type=Path,default=Path('reports/v0404/prediction_scale.json'))
    args=parser.parse_args()
    raise SystemExit(run(sizes=args.sizes,source_baseline=args.source_baseline,out=args.out))
