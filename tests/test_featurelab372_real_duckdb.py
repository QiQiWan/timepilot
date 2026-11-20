"""Independent, real DuckDB SQL reconciliation across versions and filters.

When duckdb is missing this module is SKIPPED, while the release gate is BLOCKED.
"""
import csv
import hashlib
from pathlib import Path
import pytest

duckdb=pytest.importorskip('duckdb',reason='物理验收必须使用真实 DuckDB，缺少时不得计通过')
from timepilot.feature_contracts import DataContract
from timepilot.feature_data_analysis import FeatureDataAnalysis

FIXED=Path(__file__).resolve().parents[1]/'benchmarks/featurelab_v0372/FIXED_DASHBOARD.csv'

class Catalog:
    def get(self,pid):
        assert pid=='fixture-project'
        return {'project_id':pid}

class Native:
    def __init__(self,files):self.files=files
    def verify(self,aid):return {'valid':aid=='fixture-asset'}
    def _connection(self):return duckdb.connect(':memory:')
    def _install_effective_view(self,db,aid,version,columns):
        assert aid=='fixture-asset'
        safe=str(self.files[version]).replace(chr(39), chr(39)*2)
        db.execute(f"CREATE VIEW __tp_effective AS SELECT * FROM read_parquet('{safe}')")

class Semantic:
    def __init__(self,files,contract):self.files=files;self.contract=contract
    def source(self,pid,aid,vid):
        return {'sha256':hashlib.sha256(self.files[vid].read_bytes()).hexdigest(),
                'row_count':12,'columns':[{'name':f.source_name} for f in self.contract.fields]}
    def latest(self,pid,aid,vid,source=None):
        return self.contract.model_copy(update={'source_sha256':source['sha256'],'version_id':vid})

@pytest.fixture()
def real(tmp_path):
    base=tmp_path/'base.parquet';new=tmp_path/'v2.parquet'
    with duckdb.connect(':memory:') as db:
        safe=str(FIXED).replace(chr(39),chr(39)*2)
        db.execute(f"""CREATE TABLE fixed AS SELECT event_at::TIMESTAMP event_at, device::VARCHAR device,
            measurement::DOUBLE measurement,temperature::DOUBLE temperature, quality::VARCHAR quality,
            secret::VARCHAR secret,ROW_NUMBER() OVER () __tp_rowid FROM read_csv_auto('{safe}')""")
        db.execute("COPY fixed TO '"+str(base).replace("'", "''")+"' (FORMAT PARQUET)")
        db.execute("COPY (SELECT event_at,device,measurement+20.0 measurement,temperature,quality,secret,__tp_rowid FROM fixed) TO '"+str(new).replace("'", "''")+"' (FORMAT PARQUET)")
    cols=[('field0','event_at','TIMESTAMP','event_time','confirmed'),
          ('field1','device','VARCHAR','entity_key','confirmed'),
          ('field2','measurement','DOUBLE','value','confirmed'),
          ('field3','temperature','DOUBLE','value','confirmed'),
          ('field4','quality','VARCHAR','regime','confirmed'),
          ('field5','secret','VARCHAR','unspecified','unconfirmed')]
    contract=DataContract(asset_id='fixture-asset',version_id='base',
        source_sha256=hashlib.sha256(base.read_bytes()).hexdigest(),revision=2,
        state='confirmed',event_time_field_id='field0',entity_field_ids=['field1'],
        timezone='UTC',fields=[{'field_id':fid,'source_name':name,'physical_type':ty,
            'source_column_index':int(fid[-1]),'role':role,'confirmation':confirmed,
            'semantic_type':'datetime' if role=='event_time' else 'numeric' if ty=='DOUBLE' else 'string',
            'classification':'restricted' if name=='secret' else 'internal',
            'timezone':'UTC' if role=='event_time' else None}
            for fid,name,ty,role,confirmed in cols])
    files={'base':base,'v2':new}; eda=FeatureDataAnalysis(Catalog(),Semantic(files,contract),None,Native(files))
    return eda,files


def test_exact_unfiltered_dashboard_and_independent_sql(real):
    eda,files=real
    d=eda.dashboard('fixture-project','fixture-asset',field_ids=['field2','field3','field4'],association_ids=['field2','field3'])
    with duckdb.connect(':memory:') as db:
        n,nonnull,mean,median,std,count_unique,neg=db.execute('''SELECT COUNT(*),COUNT(measurement),AVG(measurement),MEDIAN(measurement),
          STDDEV_POP(measurement),COUNT(DISTINCT quality),COUNT(*) FILTER (WHERE measurement IS NULL)
          FROM read_parquet(?)''',[str(files['base'])]).fetchone()
        corr=db.execute('SELECT CORR(measurement,temperature) FROM read_parquet(?)',[str(files['base'])]).fetchone()[0]
    m=next(x for x in d['fields'] if x['name']=='measurement')
    assert d['rows_filtered']==n==12 and d['rows_total']==12
    assert m['non_null']==nonnull==11 and m['null_count']==neg==1
    assert m['mean']==pytest.approx(mean) and m['median']==median and m['stddev']==pytest.approx(std)
    assert next(f for f in d['fields'] if f['name']=='quality')['distinct_count']==count_unique
    assert d['correlations'][0]['pearson']==pytest.approx(corr)
    assert all(f['name']!='secret' for f in d['fields'])
    assert d['metrics']['missing_cells']==1


def test_all_chart_numbers_and_filtered_same_scope(real):
    eda,files=real
    filters=[{'field_id':'field1','op':'eq','value':'A'},
             {'field_id':'field2','op':'gte','value':14}]
    d=eda.dashboard('fixture-project','fixture-asset',filters=filters,field_ids=['field2','field3'])
    with duckdb.connect(':memory:') as db:
        sql='SELECT COUNT(*), AVG(measurement), COUNT(DISTINCT temperature) FROM read_parquet(?) WHERE device=? AND measurement>=?'
        n,avg,unique=db.execute(sql,[str(files['base']),'A',14]).fetchone()
    assert d['rows_filtered']==n==4
    assert next(f for f in d['fields'] if f['name']=='measurement')['mean']==pytest.approx(avg)
    assert next(f for f in d['fields'] if f['name']=='temperature')['distinct_count']==unique
    for kind,fid,other in [('histogram','field2',None),('category','field4',None),('box','field2',None),('scatter','field2','field3'),('trend','field2',None)]:
        c=eda.dashboard_chart('fixture-project','fixture-asset',filters=filters,kind=kind,field_id=fid,other_field_id=other)
        assert c['scope_sha256']==d['scope_sha256']
        assert c['source_sha256']==d['source_sha256']
        if kind in ('histogram','category'):
            assert sum(p['count'] for p in c['points'])==n
        if kind=='box':
            with duckdb.connect(':memory:') as db:
                median=db.execute('SELECT MEDIAN(measurement) FROM read_parquet(?) WHERE device=? AND measurement>=?',
                    [str(files['base']),'A',14]).fetchone()[0]
            assert c['stats']['p50']==median
        if kind=='scatter':assert c['pair_count']==len(c['points'])==n
        if kind=='trend':assert sum(p['count'] for p in c['points'])==n


def test_version_switch_changes_sha_and_never_uses_old_scope(real):
    eda,files=real
    before=eda.dashboard('fixture-project','fixture-asset','base',field_ids=['field2'])
    after=eda.dashboard('fixture-project','fixture-asset','v2',field_ids=['field2'])
    a=before['fields'][0]['mean'];b=after['fields'][0]['mean']
    assert b==pytest.approx(a+20.0)
    assert after['source_sha256']!=before['source_sha256']
    assert after['scope_sha256']!=before['scope_sha256']
    with pytest.raises(ValueError,match='版本'):
        eda.dashboard('fixture-project','fixture-asset','v2',expected_sha256=before['source_sha256'])
    with pytest.raises(ValueError):
        eda.dashboard_chart('fixture-project','fixture-asset',kind='category',field_id='field5')


def test_all_aggregate_statistics_match_independent_duckdb_sql(real):
    eda, files = real
    results = eda.dashboard('fixture-project', 'fixture-asset',
                            field_ids=['field0', 'field1', 'field2', 'field3', 'field4'],
                            association_ids=['field2', 'field3'])

    def equal(actual, expected):
        if actual is None or expected is None:
            assert actual is None and expected is None
        elif isinstance(expected, (int, float)):
            assert actual == pytest.approx(expected, abs=1e-8)
        else:
            assert actual == expected

    with duckdb.connect(':memory:') as db:
        safe=str(files['base']).replace(chr(39),chr(39)*2)
        db.execute(f"CREATE TABLE raw AS SELECT * FROM read_parquet('{safe}')")
        assert results['rows_total'] == db.execute('SELECT COUNT(*) FROM raw').fetchone()[0]
        for field in results['fields']:
            name = field['name']
            column = '"' + name.replace('"', '""') + '"'
            nonnull, distinct = db.execute(f'SELECT COUNT({column}),COUNT(DISTINCT {column}) FROM raw').fetchone()
            assert field['non_null'] == nonnull
            assert field['null_count'] == 12 - nonnull
            assert field['distinct_count'] == distinct
            equal(field['null_rate'], (12 - nonnull) / 12)
            if field['numeric']:
                v = f'CASE WHEN isfinite(TRY_CAST({column} AS DOUBLE)) THEN TRY_CAST({column} AS DOUBLE) END'
                row = db.execute(f'SELECT AVG({v}),STDDEV_POP({v}),MIN({v}),MAX({v}),'
                    f'QUANTILE_CONT({v},0.1),QUANTILE_CONT({v},0.25),MEDIAN({v}),'
                    f'QUANTILE_CONT({v},0.75),QUANTILE_CONT({v},0.9),COUNT({v}) FROM raw').fetchone()
                for key, val in zip(['mean', 'stddev', 'min', 'max', 'p10', 'p25',
                                      'median', 'p75', 'p90', 'finite_count'], row):
                    equal(field[key], val)
        missing = db.execute('''SELECT COUNT(*) FILTER (WHERE event_at IS NULL)+
           COUNT(*) FILTER (WHERE device IS NULL)+COUNT(*) FILTER (WHERE measurement IS NULL)+
           COUNT(*) FILTER (WHERE temperature IS NULL)+COUNT(*) FILTER (WHERE quality IS NULL) FROM raw''').fetchone()[0]
        assert results['metrics']['missing_cells'] == missing
        equal(results['metrics']['missing_percent'], round(100 * missing / (12 * 5), 5))
        equal(results['metrics']['coverage_percent'], 100)
        for field, kind in [('field2', 'histogram'), ('field4', 'category'), ('field2', 'box'),
                            ('field2', 'quantiles'), ('field2', 'trend')]:
            chart = eda.dashboard_chart('fixture-project', 'fixture-asset', kind=kind, field_id=field)
            assert chart['scope_sha256'] == results['scope_sha256']
            if kind == 'histogram':
                # Independent SQL uses the returned bucket boundaries to verify every count.
                points = chart['points']
                low, high = points[0]['lower'], points[-1]['upper']
                width = (high-low)/len(points)
                counts = dict(db.execute('''SELECT CAST(GREATEST(0,LEAST(?,FLOOR((measurement-?)/?))) AS INT),COUNT(*)
                    FROM raw WHERE measurement IS NOT NULL GROUP BY 1''',
                    [len(points)-1,low,width]).fetchall())
                for i, item in enumerate(points):
                    assert item['count'] == counts.get(i, 0)
            elif kind == 'category':
                expected = dict(db.execute('SELECT quality,COUNT(*) FROM raw GROUP BY quality').fetchall())
                assert {p['label']:p['count'] for p in chart['points']} == expected
            elif kind in ('box', 'quantiles'):
                expected = db.execute('SELECT MIN(measurement), MAX(measurement),MEDIAN(measurement) FROM raw').fetchone()
                for key, val in zip(['min','max','p50'],expected):
                    equal(chart['stats'][key], val)
            elif kind == 'trend':
                independent = db.execute('''SELECT DATE_TRUNC('day',event_at),AVG(measurement),COUNT(measurement)
                    FROM raw GROUP BY 1 ORDER BY 1''').fetchall()
                assert len(chart['points']) == len(independent)
                for got, want in zip(chart['points'],independent):
                    equal(got['value'],want[1])
                    assert got['count'] == want[2]


def test_v0373_real_sql_to_evidence_and_report_same_scope(real):
    """A genuine DuckDB Parquet result is the only source of all exported claims."""
    from timepilot.feature_evidence_v0373 import dashboard_explanations,dashboard_markdown
    eda,files=real
    filters=[{'field_id':'field1','op':'eq','value':'A'},
             {'field_id':'field2','op':'gte','value':14}]
    data=eda.dashboard('fixture-project','fixture-asset',filters=filters,
        field_ids=['field2','field3'],association_ids=['field2','field3'])
    scope=dashboard_explanations(data)
    assert scope['status']=='supported'
    with duckdb.connect(':memory:') as db:
        n=db.execute('SELECT COUNT(*) FROM read_parquet(?) WHERE device=? AND measurement>=?',
                     [str(files['base']),'A',14]).fetchone()[0]
    assert n==data['rows_filtered']==4
    assert any(c['code']=='filtered-coverage' for c in scope['claims'])
    for claim in scope['claims']:
        assert claim['scope']['source_sha256']==data['source_sha256']
        assert claim['scope']['scope_sha256']==data['scope_sha256']
    bundle=dashboard_markdown(data)
    assert bundle['claim_ids']==[c['id'] for c in scope['claims']]
    assert hashlib.sha256(bundle['markdown'].encode()).hexdigest()==bundle['report_sha256']
    v2=eda.dashboard('fixture-project','fixture-asset','v2',filters=filters,
        field_ids=['field2','field3'],association_ids=['field2','field3'])
    assert v2['source_sha256']!=data['source_sha256']
    assert v2['scope_sha256']!=data['scope_sha256']
    assert [c['id'] for c in v2['evidence_analysis']['claims']]!=bundle['claim_ids']
