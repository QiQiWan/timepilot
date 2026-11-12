"""Real numerical DuckDB contract: independent values and leakage windows.

Requires installed DuckDB. Skip in source-only mode; configured acceptance
requires the dependency explicitly and will not count a skip as a pass.
"""
from __future__ import annotations
import math
import pytest
from timepilot.feature_contracts import (DataContract,FeatureDefinition,SourceRef,
                                         FeatureSpecV2,TaskSpecV2)
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_temporal_profile import temporal_diagnostics

duckdb=pytest.importorskip('duckdb',reason='真实 DuckDB 依赖未安装')


def _contract():
    columns=[('device','VARCHAR','id','entity_key'),('event_at','TIMESTAMP','datetime','event_time'),
             ('known_at','TIMESTAMP','datetime','available_at'),('decide_at','TIMESTAMP','datetime','prediction_time'),
             ('A8','DOUBLE','numeric','value'),('A10','DOUBLE','numeric','value'),
             ('target','DOUBLE','numeric','label'),('label_known','TIMESTAMP','datetime','available_at')]
    fields=[{'field_id':'fld_'+name,'source_name':name,'source_column_index':i,
             'physical_type':dtype,'semantic_type':semantic,'role':role,'confirmation':'confirmed'}
            for i,(name,dtype,semantic,role) in enumerate(columns)]
    return DataContract(asset_id='source-1',version_id='base',source_sha256='1'*64,
        state='confirmed',revision=2,fields=fields,entity_field_ids=['fld_device'],
        event_time_field_id='fld_event_at',available_at_field_id='fld_known_at',timezone='Asia/Shanghai')


def _table(con,*,late=False):
    rows=[
     (1,'eqA','2026-01-01 10:00:00','2026-01-01 10:00:03','2026-01-01 10:05:00',2.,10.,0.,'2026-01-01 11:00:00'),
     (2,'eqA','2026-01-01 10:01:00','2026-01-01 10:01:03','2026-01-01 10:05:00',4.,20.,1.,'2026-01-01 11:00:00'),
     (3,'eqB','2026-01-01 10:00:00','2026-01-01 10:00:03','2026-01-01 10:05:00',100.,30.,0.,'2026-01-01 11:00:00'),
     (4,'eqA','2026-01-01 10:02:00','2026-01-01 10:02:03','2026-01-01 10:05:00',6.,40.,1.,'2026-01-01 11:00:00')]
    if late:
        prior=list(rows[0]);prior[3]='2026-01-01 10:30:00';prior[4]='2026-01-01 11:00:00'
        rows[0]=tuple(prior)
    con.execute('''CREATE TABLE observations (__tp_rowid INTEGER,device VARCHAR,event_at TIMESTAMP,
        known_at TIMESTAMP,decide_at TIMESTAMP,A8 DOUBLE,A10 DOUBLE,target DOUBLE,label_known TIMESTAMP)''')
    con.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',rows)
    con.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM observations')


def test_true_duckdb_add_and_entity_rolling_mean():
    contract=_contract()
    spec=FeatureSpecV2(name='device features',source=SourceRef(asset_id='source-1',
        version_id='base',source_sha256='1'*64),contract_revision=2,features=[
        FeatureDefinition(feature_id='add',output_name='sum_signals',operator_id='arithmetic.binary',
            input_field_ids=['fld_A8','fld_A10'],parameters={'operator':'+'}),
        FeatureDefinition(feature_id='rolling',output_name='A8_ma2',operator_id='rolling.mean',
            input_field_ids=['fld_A8'],parameters={'window':2,'min_periods':2,
                 'order_field_id':'fld_event_at'})])
    with duckdb.connect(':memory:') as con:
        _table(con)
        rel=FeatureCompiler(contract,spec).compile_views(con)
        result=con.execute(f'SELECT __tp_rowid,sum_signals,A8_ma2 FROM {rel} ORDER BY __tp_rowid').fetchall()
        assert [(x[0],x[1]) for x in result]==[(1,12.0),(2,24.0),(3,130.0),(4,46.0)]
        assert math.isnan(result[0][2]) if isinstance(result[0][2],float) and math.isnan(result[0][2]) else result[0][2] is None
        assert result[1][2]==3.0
        assert result[2][2] is None
        assert result[3][2]==5.0
        time=temporal_diagnostics(con,contract)
        assert time['record_count']==4 and time['entity_count']==2
        assert time['status']=='verified' and time['scope']=='full'


def test_true_duckdb_point_in_time_late_history_gate():
    contract=_contract()
    task=TaskSpecV2(task_id='t',mode='supervised',task_type='binary_classification',
        entity_field_ids=['fld_device'],prediction_time_field_id='fld_decide_at',
        label_field_id='fld_target',label_available_at_field_id='fld_label_known')
    spec=FeatureSpecV2(name='causal',source=SourceRef(asset_id='source-1',
        version_id='base',source_sha256='1'*64),contract_revision=2,task=task,
        features=[FeatureDefinition(feature_id='ma',output_name='ma',operator_id='rolling.mean',
            input_field_ids=['fld_A8'],parameters={'window':2,'min_periods':2,
                'order_field_id':'fld_event_at'})])
    with duckdb.connect(':memory:') as con:
        _table(con)
        assert FeatureCompiler(contract,spec).validate_point_in_time(con)['status']=='verified'
    with duckdb.connect(':memory:') as con:
        _table(con,late=True)
        with pytest.raises(ValueError,match='窗口包含'):
            FeatureCompiler(contract,spec).validate_point_in_time(con)
