"""v0.5.3: build integrity, source immutability and workspace revision guard."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from timepilot.api import create_app
from timepilot.db import NativeBIAssetRow
from timepilot.native_bi import NativeBIError, ViewSpec
from timepilot.native_bi_bundle import bundle_status, generate_manifest, PUBLIC_EXPORTS

ROOT = Path(__file__).resolve().parents[1]


def demo_asset(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    native = app.state.native_bi
    asset_id = str(uuid4())
    file = native._path(asset_id)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b'simulated-metadata-snapshot')
    with app.state.client.store.sessions.begin() as session:
        session.add(NativeBIAssetRow(id=asset_id, name='demo', row_count=2,
            columns_json=json.dumps([{'name': 'region', 'physical_type': 'VARCHAR'},
                                     {'name': 'sales', 'physical_type': 'DOUBLE'}]),
            lineage_json='{}', sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            file_bytes=file.stat().st_size))
    return app, native, asset_id


def test_bundle_release_requires_verified_css_js_and_two_wasm(tmp_path):
    assert not bundle_status(tmp_path)['ready']
    (tmp_path / 'perspective-entry.js').write_text('\n'.join(f'export function {x}(){{}}' for x in PUBLIC_EXPORTS))
    (tmp_path / 'perspective-contract.json').write_text(json.dumps({
        'format': 1, 'entry': 'perspective-entry.js', 'exports': list(PUBLIC_EXPORTS)}))
    (tmp_path / 'perspective-entry.css').write_text('perspective-viewer{display:block}')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'perspective-server.wasm').write_bytes(b'\x00asm-server-fixture')
    (tmp_path / 'assets' / 'perspective-viewer.wasm').write_bytes(b'\x00asm-viewer-fixture')
    result = generate_manifest(tmp_path)
    assert len(result['files']) == 5
    assert bundle_status(tmp_path)['ready']
    (tmp_path / 'assets' / 'untracked-helper.js').write_text('export {};')
    assert not bundle_status(tmp_path)['ready']
    (tmp_path / 'assets' / 'untracked-helper.js').unlink()
    assert bundle_status(tmp_path)['ready']
    (tmp_path / 'assets' / 'perspective-server.wasm').write_bytes(b'changed')
    status = bundle_status(tmp_path)
    assert not status['ready'] and any('长度不一致' in x for x in status['problems'])


def test_native_bundle_checks_fail_closed_without_build():
    folder = ROOT / 'src/timepilot/static/native-bi'
    assert not bundle_status(folder)['ready'] or (folder / 'perspective-build.json').exists()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_native_bi_release.py'), '--require-build'],
                            text=True, capture_output=True)
    if not bundle_status(folder)['ready']:
        assert result.returncode != 0 and 'FAILED' in result.stderr


def test_server_rejects_wrong_origin_before_optional_import(tmp_path):
    app, _, asset_id = demo_asset(tmp_path)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f'/api/v1/native-bi/assets/{asset_id}/versions/base/ws',
                                          headers={'origin': 'https://evil.example'}):
                pass
        assert exc.value.code == 1008


def test_versioned_arrow_response_marks_exact_snapshot(tmp_path, monkeypatch):
    app, native, asset_id = demo_asset(tmp_path)
    monkeypatch.setattr(native, 'preview_arrow', lambda asset_id, limit, version_id: b'ARROW')
    monkeypatch.setattr(native, 'query_arrow', lambda spec: b'ARROW')
    with TestClient(app) as client:
        res = client.get(f'/api/v1/native-bi/assets/{asset_id}/sample.arrow?version_id=base')
        assert res.status_code == 200 and res.headers['x-timepilot-version'] == 'base'
        assert res.headers['x-timepilot-asset'] == asset_id
        other = client.post('/api/v1/native-bi/query.arrow', json={
            'asset_id': asset_id, 'version_id': 'base', 'group_by': ['region'],
            'metrics': [{'operation': 'sum', 'column': 'sales', 'alias': 'sum_sales'}]})
        assert other.status_code == 200 and other.headers['x-timepilot-version'] == 'base'
        assert other.headers['x-timepilot-source'] == 'duckdb_query'


def test_saved_virtual_workspace_binds_data_version_and_keeps_derived_names(tmp_path):
    _, native, asset_id = demo_asset(tmp_path)
    workspace = {'panels': {'detail': {'table': 'timepilot_active', 'plugin': 'Datagrid'},
                 'chart': {'table': 'rollup', 'plugin': 'Y Bar'}},
                 'derived_tables': {'rollup': {'table': 'timepilot_active', 'group_by': ['region']}},
                 'layout': {'type': 'tab-layout', 'tabs': ['detail', 'chart']}}
    config = {'format': 'timepilot-perspective-v2', 'workspace': workspace,
              'base_table_name': 'timepilot_active',
              'source': {'kind': 'virtual', 'asset_id': asset_id, 'version_id': 'base'}}
    saved = native.save_view(ViewSpec(asset_id=asset_id, name='全量透视', config=config))
    assert native.get_view(saved['view_id'])['config'] == config
    corrupted = json.loads(json.dumps(config))
    corrupted['source']['asset_id'] = str(uuid4())
    with pytest.raises(NativeBIError, match='资产'):
        native.save_view(ViewSpec(asset_id=asset_id, name='错误来源', config=corrupted))
    corrupted = json.loads(json.dumps(config))
    corrupted['workspace']['derived_tables']['rollup']['table'] = 'outside'
    with pytest.raises(NativeBIError, match='未授权'):
        native.save_view(ViewSpec(asset_id=asset_id, name='错误关系', config=corrupted))


def test_real_native_perspective_duckdb_view_when_installed(tmp_path):
    pytest.importorskip('duckdb')
    pytest.importorskip('pyarrow')
    pytest.importorskip('perspective')
    import perspective
    from timepilot.native_bi_api import create_timepilot_virtual_server, prepare_perspective_source
    app = create_app(home=tmp_path, embedded_worker=False)
    native = app.state.native_bi
    data = tmp_path / 'sales.csv'
    data.write_text('region,sales\nA,10\nB,20\n', encoding='utf-8')
    info = native.import_from_path(data, 'sales')
    asset_id = info['asset_id']
    with native._connection() as con:
        sql = native._effective_sql(asset_id, 'base', info['columns'])
        exposed = prepare_perspective_source(con, sql)
        assert exposed.endswith('.timepilot_active')
        assert con.execute('SELECT count(*) FROM timepilot_active').fetchone()[0] == 2
        virtual = create_timepilot_virtual_server(con)
        # The previous test only checked whether `new_session` existed. It did
        # not call the protocol, and missed the browser's Unknown table error.
        session = virtual.new_session(lambda response: client.handle_response(response))
        client = perspective.Client(session.handle_request)
        try:
            hosted = client.get_hosted_table_names()
            assert exposed in hosted
            table = client.open_table(exposed)
            schema = table.schema()
            assert schema['sales'] in {'float', 'integer'}
            assert schema['region'] == 'string'
            assert '__tp_rowid' not in schema
            view = table.view(columns=['region', 'sales'])
            try:
                records = view.to_json()
                assert len(records) == 2
                assert sum(row['sales'] for row in records) == 30
                assert [row['region'] for row in records] == ['A', 'B']
            finally:
                view.delete()
        finally:
            session.close()


def test_saved_workspace_rejects_cycle(tmp_path):
    _, native, asset_id = demo_asset(tmp_path)
    bad = {'format': 'timepilot-perspective-v2',
           'source': {'kind': 'virtual', 'asset_id': asset_id, 'version_id': 'base'},
           'workspace': {'panels': {'chart': {'table': 'a', 'plugin': 'Y Bar'}},
                         'derived_tables': {'a': {'table': 'b'}, 'b': {'table': 'a'}}}}
    with pytest.raises(NativeBIError, match='循环'):
        native.save_view(ViewSpec(asset_id=asset_id, name='循环', config=bad))
