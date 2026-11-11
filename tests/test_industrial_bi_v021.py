"""Industrial BI contract, project boundaries and auditable aggregation tests."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from timepilot.api import create_app
from timepilot.industrial_bi import IndustrialBI, IndustrialBIError, IndustrialSpec, SaveIndustrialView


COLUMNS = [
    {'name':'采集时间','physical_type':'TIMESTAMP'},
    {'name':'设备编号','physical_type':'VARCHAR'},
    {'name':'温度','physical_type':'DOUBLE'},
    {'name':'维护事件','physical_type':'VARCHAR'},
    {'name':'运行工况','physical_type':'VARCHAR'},
]


class FakeConnection:
    def __init__(self):
        self.queries=[]
        self.description=[]
        self._rows=[]

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if 'ORDER BY stamp LIMIT 201' in sql:
            self._rows=[(datetime(2026,11,1,0,25),'更换滤芯','运行',25.4)]
        else:
            self.description=[(k,) for k in ('timestamp','average','minimum','maximum','source_rows',
                'valid_points','operating_mode','regime_count','maintenance_count','maintenance_marker')]
            self._rows=[(datetime(2026,11,1),25.4,25.0,26.0,60,60,'运行',2,1,25.4)]
        return self

    def fetchall(self):return self._rows
    def interrupt(self):pass


class FakeNative:
    def __init__(self):
        self.rows={'asset_id':'','columns':COLUMNS,'lineage':{'confirmed_roles':{
            'time':'采集时间','target':'温度','device':'设备编号','event':'维护事件',
            'process':'运行工况'}},'head_version':'base','sha256':'a'*64}
        self.con=FakeConnection()

    def asset(self,asset_id):
        if asset_id!=self.rows['asset_id']:raise KeyError('unknown asset')
        return self.rows

    def _version(self,asset_id,version_id):
        assert asset_id==self.rows['asset_id']
        if version_id!='base':raise KeyError('unknown version')

    @contextmanager
    def _connection(self):yield self.con

    def _install_effective_view(self,con,asset_id,version_id,cols):
        assert con is self.con
        assert cols==COLUMNS


@pytest.fixture
def setup(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as cli:
        pid=cli.post('/api/v1/projects',json={'name':'Factory'}).json()['id']
        other=cli.post('/api/v1/projects',json={'name':'Other'}).json()['id']
        native=FakeNative()
        native.rows['asset_id']=str(uuid4())
        with app.state.projects._db() as con:
            con.execute('INSERT INTO project_native_assets VALUES (?,?,?,?)',
                (pid,native.rows['asset_id'],str(uuid4()),datetime.now(timezone.utc).isoformat()))
        yield app,cli,pid,other,native


def make_spec(native,**overrides):
    base={'asset_id':native.rows['asset_id'],'template':'maintenance_overlay',
        'time_field':'采集时间','metric_field':'温度','device_field':'设备编号',
        'device_value':'E01','event_field':'维护事件','regime_field':'运行工况','interval':'1h'}
    base.update(overrides)
    return base


def test_templates_are_scoped_and_reproducible(setup):
    app,c,pid,other,native=setup
    obj=IndustrialBI(app.state.projects,native)
    suggestions=obj.suggestions(pid,native.rows['asset_id'])
    assert suggestions['suggested_fields']['time_field']=='采集时间'
    assert suggestions['suggested_fields']['event_field']=='维护事件'
    spec=IndustrialSpec(**make_spec(native))
    preview=obj.preview(pid,spec)
    assert preview['rows'][0]['valid_points']==60
    assert preview['events'][0]['event']=='更换滤芯'
    assert preview['regime_mixed_buckets']==1
    assert preview['has_row_truncation'] is False
    assert preview['source_sha256']=='a'*64
    saved=obj.save(pid,SaveIndustrialView(name='维修与温度',spec=spec))
    assert saved['source_sha256']==native.rows['sha256']
    assert obj.get(pid,saved['view_id'])['spec']['event_field']=='维护事件'
    assert len(obj.list(pid))==1
    with pytest.raises(KeyError):obj.get(other,saved['view_id'])
    with pytest.raises(KeyError):obj.preview(other,spec)
    native.rows['sha256']='b'*64
    with pytest.raises(IndustrialBIError,match='校验'):obj.get(pid,saved['view_id'])


def test_sql_params_and_field_whitelist(setup):
    app,_,pid,_,native=setup
    obj=IndustrialBI(app.state.projects,native)
    injected="E01' OR 1=1 --"
    spec=IndustrialSpec(**make_spec(native,device_value=injected))
    obj.preview(pid,spec)
    assert native.con.queries[0][1]==[injected]
    assert injected not in native.con.queries[0][0]
    with pytest.raises(IndustrialBIError,match='字段'):
        obj.preview(pid,IndustrialSpec(**make_spec(native,metric_field='温度;DROP TABLE sensors')))
    with pytest.raises(ValidationError):
        IndustrialSpec(**make_spec(native,interval='not-valid'))
    with pytest.raises(ValidationError):
        IndustrialSpec(**make_spec(native,device_value='',device_field='设备编号'))
    with pytest.raises(IndustrialBIError,match='工况'):
        obj.preview(pid,IndustrialSpec(**make_spec(native,template='regime_transition',regime_field=None)))


def test_project_scoped_api_no_source_row_truncation(setup,monkeypatch):
    app,c,pid,other,native=setup
    # Route's existing service retains a real SQL-backed contract; only the
    # engine is replaced, with no DuckDB or new files installed by tests.
    monkeypatch.setattr(app.state.native_bi,'asset',native.asset)
    monkeypatch.setattr(app.state.native_bi,'_version',native._version)
    monkeypatch.setattr(app.state.native_bi,'_connection',native._connection)
    monkeypatch.setattr(app.state.native_bi,'_install_effective_view',native._install_effective_view)
    payload=make_spec(native)
    r=c.post(f'/api/v1/projects/{pid}/industrial/preview',json=payload)
    assert r.status_code==200,r.text
    assert r.json()['events'][0]['event']=='更换滤芯'
    assert r.json()['is_full_data_aggregation']
    cross=c.post(f'/api/v1/projects/{other}/industrial/preview',json=payload)
    assert cross.status_code in (400,404)
