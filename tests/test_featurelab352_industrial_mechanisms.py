"""v0.35.2: deterministic industrial reset semantics and fail-closed contracts.

SQLite executes the exact SQL window definitions where supported; the project
DuckDB/Parquet gate is separate and must pass on the deployment machine.
"""
from __future__ import annotations
import sqlite3
import re
import pytest

from timepilot.feature_contracts import DataContract,FieldContract,FeatureDefinition,FeatureSpecV2,SourceRef
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_operator_registry import discover
from timepilot.feature_industrial import eligible_reset_features


def field(name,index,typ,semantic,role='value',unit=None,evidence=None):
    return FieldContract(field_id='fld_'+name,source_name=name,source_column_index=index,
        physical_type=typ,semantic_type=semantic,role=role,unit=unit,
        confirmation='confirmed',classification='internal',
        timezone='UTC' if semantic=='datetime' else None,evidence=evidence or [])


def contract():
    fields=[field('device',0,'VARCHAR','id','entity_key'),
            field('t',1,'TIMESTAMP','datetime','event_time'),
            field('pressure',2,'DOUBLE','numeric',unit='kPa'),
            field('increment',3,'DOUBLE','numeric',unit='kg'),
            field('maintenance',4,'BOOLEAN','event',evidence=['产线维护日志与业务人员确认的维护复位规则']),
            field('regime',5,'VARCHAR','category','regime')]
    return DataContract(asset_id='asset',version_id='base',source_sha256='a'*64,
        revision=4,state='confirmed',fields=fields,entity_field_ids=['fld_device'],
        event_time_field_id='fld_t',timezone='UTC')


def definition(op='industrial.reset_mean',input_id='fld_pressure',**param):
    opts={'order_field_id':'fld_t','partition_field_ids':['fld_device'],
          'reset_field_id':'fld_maintenance','regime_field_id':'fld_regime',
          'mechanism_reference':'产线维护日志与业务人员确认的维护复位规则'}
    opts.update(param)
    if op=='industrial.reset_sum':opts.setdefault('incremental_confirmed',True)
    return FeatureDefinition(feature_id='feat_test',output_name='mechanism_output',
        operator_id=op,operator_version='0.35.2',input_field_ids=[input_id],parameters=opts)


def compiler(c,feat):
    return FeatureCompiler(c,FeatureSpecV2(name='industrial',source=SourceRef(
        asset_id=c.asset_id,version_id=c.version_id,source_sha256=c.source_sha256),
        contract_revision=c.revision,features=[feat]))


class SQLiteWindowCompat:
    """SQLite implements SQL windows but lacks DuckDB's TRY_CAST keyword."""
    def __init__(self, connection):self.connection=connection
    def execute(self, statement, parameters=()):
        statement=re.sub(r'TRY_CAST\(("[^"]+") AS DOUBLE\)',
                         r'CAST(\1 AS REAL)', statement)
        return self.connection.execute(statement,parameters)


def setup():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE records(device TEXT,t TEXT,pressure REAL,increment REAL,maintenance INTEGER,regime TEXT,__tp_rowid INTEGER)')
    rows=[('a','2026-01-01T00:00:00',10,1,1,'A',1),
          ('a','2026-01-01T00:01:00',20,2,0,'A',2),
          ('a','2026-01-01T00:02:00',30,3,0,'B',3),
          ('a','2026-01-01T00:03:00',40,4,1,'B',4),
          ('a','2026-01-01T00:04:00',50,5,0,'A',5),
          ('a','2026-01-01T00:05:00',60,6,0,'A',6),
          ('b','2026-01-01T00:00:00',999,9,0,'A',7),
          ('b','2026-01-01T00:01:00',100,2,1,'A',8)]
    db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?)',rows)
    db.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM records')
    return SQLiteWindowCompat(db)


def test_reset_mean_handles_maintenance_and_noncontiguous_aba_regimes():
    c=contract();db=setup();view=compiler(c,definition()).compile_views(db)
    values=db.execute(f'SELECT device,mechanism_output FROM {view} ORDER BY device,t').fetchall()
    assert values==[('a',10),('a',15),('a',30),('a',40),('a',50),('a',55),('b',None),('b',100)]
    assert any(x['operator_id']=='industrial.reset_mean' and x['available'] for x in discover(contract=c))
    assert eligible_reset_features(c)


def test_cumulative_increment_only_resets_at_actual_transitions():
    c=contract();db=setup()
    view=compiler(c,definition('industrial.reset_sum',input_id='fld_increment')).compile_views(db)
    assert [r[0] for r in db.execute(f'SELECT mechanism_output FROM {view} ORDER BY device,t')]==[1,3,3,4,5,11,None,2]


def test_mechanism_requires_verified_units_and_increment_attestation():
    c=contract()
    with pytest.raises(ValueError,match='瞬时读数'):
        compiler(c,definition('industrial.reset_sum',input_id='fld_pressure'))
    with pytest.raises(ValueError,match='人工确认|增量'):
        compiler(c,definition('industrial.reset_sum',input_id='fld_increment',incremental_confirmed=False))
    with pytest.raises(ValueError,match='来源'):
        compiler(c,definition(mechanism_reference='猜测'))
    with pytest.raises(ValueError,match='按真实事件时间'):
        compiler(c,definition(order_field_id='fld_device'))
    with pytest.raises(ValueError,match='设备'):
        compiler(c,definition(partition_field_ids=[]))
    with pytest.raises(ValueError,match='工况'):
        compiler(c,definition(regime_field_id='fld_pressure'))
    c2=c.model_copy(update={'fields':[f.model_copy(update={'unit':None}) if f.field_id=='fld_pressure'
                                         else f for f in c.fields]})
    with pytest.raises(ValueError,match='工程单位'):
        compiler(c2,definition())


def test_null_reset_or_regime_and_nonunique_time_fail_closed():
    c=contract();db=setup()
    db.execute("UPDATE records SET maintenance=NULL WHERE device='a' AND __tp_rowid=2")
    with pytest.raises(ValueError,match='空值'):
        compiler(c,definition()).compile_views(db)
    db.execute("UPDATE records SET maintenance=0 WHERE device='a' AND __tp_rowid=2")
    db.execute("UPDATE records SET t='2026-01-01T00:00:00' WHERE device='a' AND __tp_rowid=2")
    with pytest.raises(ValueError,match='重复'):
        compiler(c,definition()).compile_views(db)


def test_unconfirmed_maintenance_does_not_automatically_generate_mechanism():
    c=contract()
    c2=c.model_copy(update={'fields':[f.model_copy(update={'confirmation':'proposed'}) if f.field_id=='fld_maintenance'
                                          else f for f in c.fields]})
    assert eligible_reset_features(c2)==[]
    with pytest.raises(ValueError,match='重置标记'):
        compiler(c2,definition())


def test_adaptive_candidates_include_reset_feature_only_with_manual_evidence():
    from timepilot.feature_strategy import plan_candidates
    from timepilot.feature_candidates import propose
    c=contract()
    plan=plan_candidates(c,budget=32,objective='forecast')
    assert any(x['operator_id']=='industrial.reset_mean' for x in plan['proposals'])
    proposals=propose(c,template='adaptive',budget=32,objective='forecast')
    assert any(p['operator_id']=='industrial.reset_mean' for p in proposals)
    no_evidence=c.model_copy(update={'fields':[f.model_copy(update={'evidence':[]})
                                                if f.field_id=='fld_maintenance' else f for f in c.fields]})
    assert not any(x['operator_id']=='industrial.reset_mean'
                   for x in plan_candidates(no_evidence,budget=32,objective='forecast')['proposals'])
    with pytest.raises(ValueError,match='证据'):
        compiler(no_evidence,definition())
