"""v0.33.2 physical DuckDB feature calculations against committed fixed CSVs.

A test skipped because DuckDB is absent is NOT a passed integration gate.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from timepilot.feature_contracts import FeatureDefinition,FeatureSpecV2,SourceRef
from timepilot.feature_compiler import FeatureCompiler
from test_featurelab332_fixed_benchmarks import fixed,contract,make_protocol

duckdb=pytest.importorskip('duckdb',reason='此端缺少真实 DuckDB，必须在用户运行环境补跑')


def _compile(raw,con,contract_,feature,task):
    base=raw.copy()
    for col in ('event_at','available_at','decision_at','label_at'):
        if col in base:
            base[col]=pd.to_datetime(base[col],utc=True).dt.tz_localize(None)
    base.insert(0,'__tp_rowid',np.arange(1,len(base)+1,dtype='int64'))
    con.register('fixed_source',base)
    con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM fixed_source')
    spec=FeatureSpecV2(name='fixed physical calculation',source=SourceRef(asset_id=contract_.asset_id,
        version_id='base',source_sha256=contract_.source_sha256),contract_revision=contract_.revision,
        task=task,features=[feature])
    compiler=FeatureCompiler(contract_,spec)
    gate=compiler.validate_point_in_time(con)
    table=compiler.compile_views(con)
    output=con.execute(f'SELECT "{feature.output_name}" FROM {table} ORDER BY "__tp_rowid"').fetchall()
    return np.asarray([v[0] for v in output],dtype=float),gate


def test_static_regression_duckdb_squared_feature_matches_committed_values():
    frame=fixed('static_regression.csv')
    base=frame.drop(columns=['feature_b_squared','duplicate_a','constant_candidate'])
    c=contract(base)
    task=make_protocol().task
    feat=FeatureDefinition(feature_id='square_b',output_name='feature_b_squared',
        operator_id='arithmetic.binary',operator_version='2.0',
        input_field_ids=['fld_feature_b','fld_feature_b'],parameters={'operator':'*'})
    with duckdb.connect(':memory:') as con:
        actual,gate=_compile(base,con,c,feat,task)
    assert gate['status']=='verified' and gate['validation_kind']=='static_row_local'
    np.testing.assert_allclose(actual,frame['feature_b_squared'].to_numpy(),rtol=1e-9,atol=1e-9)


def test_static_binary_duckdb_interaction_matches_committed_values():
    frame=fixed('static_binary.csv')
    base=frame.drop(columns=['interaction','duplicate_a','constant_candidate'])
    c=contract(base)
    feat=FeatureDefinition(feature_id='interact',output_name='interaction',
        operator_id='arithmetic.binary',operator_version='2.0',
        input_field_ids=['fld_sensor_a','fld_sensor_b'],parameters={'operator':'*'})
    with duckdb.connect(':memory:') as con:
        actual,gate=_compile(base,con,c,feat,make_protocol('binary_classification','stratified').task)
    assert gate['status']=='verified'
    np.testing.assert_allclose(actual,frame['interaction'].to_numpy(),rtol=1e-9,atol=1e-9)


def test_six_device_duckdb_window_never_crosses_entities_or_unsorted_input():
    frame=fixed('multi_device_time.csv')
    base=frame.drop(columns=['pressure_mean_3'])
    c=contract(base,entity=('device',),time=True)
    feat=FeatureDefinition(feature_id='mean3',output_name='pressure_mean_3',
        operator_id='rolling.mean',operator_version='2.0',
        input_field_ids=['fld_pressure'],parameters={'window':3,'min_periods':1,
            'order_field_id':'fld_event_at','partition_field_ids':['fld_device']})
    with duckdb.connect(':memory:') as con:
        actual,gate=_compile(base,con,c,feat,make_protocol('regression','time',groups=('device',)).task)
    assert gate['status']=='verified' and gate['availability_window_checks']>=1
    np.testing.assert_allclose(actual,frame['pressure_mean_3'].to_numpy(),rtol=1e-9,atol=1e-9)
