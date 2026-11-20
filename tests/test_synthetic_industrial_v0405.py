"""Synthetic-only, reproducible cross-family QA for v0.40.5."""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd
import pytest

from timepilot.synthetic_industrial import generate
from timepilot.model_prediction_query import _csv_query, _filters
from timepilot.model_prediction_assets import _sha, FIELDS
from timepilot.model_task_spec import ModelTaskInput
from timepilot.model_task_store import ModelTaskError
from timepilot.model_task_time_audit import audit_availability, make_temporal_folds


def task(kind='rolling_origin'):
    return ModelTaskInput.model_validate({
        'name':'Synth future device temperature', 'asset_id':'synthetic',
        'task_kind':'tabular_regression',
        'target':{'column':'target_next_c'},
        'features':[{'column':'temperature_c','available_at_column':'x_available_at_utc'},
                    {'column':'vibration_mm_s','available_at_column':'x_available_at_utc'}],
        'time':{'event_time_column':'event_time_utc','decision_time_column':'decision_time_utc',
                'label_available_at_column':'label_ready_at_utc',
                'entity_columns':['device'],'timezone':'UTC'},
        'validation':{'kind':kind,'folds':3,'gap':2},
    })


def test_synthetic_generation_is_reproducible_and_tampering_is_detectable(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';c=tmp_path/'c'
    first=generate(a,rows=1400,seed=47,devices=4,step_minutes=120)
    second=generate(b,rows=1400,seed=47,devices=4,step_minutes=120)
    third=generate(c,rows=1400,seed=48,devices=4,step_minutes=120)
    assert first == second
    assert first['predictions_csv_sha256'] != third['predictions_csv_sha256']
    assert first['telemetry_csv_sha256'] == _sha(a/'industrial_telemetry.csv')
    assert first['model_rows'] == {'baseline':1400,'linear':1400,'forest':1400}
    assert first['future_x_rows'] == first['invalid_label_rows'] == 0
    assert first['synthetic_only'] and first['ground_truth_not_real_operational_data']
    original_sha = first['predictions_csv_sha256']
    with (a/'validation_predictions.csv').open('a') as stream:
        stream.write('tampered\n')
    assert _sha(a/'validation_predictions.csv') != original_sha


def test_pit_and_forward_folds_use_actual_generated_clocks(tmp_path):
    generate(tmp_path,rows=1600,seed=43,devices=4,step_minutes=120)
    frame=pd.read_csv(tmp_path/'industrial_telemetry.csv')
    summary,event,decision,label=audit_availability(frame,task())
    assert summary['rows']==1600 and summary['full_sample_checked']
    assert len(summary['features'])==2
    assert all(x['late_rows']==0 for x in summary['features'])
    folds=make_temporal_folds(frame,task(),event=event,label_available=label)
    assert len(folds)==3
    for tr,va,cutoff,_ in folds:
        assert len(tr)>0 and len(va)>0
        assert event.iloc[tr].max()<event.iloc[va].min()
        assert (label.iloc[tr]<=cutoff).all()
        assert not set(tr).intersection(set(va))
        assert decision.iloc[va].max()<=event.iloc[va].max()


@pytest.mark.parametrize('scenario,keyword',[('future_x','PIT violation'),
                                              ('premature_label','precedes the event')])
def test_planted_leakage_and_bad_label_clock_are_rejected(tmp_path,scenario,keyword):
    manifest=generate(tmp_path,rows=540,devices=4,scenario=scenario)
    assert (manifest['future_x_rows'] if scenario=='future_x' else manifest['invalid_label_rows'])>0
    frame=pd.read_csv(tmp_path/'industrial_telemetry.csv')
    with pytest.raises(ModelTaskError,match=keyword):
        audit_availability(frame,task())


def test_independent_regime_shift_and_query_pagination(tmp_path):
    manifest=generate(tmp_path,rows=10000,devices=4,seed=43,step_minutes=120)
    assert manifest['regime_shift_rows']>0
    with (tmp_path/'validation_predictions.csv').open(newline='') as fh:
        data=list(csv.DictReader(fh))
    assert len(data)==30000 and tuple(data[0]) == FIELDS
    assert len({x['source_row_sha256'] for x in data})==10000
    assert data[0]['source_row_sha256']==data[1]['source_row_sha256']==data[2]['source_row_sha256']
    assert {x['model'] for x in data[:3]}==set(('baseline','linear','forest'))
    assert len({x['fold'] for x in data[:3]})==1
    samples=[x for x in data if x['model']=='forest']
    normal=[float(x['absolute_error']) for x in samples if int(x['source_row_index'])//4<1800]
    shifted=[float(x['absolute_error']) for x in samples if int(x['source_row_index'])//4>=1800]
    assert normal and shifted and sum(shifted)/len(shifted)>sum(normal)/len(normal)+0.9
    f=_filters(model='forest',fold=2,device='SMT-01',min_abs_error=.3,offset=3,limit=25)
    page,metrics,backend=_csv_query(tmp_path/'validation_predictions.csv',f)
    expected=[r for r in data if r['model']=='forest' and int(r['fold'])==2
              and r['series_id']=='SMT-01' and float(r['absolute_error'])>=.3]
    assert metrics['prediction_rows']==len(expected)
    assert page==expected[3:28]
    assert metrics['mae']==pytest.approx(sum(float(x['absolute_error']) for x in expected)/len(expected),abs=1e-9)
    assert metrics['rmse']==pytest.approx(math.sqrt(sum(float(x['residual'])**2 for x in expected)/len(expected)),abs=1e-9)
    assert backend=='verified_csv_scan_fallback'


def test_real_duckdb_parquet_property_when_installed(tmp_path):
    pytest.importorskip('duckdb')
    pytest.importorskip('pyarrow')
    from timepilot.model_prediction_query import _duckdb_query
    from timepilot.model_prediction_assets import _clean_rows, write_parquet_blocks
    generate(tmp_path,rows=1500,devices=4)
    with (tmp_path/'validation_predictions.csv').open(newline='') as src:
        rows=[]
        for item in csv.DictReader(src):
            for key in ('fold','source_row_index','__tp_rowid','predicted_label','actual_label'):
                item[key]=int(item[key])
            for key in ('actual','prediction','residual','absolute_error'):
                item[key]=float(item[key])
            rows.append(item)
    p=tmp_path/'val.parquet'
    write_parquet_blocks(p,_clean_rows(rows),block_rows=128)
    f=_filters(model='linear',fold=1,limit=11,offset=9)
    csv_items,csv_metrics,_=_csv_query(tmp_path/'validation_predictions.csv',f)
    native_items,native_metrics,backend=_duckdb_query(p,True,f)
    assert backend=='duckdb_parquet_pushdown'
    assert [int(x['__tp_rowid']) for x in native_items]==[int(x['__tp_rowid']) for x in csv_items]
    assert native_metrics['prediction_rows']==csv_metrics['prediction_rows']
    assert native_metrics['mae']==pytest.approx(csv_metrics['mae'],abs=1e-8)
