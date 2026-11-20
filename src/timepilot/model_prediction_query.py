"""v0.40.3 version-bound validation prediction query and metric scope.

DuckDB/Parquet is used when present; the bounded, verified CSV is a degraded
compatibility path. No user SQL, user filesystem path or dynamic identifier
is accepted. The returned metric is computed on matching prediction *rows*,
not an unweighted mean of fold metrics or a model-selection score.
"""
from __future__ import annotations

import csv
import math
import os
import re
import threading
import time

from .model_task_store import ModelTaskError


def query_timeout_seconds():
    """Server-owned budget, never a client-supplied value."""
    try:return min(60,max(2,int(os.environ.get('TIMEPILOT_PREDICTION_QUERY_TIMEOUT_SECONDS','15'))))
    except ValueError:return 15


def _check_deadline(deadline):
    if deadline is not None and time.monotonic()>deadline:
        raise ModelTaskError('PREDICTION_QUERY_TIMEOUT: server query budget exceeded')


def _filters(*, model=None, fold=None, month=None, device=None, min_abs_error=None,
             offset=0, limit=100):
    if not isinstance(offset,int) or not 0<=offset<=2000000 or not isinstance(limit,int) or not 1<=limit<=500:
        raise ModelTaskError('Invalid bounded validation query page')
    if model is not None and (not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',model)):
        raise ModelTaskError('Invalid model filter')
    if fold is not None and (not isinstance(fold,int) or not 1<=fold<=1000):
        raise ModelTaskError('Invalid fold filter')
    if month is not None and (not isinstance(month,str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])',month)):
        raise ModelTaskError('Invalid month filter')
    if device is not None and (not isinstance(device,str) or len(device)>160 or not device.strip()):
        raise ModelTaskError('Invalid device filter')
    if min_abs_error is not None and (not isinstance(min_abs_error,(int,float)) or
            isinstance(min_abs_error,bool) or not math.isfinite(min_abs_error) or min_abs_error<0):
        raise ModelTaskError('Invalid finite error threshold')
    return {'model':model,'fold':fold,'month':month,'device':device,
            'min_abs_error':min_abs_error,'offset':offset,'limit':limit}


def _metrics(n, ae, squared):
    return {'prediction_rows':int(n),
            'mae':round(ae/n,10) if n else None,
            'rmse':round(math.sqrt(squared/n),10) if n else None,
            'metric_scope':'selected_validation_prediction_rows_micro_average',
            'not_a_model_selection_score':True}


def _csv_query(path, f, *, deadline=None):
    records=[];count=0;sum_ae=0.0;sum_squares=0.0
    with path.open(newline='',encoding='utf-8') as stream:
        for scanned,row in enumerate(csv.DictReader(stream)):
            if scanned % 1024==0:_check_deadline(deadline)
            if f['model'] is not None and row['model']!=f['model']:continue
            if f['fold'] is not None and int(row['fold'])!=f['fold']:continue
            if f['month'] is not None and row['month']!=f['month']:continue
            if f['device'] is not None and row['series_id']!=f['device']:continue
            ae=float(row['absolute_error'])
            if f['min_abs_error'] is not None and ae<f['min_abs_error']:continue
            sum_ae+=ae;sum_squares+=float(row['residual'])**2
            if f['offset']<=count<f['offset']+f['limit']:
                records.append(row)
            count+=1
    _check_deadline(deadline)
    return records,_metrics(count,sum_ae,sum_squares),'verified_csv_scan_fallback'


def _duckdb_query(path, parquet, f, *, deadline=None):
    import duckdb
    relation='read_parquet(?)' if parquet else 'read_csv_auto(?, header=true, all_varchar=true)'
    clauses=[];args=[]
    for param,col in (('model','model'),('fold','fold'),('month','month'),('device','series_id')):
        if f[param] is not None:
            # Column names are fixed server-side; only *values* are parameters.
            cast='TRY_CAST("fold" AS INTEGER)' if param=='fold' else '"'+col+'"'
            clauses.append(cast+' = ?');args.append(f[param])
    if f['min_abs_error'] is not None:
        clauses.append('TRY_CAST("absolute_error" AS DOUBLE) >= ?');args.append(f['min_abs_error'])
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    _check_deadline(deadline)
    remaining=query_timeout_seconds() if deadline is None else max(0.001,deadline-time.monotonic())
    expired=threading.Event()
    with duckdb.connect(':memory:') as con:
        con.execute('SET threads=1')
        con.execute("SET memory_limit='256MB'")
        timer=threading.Timer(remaining,lambda:(expired.set(),con.interrupt()))
        timer.daemon=True
        timer.start()
        try:
            base=[str(path),*args]
            count,ae,squared=con.execute(
                f'SELECT COUNT(*), COALESCE(SUM(TRY_CAST("absolute_error" AS DOUBLE)),0), '
                f'COALESCE(SUM(POW(TRY_CAST("residual" AS DOUBLE),2)),0) FROM {relation}{where}',base).fetchone()
            cursor=con.execute(f'SELECT * FROM {relation}{where} '
                'ORDER BY TRY_CAST("__tp_rowid" AS BIGINT) ASC LIMIT ? OFFSET ?',
                [*base,f['limit'],f['offset']])
            fields=[c[0] for c in cursor.description]
            records=[{name:(str(value) if value is not None else None)
                      for name,value in zip(fields,row)} for row in cursor.fetchall()]
        except Exception as exc:
            if expired.is_set():
                raise ModelTaskError('PREDICTION_QUERY_TIMEOUT: DuckDB statement interrupted') from exc
            raise
        finally:
            timer.cancel()
    _check_deadline(deadline)
    return records,_metrics(count,float(ae),float(squared)), 'duckdb_parquet_pushdown' if parquet else 'duckdb_csv_scan'


def query(assets,pid,tid,run_id,*,model=None,fold=None,month=None,device=None,
          min_abs_error=None,offset=0,limit=100):
    started=time.monotonic()
    deadline=started+query_timeout_seconds()
    f=_filters(model=model,fold=fold,month=month,device=device,
               min_abs_error=min_abs_error,offset=offset,limit=limit)
    # Must verify the immutable asset (including BOTH CSV and Parquet hashes)
    # before importing optional query engines or opening any file.
    asset=assets.get(pid,tid,run_id)
    source,is_parquet=assets.source_file(pid,tid,run_id,asset['asset_id'])
    try:
        import duckdb  # noqa: F401
    except ImportError:
        # Source-only deployment retains a bounded, correct fallback. It does
        # not claim the native BI physical gate or million-row performance.
        csv_meta=assets._read_metadata(pid,tid,asset['revision'],run_id)
        path=assets._inside(csv_meta['csv_rel'])
        records,metrics,backend=_csv_query(path,f,deadline=deadline)
    else:
        records,metrics,backend=_duckdb_query(source,is_parquet,f,deadline=deadline)
    return {'asset_id':asset['asset_id'],'manifest_sha256':asset['manifest_sha256'],
            'result_sha256':asset['result_sha256'],'source_sha256':asset['source_sha256'],
            'task_revision':asset['revision'],'filters':f,'items':records,
            'metrics':metrics,'query_backend':backend,
            'next_offset':f['offset']+len(records) if f['offset']+len(records)<metrics['prediction_rows'] else None,
            'row_identity':'__tp_rowid (stable in verified validation asset)',
            'selectable_into_lineage':True,
            'elapsed_ms':round((time.monotonic()-started)*1000,2),
            'query_budget_seconds':query_timeout_seconds(),
            'prediction_asset_row_limit':1_000_000}
