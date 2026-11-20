"""Real DuckDB/Parquet regression: not substituted by a mock when missing."""
import pytest

duckdb=pytest.importorskip('duckdb',reason='真实 DuckDB/Parquet 未安装时必须 BLOCKED')
from test_featurelab372_real_duckdb import real  # same fixed 12-row real-Parquet fixture
from timepilot.feature_dashboard_sql import category_sql


def test_both_chart_apis_category_use_real_duckdb(real):
    eda,_=real
    modern=eda.dashboard_chart('fixture-project','fixture-asset',kind='category',field_id='field4')
    legacy=eda.chart('fixture-project','fixture-asset','base','field4',mode='category')
    expected=[(item['label'],item['count']) for item in modern['points'] if not item.get('other')]
    assert expected and expected==[(item['label'],item['count']) for item in legacy['points']]
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE __tp_effective ("odd""field" VARCHAR)')
        db.executemany('INSERT INTO __tp_effective VALUES (?)',[('A',),('A',),('B',)])
        assert db.execute(category_sql('odd"field',base='FROM __tp_effective WHERE TRUE',limit=10)).fetchall()==[('A',2),('B',1)]


def test_tukey_whiskers_and_outliers_match_independent_sql(real):
    eda,files=real
    overview=eda.dashboard('fixture-project','fixture-asset',field_ids=['field2'],association_ids=[])
    entry=overview['fields'][0]
    assert entry['tukey_status']=='computed'
    with duckdb.connect(':memory:') as db:
        row=db.execute('SELECT QUANTILE_CONT(measurement,.25),QUANTILE_CONT(measurement,.75) FROM read_parquet(?)',[str(files['base'])]).fetchone()
        lo,hi=row[0]-1.5*(row[1]-row[0]),row[1]+1.5*(row[1]-row[0])
        expected=db.execute('''SELECT MIN(measurement) FILTER (WHERE measurement BETWEEN ? AND ?),
           MAX(measurement) FILTER (WHERE measurement BETWEEN ? AND ?),
           COUNT(*) FILTER (WHERE measurement IS NOT NULL AND (measurement<? OR measurement>?))
           FROM read_parquet(?)''',[lo,hi,lo,hi,lo,hi,str(files['base'])]).fetchone()
    assert entry['whisker_low']==pytest.approx(expected[0])
    assert entry['whisker_high']==pytest.approx(expected[1])
    assert entry['outlier_count']==expected[2]
    card=eda.dashboard_chart('fixture-project','fixture-asset',kind='box',field_id='field2')
    assert card['stats']['outlier_count']==entry['outlier_count']
    assert card['stats']['whisker_low']==entry['whisker_low']


def test_spearman_paired_average_ranks_and_cache_invalidation(real):
    from statistics import correlation
    eda,files=real
    def ranks(xs):
        order=sorted(range(len(xs)),key=lambda i:xs[i]);out=[0.0]*len(xs)
        i=0
        while i<len(order):
            j=i+1
            while j<len(order) and xs[order[j]]==xs[order[i]]:j+=1
            for k in range(i,j):out[order[k]]=(i+1+j)/2
            i=j
        return out
    current=eda.dashboard('fixture-project','fixture-asset',field_ids=['field2','field3'],
        association_ids=['field2','field3'],association_method='spearman')
    with duckdb.connect(':memory:') as db:
        pairs=db.execute('SELECT measurement,temperature FROM read_parquet(?) WHERE measurement IS NOT NULL AND temperature IS NOT NULL',[str(files['base'])]).fetchall()
    expected=correlation(ranks([x for x,_ in pairs]),ranks([y for _,y in pairs]))
    assert current['correlations'][0]['spearman']==pytest.approx(expected,abs=1e-10)
    second=eda.dashboard('fixture-project','fixture-asset',field_ids=['field2','field3'],
        association_ids=['field2','field3'],association_method='spearman')
    assert second['performance']['cache_hit'] is True
    newer=eda.dashboard('fixture-project','fixture-asset','v2',field_ids=['field2','field3'],
        association_ids=['field2','field3'],association_method='spearman')
    assert newer['source_sha256']!=current['source_sha256']
    assert newer['performance']['cache_hit'] is False
