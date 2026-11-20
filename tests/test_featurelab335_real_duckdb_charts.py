"""Physical DuckDB/Parquet acceptance: independent SQL vs data-analysis charts.

A skipped test is a BLOCKED production gate and must never count as passed.
No synthetic chart counts are injected: actual data is written to Parquet.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

import pytest

duckdb=pytest.importorskip('duckdb',reason='需要在安装了真实 DuckDB 的 Mac 环境执行')
from timepilot.feature_contracts import DataContract
from timepilot.feature_data_analysis import FeatureDataAnalysis


class RealNative:
    def __init__(self,path,expected):self.path=path;self.expected=expected
    def verify(self,_aid):
        return {'valid':hashlib.sha256(self.path.read_bytes()).hexdigest()==self.expected}
    def _connection(self):return duckdb.connect(':memory:')
    def _install_effective_view(self,connection,asset_id,version_id,columns):
        assert asset_id=='real-source' and version_id=='base'
        safe=str(self.path).replace("'","''")
        connection.execute(f"CREATE TEMP VIEW __tp_effective AS SELECT * FROM read_parquet('{safe}')")


class RealSemantic:
    def __init__(self,sha,contract):self.sha=sha;self.contract=contract
    def source(self,project_id,aid,vid):
        assert aid=='real-source' and vid=='base'
        return {'asset_id':aid,'version_id':vid,'sha256':self.sha,'row_count':6,
                'columns':[{'name':'measurement'},{'name':'device'},{'name':'event_at'}]}
    def latest(self,*args,**kwargs):return self.contract


def test_physical_duckdb_source_matches_histogram_category_and_time_chart(tmp_path):
    path=tmp_path/'real.parquet'
    with duckdb.connect(':memory:') as db:
        db.execute("""CREATE TABLE real AS SELECT i::BIGINT __tp_rowid,
          (10+i*2)::DOUBLE measurement,
          CASE WHEN i<=3 THEN 'A' ELSE 'B' END::VARCHAR device,
          TIMESTAMP '2026-01-01' + i*INTERVAL '1 day' event_at
          FROM range(1,7) t(i)""")
        db.execute(f"COPY real TO '{str(path)}' (FORMAT PARQUET)")
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    contract=DataContract(asset_id='real-source',version_id='base',source_sha256=sha,
        revision=1,state='confirmed',timezone='UTC',event_time_field_id='fld_time',fields=[
        {'field_id':'fld_measurement','source_name':'measurement','source_column_index':0,
         'physical_type':'DOUBLE','role':'value','confirmation':'confirmed'},
        {'field_id':'fld_device','source_name':'device','source_column_index':1,
         'physical_type':'VARCHAR','role':'entity_key','confirmation':'confirmed'},
        {'field_id':'fld_time','source_name':'event_at','source_column_index':2,
         'physical_type':'TIMESTAMP','semantic_type':'datetime','role':'event_time',
         'confirmation':'confirmed','timezone':'UTC'}])
    native=RealNative(path,sha)
    eda=FeatureDataAnalysis(None,RealSemantic(sha,contract),None,native)
    numeric=eda.chart('project','real-source','base','fld_measurement','distribution')
    categorical=eda.chart('project','real-source','base','fld_device','category')
    temporal=eda.chart('project','real-source','base','fld_measurement','series')
    with duckdb.connect(':memory:') as db:
        expected=db.execute('SELECT COUNT(*),MIN(measurement),MAX(measurement),SUM(measurement) FROM read_parquet(?)',[str(path)]).fetchone()
        groups=db.execute('SELECT device,COUNT(*) FROM read_parquet(?) GROUP BY device ORDER BY device',[str(path)]).fetchall()
        ordered=db.execute('SELECT measurement FROM read_parquet(?) ORDER BY event_at,__tp_rowid',[str(path)]).fetchall()
    assert numeric['finite_count']==expected[0]
    assert sum(item['count'] for item in numeric['points'])==expected[0]
    assert sum(item['count'] for item in categorical['points'])==expected[0]
    assert sorted((item['label'],item['count']) for item in categorical['points'])==groups
    assert [v['value'] for v in temporal['points']]==[v[0] for v in ordered]
    for result in (numeric,categorical,temporal):
        assert result['source_sha256']==sha
    with duckdb.connect(':memory:') as db:
        db.execute(f"COPY (SELECT 99.0 measurement, 'Z' device, TIMESTAMP '2026-01-01' event_at, 1 __tp_rowid) TO '{str(tmp_path/'overwrite.parquet')}' (FORMAT PARQUET)")
    path.write_bytes((tmp_path/'overwrite.parquet').read_bytes())
    with pytest.raises(ValueError,match='哈希'):
        eda.chart('project','real-source','base','fld_measurement','distribution')
