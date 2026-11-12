"""Numerical and SQL-contract verification for full-row Pearson matrices.

When duckdb is missing, the real-engine test is SKIPPED, never reported as
an actual DuckDB pass. Other tests inspect only compiled SQL/validation.
"""
from __future__ import annotations
from contextlib import contextmanager
from math import isclose
import pytest
from timepilot.bi_statistics import BICorrelationService


class _FakeNative:
    def __init__(self,con):self.con=con
    @contextmanager
    def _connection(self):yield self.con

class _FakeEngine:
    def __init__(self,con):
        self.native=_FakeNative(con)
        self.fields=['X','Y space','Constant','Index']
    def checked(self,pid,aid,vid):
        return {'sha256':'sha1','columns':[{'name':f,'physical_type':'DOUBLE'} for f in self.fields]}
    def _source(self,con,pid,state):
        return '__tp_effective',self.fields


def workspace(filters=None):
    return {'source_sha256':'sha1','state':{'asset_id':'asset','version_id':'base',
            'query':{'filters':filters or []},'expressions':[],'window':None,'join':None,'bucket':None,'industrial':None}}


def test_matrix_rejects_unknown_columns_before_query_execution():
    class Con:
        def execute(self,*args):raise AssertionError('untrusted query ran')
    service=BICorrelationService(_FakeEngine(Con()))
    for fields in (['X','drop; SELECT * FROM secrets'],['X','X'],['X'],list('x'*41)):
        with pytest.raises(ValueError):service.compute('project',workspace(),field_ids=fields)
    with pytest.raises(ValueError):service.compute('project',workspace(),field_ids=['X','Y space'],method='spearman')
    with pytest.raises(ValueError):service.compute('project',{**workspace(),'state':{**workspace()['state'],'join':{'right_asset_id':'other'}}},field_ids=['X','Y space'])


def test_matrix_compiles_unpaged_parameterized_selection():
    class Con:
        def __init__(self):self.calls=[]
        def execute(self,sql,args):
            self.calls.append((sql,args))
            class Result:
                def fetchone(self):return (9,1.0,8,0.5,9,1.0)
            return Result()
        def interrupt(self):pass
    con=Con();engine=_FakeEngine(con)
    computed=BICorrelationService(engine).compute('project',workspace([{'column':'Index','op':'gt','value':5}]),
                field_ids=['X','Y space'])
    assert len(con.calls)==1
    sql,params=con.calls[0]
    assert 'WHERE TRY_CAST("Index" AS DOUBLE) > ?' in sql
    assert '"Y space"' in sql
    assert 'CORR(' in sql and 'COUNT(*) FILTER' in sql
    assert 'LIMIT ?' not in sql and 'OFFSET ?' not in sql
    assert params==[5]
    assert computed['scan_scope']=='all_filtered_rows'
    assert len(computed['matrix'])==4
    assert computed['matrix'][1]['coefficient']==computed['matrix'][2]['coefficient']==0.5
    assert computed['matrix'][1]['n_pair']==8
    assert computed['selection_fingerprint']!=BICorrelationService.selection_fingerprint(workspace()['state'],'sha1')


def test_true_duckdb_pairwise_nulls_constant_and_filtered_r():
    duckdb=pytest.importorskip('duckdb',reason='真实 DuckDB 缺失：数值 E2E 标记 BLOCKED/SKIP')
    con=duckdb.connect(database=':memory:')
    con.execute('''CREATE TEMP TABLE __tp_effective AS SELECT * FROM (VALUES
       (1,1.0,2.0,9.0), (2,2.0,4.0,9.0), (3,3.0,6.0,9.0),
       (4,4.0,NULL,9.0), (5,5.0,-2.0,9.0), (6,NULL,10.0,9.0)) AS t("Index",X,"Y space",Constant)''')
    service=BICorrelationService(_FakeEngine(con))
    r=service.compute('p',workspace(),field_ids=['X','Y space','Constant'])
    by={(c['field_x'],c['field_y']):c for c in r['matrix']}
    assert r['dimension']==3 and len(r['matrix'])==9
    assert isclose(by[('X','X')]['coefficient'],1.0,abs_tol=1e-12)
    assert by[('X','Y space')]['n_pair']==4
    assert by[('Constant','X')]['coefficient'] is None
    assert by[('Constant','X')]['status']=='undefined'
    assert by[('X','Y space')]['coefficient'] is not None
    narrower=service.compute('p',workspace([{'column':'Index','op':'lt','value':5}]),field_ids=['X','Y space'])
    assert narrower['matrix'][1]['n_pair']==3
    assert isclose(narrower['matrix'][1]['coefficient'],1.0,abs_tol=1e-12)
    assert r['selection_fingerprint'] != narrower['selection_fingerprint']
    con.close()
