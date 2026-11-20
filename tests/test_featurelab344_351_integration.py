"""v0.34.4-0.35.1 deterministic non-DuckDB acceptance gates.

Production DuckDB/Parquet graph tests are in test_featurelab344_real_duckdb.py,
and are intentionally NOT counted as passed if the optional engines are absent.
"""
import math
import sqlite3

import numpy as np
import pytest

from timepilot.feature_chart_semantics import series_aggregate,validate_series_args
from timepilot.feature_search_orchestrator import summarize_search,TIERS
from timepilot.feature_ablation import ablate_fold,summarize_ablation


def _source():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE source (__tp_rowid INTEGER, device TEXT, seq INTEGER, val DOUBLE)')
    conn.executemany('INSERT INTO source VALUES (?,?,?,?)',
        [(i,'A',i,1234.0 if i==5000 else 10.0) for i in range(6000)] +
        [(i+6000,'B',i,77.0) for i in range(6000)])
    return conn


def _series(conn,**kwargs):
    return series_aggregate(conn,'source',order_column='seq',
        value_expression='val',original_expression='val',entity_column='device',
        entity_field_id='device_id',**kwargs)


def test_full_time_range_keeps_late_spike_and_no_cross_device_connection():
    db=_source()
    doc=_series(db,resolution=120)
    assert doc['entity_value']=='A'
    assert doc['entity_selection']=='auto_largest_group'
    assert doc['population_count']==6000
    assert len(doc['points'])==120
    assert sum(item['count'] for item in doc['points'])==6000
    assert max(item['max'] for item in doc['points'])==1234.0
    assert doc['time_range']=={'start':'0','end':'5999'}
    assert doc['partial'] is False
    assert {x['value'] for x in doc['entity_options']}=={'A','B'}
    assert doc['scope']=='selected_entity_and_time_range_full_aggregate'


def test_filter_by_entity_and_time_does_not_include_other_entity():
    db=_source()
    doc=_series(db,entity_value='B',start='4700',end='5300',resolution=60)
    assert doc['population_count']==601
    assert doc['entity_value']=='B'
    assert all(x['min']==77.0 and x['max']==77.0 for x in doc['points'])
    doc_a=_series(db,entity_value='A',start='4950',end='5060',resolution=60)
    assert doc_a['population_count']==111
    assert max(x['max'] for x in doc_a['points'])==1234.0
    # Every reported bucket mean can be independently calculated.
    count=db.execute('SELECT COUNT(*) FROM source WHERE device=? AND seq BETWEEN ? AND ?',('A',4950,5060)).fetchone()[0]
    assert sum(p['count'] for p in doc_a['points'])==count


def test_bounded_parameters_and_nonexistent_entity_are_rejected():
    db=_source()
    for n in (0,11,301,5000):
        with pytest.raises(ValueError):validate_series_args(resolution=n)
    with pytest.raises(ValueError,match='设备'): _series(db,entity_value='missing')
    with pytest.raises(ValueError):validate_series_args(entity_value='X'*161)
    with pytest.raises(ValueError):validate_series_args(start='2025-09-10',end='2025-09-01')


def test_null_stats_are_retained_and_counted_without_fake_zero():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE source (__tp_rowid INTEGER,device TEXT,seq INTEGER,val DOUBLE)')
    db.executemany('INSERT INTO source VALUES (?,?,?,?)',[(i,'A',i,None if i<20 else 6.) for i in range(40)])
    doc=_series(db,resolution=20)
    assert doc['population_count']==40
    assert doc['finite_count']==20
    assert sum(p['finite_count'] for p in doc['points'])==20
    assert any(p['value'] is None and p['min'] is None for p in doc['points'])


def test_search_round_evidence_is_reproducible_and_never_labels_gain():
    candidates=[{'candidate_id':f'c{i}','fingerprint':f'fp{i}',
                 'search_round':1+i//16,'family':'statistics' if i%2 else 'transform',
                 'input_field_ids':[f'f{i%12}']} for i in range(32)]
    rating=[{'candidate_id':f'c{i}','status':'recommended' if i%3 else 'review'} for i in range(32)]
    one=summarize_search(candidates,rating,budget=32,source_sha256='a'*64)
    two=summarize_search(candidates,rating,budget=32,source_sha256='a'*64)
    assert one==two
    assert [x['candidates'] for x in one['rounds']]==[16,16]
    assert one['stopping_reason']=='budget_consumed'
    assert one['quality_verified'] is True
    assert one['labels_used'] is False and one['model_gain_claimed'] is False
    assert TIERS=={'quick':16,'standard':32,'deep':64}
    rating.pop()
    doc=summarize_search(candidates,rating,budget=32,source_sha256='a'*64)
    assert doc['quality_verified'] is False
    assert doc['stopping_reason']=='unverified_computations'


def test_group_ablation_runs_same_fold_and_does_not_infer_causality():
    # Synthetic signal: raw column weak, derived column strong.
    rng=np.random.default_rng(772)
    all_x=rng.normal(size=(180,3))
    y=8*all_x[:,1]+rng.normal(scale=.04,size=180)
    r=ablate_fold(all_x[:130],all_x[130:],y[:130],y[130:],
        raw_count=1,names=['raw','good','noise'],selected_indices=[1,2],
        task_type='regression',family='linear',seed=17,
        groups={'good':'derived_signal','noise':'noise_group'})
    assert r['status']=='evaluated'
    assert r['paired_fold'] is True and r['scope']=='outer_training_fold_fit_validation_fold_score'
    assert any(x['group']=='derived_signal' and x['loss_when_removed']>0 for x in r['groups'])
    summary=summarize_ablation([{'ablation':r}])
    assert summary['causal_claim'] is False
    assert {x['group'] for x in summary['groups']}=={'derived_signal','noise_group'}


def test_ablation_no_features_is_reported_unavailable():
    x=np.ones((30,1));y=np.arange(30)
    r=ablate_fold(x[:20],x[20:],y[:20],y[20:],raw_count=1,
        names=['raw'],selected_indices=[],task_type='regression',family='linear',seed=1)
    assert r['status']=='no_selected_features' and r['groups']==[]
