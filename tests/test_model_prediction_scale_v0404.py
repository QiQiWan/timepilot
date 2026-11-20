"""v0.40.4: query budget, million-row publication contract and read semantics."""
import csv
import math
from pathlib import Path

import pytest
from timepilot.model_prediction_assets import MAX_PREDICTIONS, FIELDS, _clean_rows, write_parquet_blocks
from timepilot.model_prediction_query import _csv_query, _duckdb_query, _filters
from timepilot.model_task_store import ModelTaskError


def _record(i):
    return {'__tp_rowid':i,'model':'forest' if i%2 else 'baseline','fold':1+i%3,
        'source_row_sha256':'a'*64,'source_row_index':i,'series_id':f'D{i%3}',
        'event_time_utc':'2026-01-01T00:00:00+00:00','decision_time_utc':'2025-12-31T00:00:00+00:00',
        'label_mature_utc':'2026-01-02T00:00:00+00:00','month':'2026-01',
        'actual':5.0,'prediction':5.25,'residual':0.25,'absolute_error':0.25,
        'predicted_label':None,'actual_label':None}


def _csv(path,n=36):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
        for i in range(n):w.writerow(_record(i))


def test_expanded_limit_and_stable_original_row_ids():
    assert MAX_PREDICTIONS == 1_000_000
    records=_clean_rows([_record(i) for i in range(60)])
    assert [r['__tp_rowid'] for r in records]==list(range(60))
    assert all(r['source_row_sha256']=='a'*64 for r in records)
    with pytest.raises(ModelTaskError):_clean_rows([])


def test_csv_fallback_has_identical_selection_metrics_and_paging(tmp_path):
    path=tmp_path/'val.csv';_csv(path,90)
    f=_filters(model='forest',fold=2,month='2026-01',device='D1',min_abs_error=0.2,
               offset=0,limit=5)
    rows,metrics,backend=_csv_query(path,f)
    assert backend=='verified_csv_scan_fallback'
    assert metrics['prediction_rows']==15
    assert metrics['mae']==0.25 and metrics['rmse']==0.25
    assert [int(r['__tp_rowid']) for r in rows]==[1,7,13,19,25]
    f['offset']=12
    final,stats,_=_csv_query(path,f)
    assert [int(r['__tp_rowid']) for r in final]==[73,79,85]
    assert stats==metrics


def test_csv_timeout_is_fail_closed(tmp_path):
    from timepilot import model_prediction_query as query
    path=tmp_path/'val.csv';_csv(path)
    f=_filters()
    with pytest.raises(ModelTaskError,match='PREDICTION_QUERY_TIMEOUT'):
        _csv_query(path,f,deadline=0)
    assert query.query_timeout_seconds()>=2


def test_signed_parquet_rowgroups_preserve_identity_if_real_pyarrow_available(tmp_path):
    pytest.importorskip('pyarrow')
    import pyarrow.parquet as pq
    rows=_clean_rows([_record(i) for i in range(67)])
    path=tmp_path/'val.parquet';write_parquet_blocks(path,rows,block_rows=10)
    file=pq.ParquetFile(path)
    assert file.metadata.num_row_groups==7
    assert file.metadata.num_rows==67
    assert sorted(file.read(columns=['__tp_rowid']).column('__tp_rowid').to_pylist())==list(range(67))
    assert file.metadata.row_group(0).column(0).statistics is not None


def test_real_duckdb_parquet_filtered_metrics_when_available(tmp_path):
    pytest.importorskip('duckdb');pytest.importorskip('pyarrow')
    path=tmp_path/'val.parquet';write_parquet_blocks(path,_clean_rows([_record(i) for i in range(90)]),block_rows=12)
    f=_filters(model='forest',fold=2,device='D1',offset=10,limit=4)
    rows,metrics,backend=_duckdb_query(path,True,f)
    assert backend=='duckdb_parquet_pushdown'
    assert metrics['prediction_rows']==15
    assert [int(r['__tp_rowid']) for r in rows]==[61,67,73,79]


def test_websocket_auth_revalidated_against_live_task_and_asset():
    from timepilot.native_bi_api import prediction_session_valid
    expected={'run_id':'r','asset_id':'a','revision':2,'manifest_sha256':'x'*64}
    active={'state':'succeeded','current':True,'report_available':True,'run_id':'r',
            'prediction_asset':{'asset_id':'a','revision':2,'manifest_sha256':'x'*64}}
    assert prediction_session_valid(active,**expected)
    for change in ({'state':'failed'},{'current':False},{'report_available':False},
                   {'run_id':'new'}):
        assert not prediction_session_valid({**active,**change},**expected)
    for change in ({'revision':3},{'manifest_sha256':'y'*64},{'asset_id':'other'}):
        assert not prediction_session_valid({**active,'prediction_asset':{**active['prediction_asset'],**change}},**expected)
