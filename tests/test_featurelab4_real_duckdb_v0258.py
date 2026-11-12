"""Target-machine SQL numerical verification for feature windows and PIT.

Skipped in source-only containers without DuckDB; configured acceptance must
record this as blocked, not a pass.
"""
import pytest
from test_featurelab4_industrial_v0259 import data_contract,spec_for
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_contracts import FeatureSpecV2

duckdb=pytest.importorskip('duckdb')


def make_table(con,late=False):
    con.execute('''CREATE TABLE t(__tp_rowid INTEGER,device VARCHAR,event_at TIMESTAMP,
        available_at TIMESTAMP,prediction_at TIMESTAMP,maintenance_flag BOOLEAN,
        temp DOUBLE,target DOUBLE,label_available TIMESTAMP)''')
    rows=[
      (1,'A','2026-10-01 10:00:00','2026-10-01 10:00:01','2026-10-01 13:00:00',True,3.,0.,'2026-10-02 10:00:00'),
      (2,'A','2026-10-01 10:01:00','2026-10-01 10:01:01','2026-10-01 11:00:00',False,4.,0.,'2026-10-02 10:00:00'),
      (3,'B','2026-10-01 10:01:00','2026-10-01 10:01:01','2026-10-01 11:00:00',False,5.,1.,'2026-10-02 10:00:00'),
      (4,'A','2026-10-01 10:02:00','2026-10-01 10:02:01','2026-10-01 11:00:00',False,6.,1.,'2026-10-02 10:00:00'),
      (5,'A','2026-10-01 10:03:00','2026-10-01 10:03:01','2026-10-01 11:00:00',True,7.,0.,'2026-10-02 10:00:00')]
    if late:
        r=list(rows[0]);r[3]='2026-10-01 12:00:00';rows[0]=tuple(r)
    con.executemany('INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?)',rows)
    con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM t')


def test_time_since_maintenance_event_sql_matches_independent_expected():
    with duckdb.connect(':memory:') as con:
        make_table(con)
        comp=FeatureCompiler(data_contract(),spec_for(data_contract()))
        view=comp.compile_views(con)
        result=con.execute('SELECT __tp_rowid,seconds_since_maintenance FROM '+view+' ORDER BY __tp_rowid').fetchall()
        assert [r[0] for r in result]==[1,2,3,4,5]
        assert [r[1] for r in result]==[0,60,None,120,0]


def test_late_maintenance_observation_rejected_for_supervised_task():
    with duckdb.connect(':memory:') as con:
        make_table(con,late=True)
        c=data_contract();spec=spec_for(c,supervised=True)
        with pytest.raises(ValueError,match='窗口包含'):
            FeatureCompiler(c,spec).validate_point_in_time(con)
