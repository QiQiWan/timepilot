"""Dependency-free privacy, filter and frontend integration assertions."""
import hashlib
import json
from pathlib import Path
import pytest
from timepilot.feature_contracts import FieldContract
from timepilot.feature_dashboard_v0372 import DataOverviewDashboard

ROOT=Path(__file__).resolve().parents[1]

def field(fid,name,ty='DOUBLE',classification='internal'):
    return FieldContract(field_id=fid,source_name=name,physical_type=ty,
        source_column_index=int(fid[-1]),classification=classification)

def test_filter_whitelist_parameterized_and_restricted():
    svc=DataOverviewDashboard(None)
    fs={'field0':field('field0','measurement'),
        'field1':field('field1','device','VARCHAR')}
    predicate,params,clean=svc._predicate(fs,[{'field_id':'field1','op':'eq','value':"A' OR 1=1 --"},
        {'field_id':'field0','op':'between','value':12,'upper':20}])
    assert "OR 1=1" not in predicate and predicate.count('?')==3
    assert params==["A' OR 1=1 --",12.0,20.0]
    assert clean[0]['value']=="A' OR 1=1 --"
    with pytest.raises(ValueError):svc._predicate(fs,[{'field_id':'unknown','op':'eq','value':'A'}])
    with pytest.raises(ValueError):svc._predicate(fs,[{'field_id':'field0','op':'eq','value':'NaN'}])
    with pytest.raises(ValueError):svc._predicate(fs,[{'field_id':'field0','op':'between','value':2,'upper':1}])
    with pytest.raises(ValueError):svc._predicate(fs,[{'field_id':'field0','op':'raw_sql','value':'A'}])
    with pytest.raises(ValueError):svc._predicate(fs,[{'field_id':'field1','op':'eq','value':'A'}]*6)


def test_pinned_fixture_and_runtime_gate():
    folder=ROOT/'benchmarks/featurelab_v0372'
    manifest=json.loads((folder/'MANIFEST.json').read_text())
    data=(folder/'FIXED_DASHBOARD.csv').read_bytes()
    assert hashlib.sha256(data).hexdigest()==manifest['sha256']
    assert manifest['rows']==12 and data.count(b'\n')==13
    from timepilot.feature_lab_v2_api import DashboardQuery,DashboardChartQuery
    with pytest.raises(Exception):DashboardQuery(asset_id='source',filters=[{}]*6)
    with pytest.raises(Exception):DashboardChartQuery(asset_id='source',field_id='field0',bins=100)


def test_dashboard_frontend_wired_and_version_scope():
    static=ROOT/'src/timepilot/static'
    shell=(static/'index.html').read_text()
    assert shell.index('data-dashboard-v0372.js')<shell.index('data-analysis-ui.js')
    src=(static/'data-analysis-ui.js').read_text()
    assert "window.TimePilotDataDashboard372.mount(" in src
    assert "'da_filters','da_cards'" in src
    backend=(ROOT/'src/timepilot/feature_lab_v2_api.py').read_text()
    assert "'/data-analysis/dashboard'" in backend
    assert "'/data-analysis/dashboard/chart'" in backend


def test_registered_dashboard_routes_are_scoped_and_validated(tmp_path):
    from fastapi.testclient import TestClient
    from timepilot.api import create_app
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        paths={str(x.path) for x in client.app.routes}
        assert '/api/v1/projects/{pid}/feature-lab/data-analysis/dashboard' in paths
        assert '/api/v1/projects/{pid}/feature-lab/data-analysis/dashboard/chart' in paths
        bad=client.post('/api/v1/projects/missing-project/feature-lab/data-analysis/dashboard',json={
            'asset_id':'fixture','filters':[{'field_id':'bad','op':'eq','value':'X'}]})
        assert bad.status_code in (404,409,422)
        impossible=client.post('/api/v1/projects/missing-project/feature-lab/data-analysis/dashboard/chart',json={
            'asset_id':'fixture','field_id':'f','kind':'histogram','bins':150})
        assert impossible.status_code==422
