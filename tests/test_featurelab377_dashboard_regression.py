"""v0.37.5-7 SQL template, diagnostics, scope and statistical safety checks."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from timepilot.feature_dashboard_sql import category_sql,CATEGORY_TEMPLATE_ID
from timepilot.feature_dashboard_v0372 import DataOverviewDashboard,_time
from timepilot.feature_contracts import FieldContract
from timepilot.system_logs import setup_logging,log_failure,read_logs


def test_legacy_and_modern_category_share_one_quoted_non_reserved_template():
    sql=category_sql('A" 1',base='FROM __tp_effective WHERE TRUE',limit=15)
    assert ' AS category_value' in sql and ' AS category_count' in sql
    assert ' AS label' not in sql and CATEGORY_TEMPLATE_ID=='tp.category.counts.v2'
    assert '"A"" 1"' in sql
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE __tp_effective ("A"" 1" VARCHAR)')
    db.executemany('INSERT INTO __tp_effective VALUES (?)', [('B',),('A',),('B',),(None,)])
    assert db.execute(sql).fetchall()==[('B',2),('A',1)]
    with pytest.raises(ValueError,match='授权'):
        category_sql('A',base='FROM unrelated WHERE TRUE',limit=15)
    with pytest.raises(ValueError,match='类别数量'):
        category_sql('A',base='FROM __tp_effective WHERE TRUE',limit=500)


def test_calendar_time_disallows_sequence_and_time_of_day():
    def f(typ):
        return FieldContract(field_id='field000',source_name='Time',physical_type=typ,source_column_index=0)
    assert not _time(f('TIME')) and not _time(f('INTEGER')) and not _time(f('VARCHAR'))
    assert not _time(f('UPDATED_TIMESTAMP_FLAG'))
    assert _time(f('TIMESTAMP')) and _time(f('DATE'))


def test_real_incident_count_and_chart_specific_fingerprint(tmp_path):
    setup_logging(tmp_path)
    def error():
        try:raise RuntimeError('password=SECRET different source rows')
        except RuntimeError as exc:return exc
    ids=[log_failure(error(),stage='dashboard.chart',context={'kind':'category','sql_template_id':CATEGORY_TEMPLATE_ID}) for _ in range(3)]
    other=log_failure(error(),stage='dashboard.chart',context={'kind':'scatter','sql_template_id':'tp.scatter.v1'})
    assert len(set(ids))==1 and other!=ids[0]
    data=read_logs(tmp_path,level='ERROR')
    assert {g['diagnostic_id']:g['count'] for g in data['error_groups']}[ids[0]]==3
    assert sum(bool(x.get('stack')) for x in data['items'] if x.get('diagnostic_id')==ids[0])==1
    assert 'SECRET' not in json.dumps(data)


def test_evidence_zip_download_and_code_contract(tmp_path):
    from timepilot.api import create_app
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        endpoint='/api/v1/system/logs/download?format=zip'
        reply=client.get(endpoint)
        assert reply.status_code==200
        assert reply.headers['content-type'].startswith('application/zip')
        import zipfile,io
        with zipfile.ZipFile(io.BytesIO(reply.content)) as z:
            manifest=json.loads(z.read('summary.json'))
            assert manifest['exported']>=0
            assert 'jsonl_sha256' in manifest


def test_ui_can_explain_unconfirmed_time_without_500(tmp_path):
    from timepilot.api import create_app
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        bad=client.post('/api/v1/projects/no-such/feature-lab/data-analysis/dashboard/chart',
            json={'asset_id':'asset','field_id':'unknown','kind':'scatter'})
        assert bad.status_code in (404,409,422)
        routes={r.path for r in client.app.routes}
        assert '/api/v1/projects/{pid}/feature-lab/data-analysis/chart-capabilities' in routes


def test_source_pages_use_real_mounting_and_bounded_card_query_queue():
    root=Path(__file__).resolve().parents[1]/'src/timepilot/static'
    dashboard=(root/'data-dashboard-v0372.js').read_text()
    analysis=(root/'data-analysis-ui.js').read_text()
    workspace=(root/'analysis-workspace.js').read_text()
    assert 'requestCharts(z,[i])' in dashboard
    assert 'z.activeChartRequests<2' in dashboard
    assert 'job.token!==z.cardTokens[job.id]' in dashboard
    assert 'cardResult(z,id)' in dashboard
    assert 'sql_template_id' in (Path(__file__).resolve().parents[1]/'src/timepilot/system_logs.py').read_text()
    assert "if(['overview','quality','statistics'].includes(task))" in analysis
    assert 'node.__tpEnsureFeature' in workspace


def test_production_budget_guards_exposed():
    service=DataOverviewDashboard(None)
    assert service.MAX_CORR_FIELDS==10
    assert service.MAX_SPEARMAN_FIELDS==6
    assert service.MAX_SPEARMAN_ROWS==100_000
    assert 0<service.CACHE_TTL_SECONDS<=120
    assert service.CACHE_MAX_ENTRIES<=64


def test_single_field_box_renderer_uses_tukey_whisker_not_extrema():
    root=Path(__file__).resolve().parents[1]/'src/timepilot/static'
    body=(root/'data-dashboard-v0372.js').read_text()
    assert 'scale(s.whisker_low??s.min)' in body
    assert 'scale(s.whisker_high??s.max)' in body
    assert '离群点' in body


def test_trend_visibility_requires_calendar_date_not_confirmed_sequence():
    root=Path(__file__).resolve().parents[1]
    backend=(root/'src/timepilot/feature_dashboard_v0372.py').read_text()
    assert 'fields[con.event_time_field_id].confirmation==\'confirmed\' and' in backend
    assert '_time(fields[con.event_time_field_id])' in backend
