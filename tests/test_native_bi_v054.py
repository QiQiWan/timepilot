"""v0.5.4: BI CSV semantics, realtime table, route/security, engine contract."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.native_bi_realtime import (
    RealtimeStreamHub, StreamAppend, StreamCreate, StreamEvent,
)
from timepilot.native_bi_types import infer_text_schema, suggest_column_type, valid_type


@pytest.mark.parametrize('name,values,expected', [
    ('sales', ['12','20','31'], 'BIGINT'),
    ('revenue', ['12.12','20','32.4'], 'DOUBLE'),
    ('product_id', ['1','2','3'], 'VARCHAR'),
    ('SKU', ['001','002'], 'VARCHAR'),
    ('raw', ['003','2'], 'VARCHAR'),
    ('date', ['2026-01-01','2026-01-02'], 'DATE'),
    ('description', ['two','three'], 'VARCHAR'),
    ('amount', ['1e400','23'], 'VARCHAR'),
    ('amount', ['1e-400','23'], 'VARCHAR'),
    ('raw', ['999999999999999999999999','12'], 'VARCHAR'),
])
def test_schema_inference_conservative(name, values, expected):
    assert suggest_column_type(name, values)['physical_type'] == expected


def test_inference_preserves_business_identifiers():
    schema = infer_text_schema(['product_code','quantity','timestamp'],
                               [('00123','100','2026-01-01'),('00201','250','2026-02-01')])
    assert schema['product_code']['physical_type'] == 'VARCHAR'
    assert schema['quantity']['physical_type'] == 'BIGINT'
    assert schema['timestamp']['physical_type'] == 'DATE'
    with pytest.raises(ValueError):
        valid_type('VARCHAR);DROP TABLE data;--')


class _FakeTable:
    def __init__(self):
        self.rows = []
        self.deleted = False

    def update(self, rows):
        self.rows.extend(rows)

    def delete(self):
        self.deleted = True


class _FakeClient:
    def __init__(self):
        self.table_ref = _FakeTable()
        self.call = None

    def table(self, schema, **kwargs):
        self.call = (schema, kwargs)
        return self.table_ref


class _FakeServer:
    def __init__(self):
        self.client = _FakeClient()

    def new_local_client(self):
        return self.client


def test_realtime_stream_update_bounded_and_deletable(monkeypatch):
    server = _FakeServer()
    monkeypatch.setitem(sys.modules, 'perspective', SimpleNamespace(Server=lambda: server))
    hub = RealtimeStreamHub()
    created = hub.create(StreamCreate(name='Motor A', limit=100))
    assert server.client.call[1]['limit'] == 100
    assert created['persistence'] == 'memory_only'
    item = StreamEvent(event_time=datetime(2026,11,9,8,tzinfo=timezone.utc),
                       series_id='motor-A', value=5.0)
    outcome = hub.append(created['stream_id'], StreamAppend(events=[item]))
    assert outcome['events_received'] == 1
    assert isinstance(server.client.table_ref.rows[0]['event_time'], int)
    assert hub.server_and_table(created['stream_id'])[0] is server
    assert hub.delete(created['stream_id'])['deleted'] == created['stream_id']
    assert server.client.table_ref.deleted
    with pytest.raises(KeyError):
        hub.get(created['stream_id'])


def test_realtime_rejects_bad_time_and_nonfinite_value():
    with pytest.raises(ValueError):
        StreamEvent(event_time=datetime(2026, 1, 1), series_id='m1', value=1)
    with pytest.raises(ValueError):
        StreamEvent(event_time=datetime.now(timezone.utc), series_id='m1', value=float('nan'))
    with pytest.raises(ValueError):
        StreamAppend(events=[])


def test_realtime_api_routes_and_stream_creation(tmp_path, monkeypatch):
    server = _FakeServer()
    monkeypatch.setitem(sys.modules,'perspective',SimpleNamespace(Server=lambda:server))
    api = create_app(home=tmp_path,embedded_worker=False)
    with TestClient(api) as client:
        assert client.get('/api/v1/native-bi/streams').json() == []
        created = client.post('/api/v1/native-bi/streams',json={'name':'Motor A','limit':100})
        assert created.status_code == 201, created.text
        sid = created.json()['stream_id']
        assert client.get('/bi/native/stream/'+sid).status_code == 200
        added = client.post('/api/v1/native-bi/streams/'+sid+'/append',json={'events':[
            {'event_time':'2026-11-09T08:00:00+08:00','series_id':'A','value':2.5}]})
        assert added.status_code == 200, added.text
        assert added.json()['events_received'] == 1
        assert client.delete('/api/v1/native-bi/streams/'+sid).status_code == 200
        assert client.get('/api/v1/native-bi/streams/'+sid).status_code == 404


def test_local_api_rejects_cross_site_origin(tmp_path):
    api=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(api) as client:
        response = client.post('/api/v1/native-bi/streams',headers={'Origin':'https://hostile.invalid'},
                               json={'name':'injected'})
        assert response.status_code == 403


def test_local_api_denies_remote_client(tmp_path):
    api=create_app(home=tmp_path,embedded_worker=False)
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=api,client=('198.51.100.4',7777)),base_url='http://local.invalid') as client:
            return await client.get('/api/v1/health')
    response = asyncio.run(call())
    assert response.status_code == 403


def test_type_derive_route_exists(tmp_path):
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        response = client.post('/api/v1/native-bi/assets/00000000-0000-4000-8000-000000000000/derive-types',json={
            'expected_version':'base','changes':{'amount':'DOUBLE'}})
        assert response.status_code == 404


def test_frontend_v054_source_mode_and_native_edit_still_connected():
    ui = (Path(__file__).resolve().parents[1] / 'src/timepilot/static/native-bi.js').read_text(encoding='utf8')
    front = (Path(__file__).resolve().parents[1] / 'frontend/native-bi/src/main.js').read_text(encoding='utf8')
    assert "restoreVisualSource(previous.source, latestVersion)" in ui
    assert "source.kind==='sample'" in ui and "source.kind==='query'" in ui
    assert 'selectNativeCell(cell)' in ui
    assert "'perspective-click'" in front
    assert "name !== '__tp_rowid'" in front


def test_duckdb_csv_full_inference_and_immutable_derive(tmp_path):
    pytest.importorskip('duckdb')
    from timepilot.sdk import TimePilot
    from timepilot.native_bi import NativeBIService, QuerySpec, MetricSpec
    client=TimePilot(home=tmp_path)
    native=NativeBIService(client.store,client.settings)
    payload=('product_id,date,sales,region\n001,2026-01-01,10,East\n'
             '002,2026-02-01,20,West\n').encode()
    base=native.import_file(payload,'sales.csv','销量数据')
    fields={x['name']:x for x in base['columns']}
    assert fields['product_id']['physical_type'].upper()=='VARCHAR'
    assert fields['sales']['physical_type'].upper()=='BIGINT'
    assert fields['date']['physical_type'].upper()=='DATE'
    original_hash=base['sha256']
    original=native.query(QuerySpec(asset_id=base['asset_id'],version_id='base',limit=5))
    assert original['rows'][0]['product_id']=='001'
    query=QuerySpec(asset_id=base['asset_id'],metrics=[MetricSpec(operation='sum',column='sales')])
    assert native.query(query)['rows'][0]['metric']==30.0
    derived=native.derive_types(base['asset_id'],expected_version='base',changes={'sales':'DOUBLE'})
    assert derived['asset_id']!=base['asset_id']
    assert native.verify(base['asset_id'])['actual_sha256']==original_hash
    assert native.verify(derived['asset_id'])['valid']


def test_realtime_websocket_rejects_external_origin(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    server = _FakeServer()
    monkeypatch.setitem(sys.modules, 'perspective', SimpleNamespace(Server=lambda:server))
    with TestClient(create_app(home=tmp_path, embedded_worker=False)) as client:
        created = client.post('/api/v1/native-bi/streams',
                              json={'name':'WS security','limit':100}).json()
        path = f"/api/v1/native-bi/streams/{created['stream_id']}/ws"
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path, headers={'origin':'https://evil.invalid'}) as ws:
                ws.receive_text()
        assert error.value.code == 1008
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path) as ws:
                ws.receive_text()
        assert error.value.code == 1008


def test_bulk_paste_and_revision_restore_keep_source_mode():
    ui = (Path(__file__).resolve().parents[1] / 'src/timepilot/static/native-bi.js').read_text(encoding='utf8')
    assert 'const pending=[]' in ui
    assert 'for(const item of pending)addEdit(item)' in ui
    assert "savedState={source:visualSource?structuredClone(visualSource):{kind:'virtual'}" in ui
    assert "await refreshCurrent(savedState)" in ui
