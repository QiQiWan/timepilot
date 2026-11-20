"""Real DuckDB/Parquet metric reconciliation at 506x31 and 12x5 scales.

When no actual DuckDB is installed, pytest skip and the release acceptance
script MUST treat this as blocked; no fabricated SQL engine or data.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import pytest

duckdb=pytest.importorskip('duckdb',reason='physical DuckDB/Parquet required for release')
from timepilot.feature_contracts import DataContract
from timepilot.feature_data_analysis import FeatureDataAnalysis

class Catalog:
    def get(self,pid):return {'project_id':pid}

class Native:
    def __init__(self,file):self.file=file
    def verify(self,aid):return {'valid':True}
    def _connection(self):return duckdb.connect(':memory:')
    def _install_effective_view(self,db,aid,vid,columns):
        safe=str(self.file).replace(chr(39),chr(39)*2)
        db.execute(f"CREATE VIEW __tp_effective AS SELECT * FROM read_parquet('{safe}')")

class Semantic:
    def __init__(self,file,contract,rows):self.file=file;self.contract=contract;self.rows=rows
    def source(self,pid,aid,vid):
        return {'sha256':hashlib.sha256(self.file.read_bytes()).hexdigest(),
                'row_count':self.rows,'columns':[{'name':f.source_name} for f in self.contract.fields]}
    def latest(self,pid,aid,vid,source=None):
        return self.contract.model_copy(update={'source_sha256':source['sha256']})

@pytest.mark.parametrize('row_count,width',[ (12,5), (506,31) ])
def test_parquet_overview_null_nan_inf_filtered_denominators(tmp_path,row_count,width):
    out=tmp_path/'fixed.parquet'
    names=[f'measure_{i}' for i in range(width)]
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE industrial ('+','.join('"'+x+'" DOUBLE' for x in names)+')')
        rows=[]
        for r in range(row_count):
            vals=[float(r+i) for i in range(width)]
            if r==0:vals[0]=None
            if r==1:vals[0]=float('nan')
            if r==2:vals[0]=float('inf')
            if r==3 and width>=4:vals[3]=float('-inf')
            rows.append(tuple(vals))
        db.executemany('INSERT INTO industrial VALUES ('+','.join('?' for _ in names)+')',rows)
        safe=str(out).replace(chr(39),chr(39)*2)
        db.execute(f"COPY industrial TO '{safe}' (FORMAT PARQUET)")
    fields=[{'field_id':f'f{i}','source_name':name,'physical_type':'DOUBLE',
             'source_column_index':i,'role':'value','classification':'internal'} for i,name in enumerate(names)]
    contract=DataContract(asset_id='fixture',version_id='base',source_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
        revision=1,state='confirmed',fields=fields)
    service=FeatureDataAnalysis(Catalog(),Semantic(out,contract,row_count),None,Native(out))
    selected=[f'f{i}' for i in range(min(width,24))]
    report=service.dashboard('test','fixture',field_ids=selected,association_ids=[])
    metrics=report['metrics']
    with duckdb.connect(':memory:') as db:
        safe=str(out).replace(chr(39),chr(39)*2)
        db.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{safe}')")
        clauses=[]
        for col in names[:24]:
            q='"'+col+'"'
            clauses.extend([f'COUNT(*)-COUNT({q})',f'COUNT({q})-COUNT(TRY_CAST({q} AS DOUBLE))',
                f'COUNT(TRY_CAST({q} AS DOUBLE))-COUNT(CASE WHEN isfinite(TRY_CAST({q} AS DOUBLE)) THEN 1 END)'])
        oracle=db.execute('SELECT '+','.join(clauses)+' FROM raw').fetchone()
    missing=sum(oracle[::3]);conversion=sum(oracle[1::3]);nonfinite=sum(oracle[2::3]);denominator=row_count*len(selected)
    assert metrics['missing_cells']==missing
    assert metrics['conversion_failed_cells']==conversion
    assert metrics['non_finite_numeric_cells']==nonfinite
    assert metrics['invalid_numeric_cells']==conversion+nonfinite
    assert metrics['missing_percent']==round(100*(missing+conversion+nonfinite)/denominator,5)
    assert metrics['columns']==width and metrics['selected_fields']==len(selected)
    assert metrics['metric_scope']['denominator_cells']==denominator
    assert len(metrics['metric_scope']['field_ids'])<=24
    # Test both a valid filter and a rejected restricted/unknown field.
    filtered=service.dashboard('test','fixture',filters=[{'field_id':'f1','op':'gte','value':10}],field_ids=selected,association_ids=[])
    assert filtered['metrics']['records']<row_count
    assert filtered['metrics']['metric_scope']['denominator_cells']==filtered['metrics']['records']*len(selected)
    with pytest.raises(ValueError):service.dashboard('test','fixture',filters=[{'field_id':'not-a-field','op':'eq','value':1}])
