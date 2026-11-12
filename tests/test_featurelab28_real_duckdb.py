"""Real DuckDB acceptance; skipped when optional runtime engine is unavailable.

The fixture is generated and is NOT production plant data. Numerical expected
values are independent of the SQL compilation path.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from test_featurelab28_advanced import _contract, _one
from timepilot.feature_contracts import FeatureDefinition, FeatureSpecV2
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_visualization import summarize_preview
from timepilot.storage import sha256_file


duckdb=pytest.importorskip('duckdb', reason='real DuckDB numerical validation needs installed DuckDB')


def _sample(con):
    con.execute('''CREATE TEMP TABLE raw(__tp_rowid BIGINT, device_id VARCHAR,
        event_time TIMESTAMP, pressure_A DOUBLE, pressure_B DOUBLE,
        temperature DOUBLE, ng_flag INTEGER)''')
    rows=[]
    for i in range(1,31):
        device='DEV-A' if i<=15 else 'DEV-B'
        idx=i if i<=15 else i-15
        pressure_a=100.0+idx
        pressure_b=50.0+idx/2
        rows.append((i,device,f'2026-11-09 10:{idx:02}:00',pressure_a,pressure_b,20.0+idx/10,0))
    con.executemany('INSERT INTO raw VALUES (?,?,?,?,?,?,?)',rows)
    con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM raw')


def _views(con):
    contract=_contract()
    defs=[
      _one('rolling.sum',parameters={'window':3,'min_periods':2,'order_field_id':'stamp'}),
      _one('rolling.median',parameters={'window':3,'min_periods':2,'order_field_id':'stamp'}),
      _one('rolling.p90',parameters={'window':3,'min_periods':2,'order_field_id':'stamp'}),
      _one('rolling.range',parameters={'window':3,'min_periods':2,'order_field_id':'stamp'}),
      _one('relative.change',parameters={'lag':1,'order_field_id':'stamp'}),
      _one('expression.safe',input_fields=['pressure_a','pressure_b'],
           parameters={'expression':'safe_div(x0 - x1, x1)'}),
    ]
    for i,feat in enumerate(defs):
        defs[i]=feat.model_copy(update={'feature_id':f'field_feature_{i}',
                                         'output_name':f'feature_{i}'})
    spec=FeatureSpecV2(name='qa_industrial',source={'asset_id':contract.asset_id,
        'version_id':'base','source_sha256':contract.source_sha256},
        contract_revision=contract.revision,features=defs)
    return FeatureCompiler(contract,spec).compile_views(con)


def test_real_duckdb_compiled_features_have_independent_expected_values():
    with duckdb.connect(':memory:') as con:
        _sample(con);table=_views(con)
        actual=con.execute(f'''SELECT device_id, "__tp_rowid", feature_0,feature_1,
            feature_2,feature_3,feature_4,feature_5 FROM {table}
            ORDER BY "__tp_rowid"''').fetchall()
        assert len(actual)==30
        assert actual[0][2:6]==(None,None,None,None)
        assert actual[0][6] is None
        # DEV-A index 3: values 101,102,103 => sum306, median102, p90 102.8, range2.
        a3=actual[2]
        assert a3[2]==306.0 and a3[3]==102.0
        assert math.isclose(a3[4],102.8,abs_tol=1e-10)
        assert a3[5]==2.0
        assert math.isclose(a3[6],1.0/102.0,abs_tol=1e-10)
        assert math.isclose(a3[7],1.0,abs_tol=1e-10)
        # First row of DEV-B must start with a blank window (no cross-device leakage).
        assert actual[15][2:6]==(None,None,None,None)
        assert actual[15][6] is None


def test_real_duckdb_all_rows_quality_stats_and_parquet_materialization(tmp_path):
    with duckdb.connect(':memory:') as con:
        _sample(con);table=_views(con)
        stats=summarize_preview(con,table,['feature_0','feature_1','feature_5'],sample_count=5)
        assert len(stats)==3
        assert all(x['rows_scanned']==30 for x in stats)
        assert stats[0]['finite_count']==28
        assert stats[1]['missing_or_nonfinite_count']==2
        assert stats[2]['finite_count']==30
        assert stats[0]['scope']=='full_source_up_to_preview_scan_budget'
        assert not stats[0]['predictive_value_evaluated']
        dest=tmp_path/'industrial_features.parquet'
        con.execute(f"COPY (SELECT * EXCLUDE (\"__tp_rowid\") FROM {table} ORDER BY \"__tp_rowid\") TO '{dest}' (FORMAT PARQUET)")
        sha=sha256_file(dest)
        assert len(sha)==64 and dest.stat().st_size>0
        count, f0=con.execute('SELECT COUNT(*),SUM(feature_0 IS NOT NULL)::INT FROM read_parquet(?)',
                              [str(dest)]).fetchone()
        assert count==30 and f0==28
        names=[row[0] for row in con.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(dest)]).fetchall()]
        assert '__tp_rowid' not in names
        assert all(f'feature_{i}' in names for i in range(6))
