"""Regression of 0.38.0 selectable statistics contracts and safe invalid inputs.

These pure contract checks do not claim real DuckDB/Parquet acceptance.
"""
from __future__ import annotations

from types import SimpleNamespace
import pytest
from timepilot.feature_dashboard_v0372 import DataOverviewDashboard


def fake_dashboard():
    dash=DataOverviewDashboard(None)
    fields={f'f{i}':SimpleNamespace(field_id=f'f{i}',source_name=f'S{i}',physical_type='DOUBLE',
             role='unspecified',unit=None,classification='normal') for i in range(31)}
    dash._authorized=lambda *args,**kwargs: (
        {'sha256':'a'*64,'row_count':11},SimpleNamespace(revision=3,event_time_field_id=None),fields)
    return dash,fields


@pytest.mark.parametrize('bad',[
    [], ['f0','f0'], ['f0',{}], [['f0']], ['f0',None], [0], ['f0']*25,
])
def test_invalid_field_selections_are_validation_errors_not_internal_errors(bad):
    dashboard,_=fake_dashboard()
    with pytest.raises(ValueError):
        dashboard.overview('p','a',field_ids=bad,association_ids=[])


@pytest.mark.parametrize('bad',[
    [{}], [['f0']], ['f0','f0'], ['f0']*11,
])
def test_invalid_correlation_selections_are_rejected(bad):
    dashboard,_=fake_dashboard()
    with pytest.raises(ValueError):
        dashboard.overview('p','a',field_ids=['f0'],association_ids=bad)


@pytest.mark.parametrize('bad',[
    [{'field_id':{'not':'hashable'},'op':'eq','value':1}],
    [{'field_id':'f0','op':[],'value':1}],
    [{'field_id':'f0','op':'eq','value':float('nan')}],
    [{'field_id':'f0','op':'between','value':5,'upper':1}],
    [{'field_id':'f0','op':'sql','value':1}],
])
def test_invalid_filters_fail_closed_without_reaching_duckdb(bad):
    dashboard,fields=fake_dashboard()
    with pytest.raises(ValueError):dashboard._predicate(fields,bad)


@pytest.mark.parametrize('kind,field,other',[
    (['histogram'],'f0',None),('histogram',{},None),('scatter','f0',{}),
])
def test_invalid_chart_arguments_fail_before_query(kind,field,other):
    dashboard,_=fake_dashboard()
    with pytest.raises(ValueError):
        dashboard.chart('p','a',kind=kind,field_id=field,other_field_id=other)


def test_http_schema_rejects_bad_field_lists_as_422(tmp_path):
    from fastapi.testclient import TestClient
    from timepilot.api import create_app
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        path='/api/v1/projects/not-a-real-project/feature-lab/data-analysis/dashboard'
        r=client.post(path,json={'asset_id':'asset','field_ids':[{'nested':'f0'}]})
        assert r.status_code==422
        r=client.post(path,json={'asset_id':'asset','field_ids':['f0']*25})
        assert r.status_code==422
