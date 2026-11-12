"""Real DuckDB duration-window numerical check; never mocked or counted when skipped."""
from datetime import datetime,timedelta
import pytest
from timepilot.feature_contracts import DataContract,FeatureSpecV2,SourceRef,FeatureDefinition
from timepilot.feature_compiler import FeatureCompiler

duckdb=pytest.importorskip('duckdb',reason='requires real DuckDB installed in target env')


def test_duration_30min_grouped_irregular_and_missing_values():
    data=[
      ('eq-A','2026-11-11 10:00:00',2.),
      ('eq-A','2026-11-11 10:01:00',4.),
      ('eq-B','2026-11-11 10:01:00',100.),
      ('eq-A','2026-11-11 10:31:00',8.),
      ('eq-A','2026-11-11 11:40:00',None),
    ]
    c=DataContract(asset_id='a',version_id='base',source_sha256='9'*64,
      fields=[
       {'field_id':'fld_device','source_name':'device','source_column_index':0,'physical_type':'VARCHAR',
        'semantic_type':'id','role':'entity_key','confirmation':'confirmed'},
       {'field_id':'fld_event','source_name':'event_at','source_column_index':1,'physical_type':'TIMESTAMP',
        'semantic_type':'datetime','role':'event_time','confirmation':'confirmed'},
       {'field_id':'fld_A','source_name':'A','source_column_index':2,'physical_type':'DOUBLE',
        'semantic_type':'numeric','role':'value','confirmation':'confirmed'}],
      entity_field_ids=['fld_device'],event_time_field_id='fld_event',timezone='Asia/Shanghai',
      state='confirmed',revision=1)
    spec=FeatureSpecV2(name='duration',source=SourceRef(asset_id='a',version_id='base',source_sha256='9'*64),
      contract_revision=1,features=[FeatureDefinition(feature_id='ma30',output_name='ma_30m',
       operator_id='rolling.duration.mean',input_field_ids=['fld_A'],
       parameters={'window_seconds':1800,'min_periods':1,'order_field_id':'fld_event'})])
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE raw (__tp_rowid INTEGER,device VARCHAR,event_at TIMESTAMP,A DOUBLE)')
        db.executemany('INSERT INTO raw VALUES (?,?,?,?)',[(i,*x) for i,x in enumerate(data)])
        db.execute('CREATE TEMP VIEW __tp_effective AS SELECT * FROM raw')
        result_table=FeatureCompiler(c,spec).compile_views(db)
        result=db.execute(f'SELECT "__tp_rowid",ma_30m FROM {result_table} ORDER BY "__tp_rowid"').fetchall()
    # Independent time-indexed reference: interval is inclusive at 30min.
    out=[]
    for ix,(device,when,val) in enumerate(data):
        t=datetime.fromisoformat(when)
        values=[]
        for d,t0,v in data:
            if d==device and v is not None and 0 <= (t-datetime.fromisoformat(t0)).total_seconds() <= 1800:
                values.append(v)
        out.append(sum(values)/len(values) if values else None)
    for (_,value),expect in zip(result,out):
        assert value==pytest.approx(expect) if expect is not None else value is None
