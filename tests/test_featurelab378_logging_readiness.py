"""v0.37.8 diagnostic aggregation and fail-closed availability signals."""
from __future__ import annotations

from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.system_logs import (
    dashboard_performance_summary,
    log_dashboard_performance,
    read_logs,
    setup_logging,
)


def test_dashboard_performance_percentiles_and_recent_scope(tmp_path):
    setup_logging(tmp_path)
    for ms in [10, 15, 30, 45, 50]:
        log_dashboard_performance(operation='dashboard.chart',chart_kind='histogram',milliseconds=ms,success=True)
    log_dashboard_performance(operation='dashboard.chart',chart_kind='histogram',milliseconds=80,success=False)
    log_dashboard_performance(operation='dashboard.overview',chart_kind='overview',milliseconds=12,success=True)
    result=dashboard_performance_summary(tmp_path)
    assert result['scope']=='retained_rotated_logs_only'
    assert result['events']==7
    chart=next(x for x in result['groups'] if x['chart_kind']=='histogram')
    assert chart['count']==6 and chart['errors']==1
    assert chart['p50_ms']==30 and chart['p95_ms']==80
    assert chart['error_rate']==round(1/6,6)
    assert result['first_observed'] and result['last_observed']
    events=read_logs(tmp_path,source='timepilot.dashboard.perf')
    assert events['matched']==7
    assert all('SELECT ' not in str(r) for r in events['items'])


def test_admin_summary_endpoint_and_readiness_not_equal_to_liveness(tmp_path):
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        health=client.get('/api/v1/health')
        ready=client.get('/api/v1/ready')
        assert health.status_code==200 and health.json()['status']=='ok'
        assert ready.status_code==503
        detail=ready.json()['detail']
        assert detail['ready'] is False
        assert detail['components']['embedded_workers'] is False
        assert detail['components']['native_bundle'] is False
        summary=client.get('/api/v1/system/logs/summary')
        assert summary.status_code==200
        assert summary.json()['scope']=='retained_rotated_logs_only'
