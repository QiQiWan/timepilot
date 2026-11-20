"""Mandatory physical numerical gate on a machine with DuckDB and PyArrow.

Skipped on unsupported development hosts; acceptance script records BLOCKED.
"""
from __future__ import annotations
import pandas as pd
import pytest

duckdb=pytest.importorskip('duckdb')
pytest.importorskip('pyarrow')
from test_featurelab352_industrial_mechanisms import contract,definition,compiler


def test_real_duckdb_parquet_mechanism_reset_and_aba(tmp_path):
    frame=pd.DataFrame([
        ('d1','2026-01-01T00:00:00Z',10.0,1.0,True,'A'),
        ('d1','2026-01-01T00:01:00Z',20.0,2.0,False,'A'),
        ('d1','2026-01-01T00:02:00Z',30.0,3.0,False,'B'),
        ('d1','2026-01-01T00:03:00Z',40.0,4.0,True,'B'),
        ('d1','2026-01-01T00:04:00Z',50.0,5.0,False,'A'),
        ('d2','2026-01-01T00:00:00Z',100.0,8.0,False,'A'),
        ('d2','2026-01-01T00:01:00Z',200.0,7.0,True,'A'),
    ],columns=['device','t','pressure','increment','maintenance','regime'])
    frame['t']=pd.to_datetime(frame['t'],utc=True).dt.tz_localize(None)
    con=duckdb.connect()
    con.register('input_frame',frame)
    target=tmp_path/'industrial.parquet'
    safe="'"+str(target).replace("'","''")+"'"
    con.execute(f'COPY input_frame TO {safe} (FORMAT PARQUET)')
    con.execute(f'CREATE TEMP VIEW __tp_effective AS SELECT *, ROW_NUMBER() OVER (ORDER BY device,t) AS __tp_rowid FROM read_parquet({safe})')
    c=contract();mean=compiler(c,definition()).compile_views(con)
    got=con.execute(f'SELECT device,t,mechanism_output FROM {mean} ORDER BY device,t').fetchall()
    assert [x[2] for x in got]==[10.0,15.0,30.0,40.0,50.0,None,200.0]
    # A separate connection/view sequence avoids helper name collisions.
    con=duckdb.connect();con.execute(f'CREATE TEMP VIEW __tp_effective AS SELECT *, ROW_NUMBER() OVER (ORDER BY device,t) AS __tp_rowid FROM read_parquet({safe})')
    total=compiler(c,definition('industrial.reset_sum',input_id='fld_increment')).compile_views(con)
    vals=[r[0] for r in con.execute(f'SELECT mechanism_output FROM {total} ORDER BY device,t')]
    assert vals==[1.0,3.0,3.0,4.0,5.0,None,7.0]
