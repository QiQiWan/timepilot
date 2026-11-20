"""Physical DuckDB/Parquet time-chart verification; SKIP means NOT ACCEPTED.

Execute with the user's existing Mac Python environment; no packages or Chrome
are downloaded and no user data is modified.
"""
import pytest

duckdb=pytest.importorskip('duckdb',reason='physical DuckDB acceptance requires installed duckdb')
from timepilot.feature_chart_semantics import series_aggregate


def test_duckdb_entity_time_buckets_match_independent_sql():
    c=duckdb.connect(':memory:')
    c.execute('CREATE TABLE t AS SELECT i AS __tp_rowid, '
        "CASE WHEN i<10000 THEN 'device_1' ELSE 'device_2' END AS device, "
        'i%10000 AS ts, CASE WHEN i=9950 THEN 5555.0 ELSE i%11 END::DOUBLE AS v '
        'FROM range(0,20000) t(i)')
    data=series_aggregate(c,'t',order_column='ts',value_expression='v',
        original_expression='v',entity_column='device',entity_value='device_1',
        start='9000',end='9999',resolution=120)
    n,lo,hi,total=c.execute("SELECT COUNT(*), MIN(v), MAX(v), SUM(v) FROM t "
        "WHERE device='device_1' AND ts BETWEEN 9000 AND 9999").fetchone()
    assert data['population_count']==n
    assert sum(p['count'] for p in data['points'])==n
    assert max(p['max'] for p in data['points'])==hi==5555.0
    assert min(p['min'] for p in data['points'])==lo
    assert abs(sum(p['value']*p['count'] for p in data['points'])-total)<1e-6
    assert data['time_range']=={'start':'9000','end':'9999'}


def test_real_parquet_roundtrip_summary_is_exact(tmp_path):
    pa=pytest.importorskip('pyarrow',reason='physical Parquet acceptance requires pyarrow')
    c=duckdb.connect(':memory:')
    filename=tmp_path/'source.parquet'
    quoted="'"+str(filename).replace("'", "''")+"'"
    c.execute("COPY (SELECT i AS __tp_rowid, 'M' AS device, i AS ts, (i%17)::DOUBLE AS v "
        f"FROM range(1500) t(i)) TO {quoted} (FORMAT PARQUET)")
    c.execute(f"CREATE VIEW scan AS SELECT * FROM read_parquet({quoted})")
    doc=series_aggregate(c,'scan',order_column='ts',value_expression='v',entity_column='device')
    assert doc['population_count']==1500
    assert sum(p['count'] for p in doc['points'])==1500
    assert max(p['max'] for p in doc['points'])==16.0
