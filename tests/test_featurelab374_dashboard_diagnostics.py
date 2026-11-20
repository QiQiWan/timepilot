"""v0.37.4 tests for scoped safe diagnostics, stage separation and matrix contracts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient
from timepilot.feature_data_analysis import FeatureDataAnalysis
from timepilot.feature_dashboard_v0372 import DataOverviewDashboard, _time
from timepilot.feature_contracts import FieldContract
from timepilot.system_logs import log_failure, read_logs, setup_logging

ROOT = Path(__file__).resolve().parents[1]


def test_correlation_matrix_limit_and_time_only_not_calendar():
    from timepilot.feature_lab_v2_api import DashboardQuery
    assert len(DashboardQuery(asset_id='a', association_ids=[str(i) for i in range(10)]).association_ids) == 10
    try:
        DashboardQuery(asset_id='a', association_ids=[str(i) for i in range(11)])
    except Exception:
        pass
    else:
        raise AssertionError('More than ten correlation fields must fail')
    assert not _time(FieldContract(field_id='field0',source_name='clock',physical_type='TIME',source_column_index=0))
    assert _time(FieldContract(field_id='field1',source_name='event',physical_type='TIMESTAMP',source_column_index=1))
    assert DataOverviewDashboard.MAX_CORR_FIELDS == 10


def _same_error():
    try:
        raise RuntimeError('api_key=SHOULD_NOT_SHOW invalid SQL expression')
    except RuntimeError as error:
        return error


def test_diagnostic_dedup_and_stack_redaction(tmp_path):
    setup_logging(tmp_path)
    first=log_failure(_same_error(),stage='dashboard.chart',context={'kind':'scatter'})
    second=log_failure(_same_error(),stage='dashboard.chart',context={'kind':'scatter'})
    third=log_failure(_same_error(),stage='dashboard.chart',context={'kind':'scatter'})
    assert first==second==third and first.startswith('TP-')
    result=read_logs(tmp_path,level='ERROR',keyword=first)
    assert result['matched']==3  # every event is durable and stack is recorded only once
    assert result['error_groups'][0]['count']==3
    item=next(x for x in result['items'] if x.get('stack'))
    assert item['error_code']=='unclassified_execution_error'
    assert any(x['file']=='test_featurelab374_dashboard_diagnostics.py' for x in item['stack'])
    assert all('/mnt/data' not in x['file'] for x in item['stack'])
    contents=(tmp_path/'logs/timepilot.log').read_text()
    assert 'SHOULD_NOT_SHOW' not in contents
    assert 'dashboard.chart' in contents


def test_dashboard_unexpected_error_returns_actionable_diagnostic(tmp_path,monkeypatch):
    from timepilot.api import create_app
    def broken(*args,**kwargs):
        raise RuntimeError('unsupported dashboard query')
    monkeypatch.setattr(FeatureDataAnalysis,'dashboard_chart',broken)
    with TestClient(create_app(home=tmp_path, embedded_worker=False)) as client:
        r=client.post('/api/v1/projects/p/feature-lab/data-analysis/dashboard/chart', json={
            'asset_id':'asset','field_id':'field0','kind':'scatter','other_field_id':'field1'})
        assert r.status_code==500
        detail=r.json()['detail']
        assert detail['code']=='unclassified_execution_error'
        assert detail['diagnostic_id'].startswith('TP-')
        assert len(r.headers['x-request-id'])==12
        assert detail['request_id']==r.headers['x-request-id']
        filtered=client.get('/api/v1/system/logs',params={'q':detail['diagnostic_id']})
        assert filtered.status_code==200
        assert filtered.json()['matched']>=1
        records=filtered.json()['items']
        assert any(x.get('operation')=='dashboard.chart' and x.get('stack') for x in records)
        assert not any('unsupported dashboard query' in json.dumps(x) for x in records)


def test_three_pages_separate_and_suppress_unneeded_dashboards():
    js=(ROOT/'src/timepilot/static/data-analysis-ui.js').read_text()
    css=(ROOT/'src/timepilot/static/analysis-workspace.css').read_text()
    workspace=(ROOT/'src/timepilot/static/analysis-workspace.js').read_text()
    assert 'tp-analysis-overview' in js
    assert 'tp-analysis-quality' in js
    assert 'tp-analysis-dashboard' in js
    assert "dataset.section==='statistics'" in js
    assert 'TimePilotDataAnalysisUI?.onSectionChange?.(section)' in workspace
    assert '[data-section="overview"] .tp-da-dashboard' in css
    assert '[data-section="statistics"] .tp-da-viz' in css
    assert '[data-section="statistics"] .tp-da-dashboard' in css


def test_failure_on_diagnostics_keeps_credentials_redacted(tmp_path):
    setup_logging(tmp_path)
    logging.getLogger('timepilot.dashboard').error('ERROR request: password=abc123; authorization=Bearer SECRET')
    found=read_logs(tmp_path,keyword='ERROR request')
    assert found['matched']>=1
    payload=json.dumps(found)
    assert 'abc123' not in payload and 'SECRET' not in payload
