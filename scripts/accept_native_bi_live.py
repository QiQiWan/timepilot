"""Real native BI acceptance using the user's installed Google Chrome.

Exercises genuine FastAPI -> DuckDB/Parquet -> Perspective WebSocket -> Chrome.
No browser downloads, no screenshots, no mock data services, no new Python env.

On failure, writes reports/native_bi_acceptance.json and prints the failing
stage, browser console errors, failed requests and recent WebSocket events.
An outer process watchdog terminates unresponsive browser/WS operations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / 'reports' / 'native_bi_acceptance.json'


class AcceptanceFailure(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise AcceptanceFailure(message)


class Progress:
    """Persistent incremental report: a hung process retains its last stage."""

    def __init__(self, path: Path):
        self.path = path
        self.started = time.monotonic()
        self.data = {'status': 'running', 'stage': 'setup', 'events': [], 'browser': {},
                     'errors': [], 'http_errors': [], 'failed_requests': [], 'websockets': []}
        self.persist()

    def persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(self.path)

    def note(self, message):
        print(message, flush=True)

    @contextmanager
    def step(self, name):
        begin = time.monotonic()
        self.data['stage'] = name
        self.persist()
        self.note(f'[START] {name}')
        try:
            yield
        except BaseException as exc:
            self.data['status'] = 'failed'
            self.data['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
            self.data['events'].append({'stage': name, 'status': 'failed',
                                        'seconds': round(time.monotonic() - begin, 2)})
            self.persist()
            self.note(f'[FAIL] {name} ({time.monotonic() - begin:.1f}s): {type(exc).__name__}: {exc}')
            raise
        else:
            self.data['events'].append({'stage': name, 'status': 'passed',
                                        'seconds': round(time.monotonic() - begin, 2)})
            self.persist()
            self.note(f'[PASS] {name} ({time.monotonic() - begin:.1f}s)')

    def complete(self):
        self.data['status'] = 'passed'
        self.data['stage'] = 'complete'
        self.data['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
        self.persist()
        self.note(f"[PASS] Real native BI acceptance completed in {self.data['elapsed_seconds']:.1f}s")


# Playwright wait_for_function must use a *synchronous* predicate. Returning
# a Promise from an async predicate is considered truthy by some Playwright
# versions, even if its eventual result is false. This was the cause of
# `AttributeError: 'bool' object has no attribute 'get'` on the user's Mac.
# The application owns a deterministic loading / ready / error state; polling
# saveWorkspace() inside wait_for_function also creates overlapping async work.
WORKSPACE_CHECK = r"""() => {
  const root = document.querySelector('#tp-native-perspective');
  if (!root) {
    const error = document.querySelector('#app .helper.danger, #app .helper.warning');
    return error ? {kind: 'error', message: (error.textContent || '').trim().slice(0, 600)} : null;
  }
  const state = root.dataset.perspectiveState;
  if (state === 'error') {
    return {kind: 'error', message: root.dataset.perspectiveError || root.textContent.trim().slice(0, 600)};
  }
  const warning = root.querySelector('.helper.danger, .helper.warning');
  if (warning) return {kind: 'error', message: (warning.textContent || '').trim().slice(0, 600)};
  if (state === 'ready') return {kind: 'ready'};
  return null;
}"""


# Runtime proof: the browser imports the actual compiled ES module and checks
# the exported names. A valid WASM hash alone does not make the module usable.
BROWSER_EXPORT_CHECK = r"""async () => {
  const resp = await fetch('/assets/native-bi/perspective-build.json', {cache: 'no-store'});
  if (!resp.ok) throw new Error(`Perspective build manifest HTTP ${resp.status}`);
  const manifest = await resp.json();
  const hash = manifest.files?.['perspective-entry.js']?.sha256;
  if (!/^[0-9a-f]{64}$/.test(String(hash || ''))) {
    throw new Error('Perspective entry JS hash is missing');
  }
  const uri = '/assets/native-bi/perspective-entry.js?v=' + hash.slice(0, 16);
  const module = await import(uri);
  const names = ['mountPerspective', 'mountPerspectiveRemote', 'getActiveTableName',
    'setPerspectivePreset', 'savePerspectiveState', 'restorePerspectiveState',
    'disposePerspective'];
  return {uri, required: names, exported: Object.keys(module),
          missing: names.filter(name => typeof module[name] !== 'function')};
}"""


def browser_workspace(page):
    """Serialize once after the UI reports ready, with a finite JS deadline."""
    return page.locator('#tp-native-perspective perspective-viewer').evaluate(
        """async el => await Promise.race([
          el.saveWorkspace(),
          new Promise((_, reject) => setTimeout(() => reject(new Error('saveWorkspace timed out after 4 seconds')), 4000))
        ])""", timeout=7000)


def wait_workspace(page, plugin='Datagrid', grouped=False, timeout=25000):
    """Wait for an actual application state, then an actual panel configuration.

    The root loading marker is set before BI initialization starts and changes
    only after Client/WebSocket/Viewer restore succeeds. The historical async
    predicate could return the boolean `false` *as a successful JSHandle*.
    """
    deadline = time.monotonic() + timeout / 1000
    try:
        state = page.wait_for_function(WORKSPACE_CHECK, timeout=timeout, polling=250).json_value()
    except Exception as exc:
        raise AcceptanceFailure(f'原生 BI 页面未在 {timeout/1000:g}s 内完成加载或报告失败：{exc}') from exc
    if not isinstance(state, dict):
        raise AcceptanceFailure('原生 BI 页面状态返回异常：' + repr(state) +
                                '。请检查 UI 异步加载和浏览器控制台。')
    if state.get('kind') == 'error':
        raise AcceptanceFailure('Perspective 页面初始化失败：' + state.get('message', '未知错误'))
    if state.get('kind') != 'ready':
        raise AcceptanceFailure('Perspective 页面返回未知状态：' + repr(state))

    last_panels, last_error = {}, None
    while time.monotonic() < deadline:
        try:
            workspace = browser_workspace(page)
            if not isinstance(workspace, dict):
                last_error = f'工作区返回异常类型：{type(workspace).__name__}'
            else:
                panels = workspace.get('panels') or {}
                if not isinstance(panels, dict):
                    last_error = '工作区 panels 数据结构不合法'
                else:
                    last_panels = panels
                    if panels and any(
                        (plugin is None or config.get('plugin') == plugin)
                        and (not grouped or bool(config.get('group_by')))
                        for config in panels.values() if isinstance(config, dict)
                    ):
                        return workspace
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
        remaining = deadline - time.monotonic()
        if remaining > 0:
            page.wait_for_timeout(min(300, int(remaining * 1000)))
    raise AcceptanceFailure(
        'Perspective 工作区未就绪：期望插件=' + repr(plugin) +
        f'，需要分组={grouped}；已发现面板=' + repr(last_panels)[:550] +
        ('；最近错误=' + str(last_error)[:350] if last_error else ''))


def browser_debug(page, progress):
    try:
        body = page.locator('#tp-native-perspective').inner_text(timeout=1000)
        progress.note('[DIAG] Native BI panel: ' + body[:500].replace('\n', ' | '))
    except Exception:
        try:
            body = page.locator('#app').inner_text(timeout=1000)
            progress.note('[DIAG] Page content: ' + body[-900:].replace('\n', ' | '))
        except Exception:
            pass
    try:
        state = page.evaluate("""() => ({
            url: location.href,
            loaded: !!window.TimePilotNativeBI,
            nativeState: document.getElementById('tp-native-perspective')?.dataset?.perspectiveState || '',
            nativeError: document.getElementById('tp-native-perspective')?.dataset?.perspectiveError || '',
            pageTail: (document.getElementById('app')?.innerText || '').slice(-650)
        })""")
        progress.data['page_state'] = state
        progress.note('[DIAG] page_state: ' + json.dumps(state, ensure_ascii=False))
    except Exception:
        pass
    for field in ('errors', 'http_errors', 'failed_requests', 'websockets'):
        items = progress.data.get(field, [])
        if items:
            progress.note('[DIAG] ' + field + ': ' + json.dumps(items[-5:], ensure_ascii=False))
    progress.persist()


def bind_observers(page, progress):
    def console(msg):
        if msg.type in ('warning', 'error') and len(progress.data['errors']) < 30:
            progress.data['errors'].append(f'console/{msg.type}: {msg.text[:350]}')
    def page_error(exc):
        if len(progress.data['errors']) < 30:
            progress.data['errors'].append(f'pageerror: {str(exc)[:400]}')
    def request_failed(req):
        if len(progress.data['failed_requests']) < 30:
            progress.data['failed_requests'].append(f'{req.method} {req.url}'[:350])
    def http_response(response):
        # HTTP 404 is not a requestfailed event. Capture exact URLs to separate
        # favicon probes from missing Perspective JS/CSS/WASM or broken APIs.
        if response.status >= 400 and len(progress.data['http_errors']) < 30:
            progress.data['http_errors'].append(
                f'{response.status} {response.request.method} {response.url}'[:400])
    def socket_event(ws):
        if len(progress.data['websockets']) < 40:
            progress.data['websockets'].append('opened: ' + ws.url[:250])
        ws.on('close', lambda _=None: progress.data['websockets'].append('closed: ' + ws.url[:250]))
        ws.on('socketerror', lambda err: progress.data['websockets'].append('error: ' + str(err)[:250]))
    page.on('console', console)
    page.on('pageerror', page_error)
    page.on('requestfailed', request_failed)
    page.on('response', http_response)
    page.on('websocket', socket_event)


def run_acceptance(progress: Progress):
    sys.path.insert(0, str(ROOT / 'src'))
    sys.path.insert(0, str(ROOT / 'scripts'))
    from verify_installation import inspect
    with progress.step('release-source-and-environment-preflight'):
        report = inspect(require_runtime=True, integration=True, require_bundle=True)
        require(report['ok'], 'Source/dependency/browser-bundle preflight failed: ' +
                '; '.join(report['issues'][:12]))
    from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
    with progress.step('validate-bundle-and-dependencies'):
        status = bundle_status(ROOT / 'src/timepilot/static/native-bi')
        require(status['ready'], 'Perspective JS/CSS/WASM validation failed: ' + '; '.join(status['problems']))
        require(frontend_matches_bundle(ROOT / 'src/timepilot/static/native-bi', ROOT / 'frontend/native-bi'),
                'Perspective browser files are from an older frontend source. '
                'Run: python scripts/build_native_bi.py')
        import httpx
        import pyarrow as pa
        import pyarrow.parquet as pq
        import uvicorn
        import duckdb  # noqa: F401
        import perspective  # noqa: F401
        from playwright.sync_api import sync_playwright
        from timepilot.api import create_app
        from system_chrome import launch_installed_chrome

    with tempfile.TemporaryDirectory(prefix='timepilot_bi_accept_') as folder:
        home = Path(folder)
        data = home / 'sales.parquet'
        pq.write_table(pa.table({'region': ['East', 'East', 'West', 'West'],
                                 'period': ['2026-01', '2026-02', '2026-01', '2026-02'],
                                 'sales': pa.array([10, 20, 30, 40], type=pa.float64())}), data)
        app = create_app(home=home / 'runtime', embedded_worker=False)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
        thread = threading.Thread(target=server.run, daemon=True, name='timepilot-acceptance-server')
        thread.start()
        try:
            with httpx.Client(base_url=url, timeout=10.0) as client:
                with progress.step('http-server-health'):
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        try:
                            if client.get('/api/v1/health').status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.15)
                    else:
                        raise AcceptanceFailure('Local FastAPI server did not become healthy within 12 seconds')
                with progress.step('native-bi-virtual-server-capabilities'):
                    capabilities_response = client.get('/api/v1/native-bi/capabilities')
                    require(capabilities_response.status_code == 200,
                            'Native BI capability endpoint failed: ' + capabilities_response.text[:450])
                    capabilities = capabilities_response.json()
                    progress.data['native_bi_capabilities'] = capabilities
                    progress.persist()
                    require(capabilities.get('perspective_virtual_server_ready') is True,
                            '原生 BI Virtual Server 未就绪：' +
                            json.dumps(capabilities, ensure_ascii=False)[:1000])
                with progress.step('parquet-import-and-duckdb-query'):
                    with data.open('rb') as handle:
                        created = client.post('/api/v1/native-bi/assets/upload', data={'name': 'Acceptance sales'},
                                              files={'file': ('sales.parquet', handle, 'application/octet-stream')})
                    require(created.status_code == 201, 'Parquet upload failed: ' + created.text[:400])
                    asset = created.json()['asset_id']
                    base_sha = created.json()['sha256']
                    progress.data['asset_id'] = asset
                    def total(version):
                        response = client.post('/api/v1/native-bi/query', json={
                            'asset_id': asset, 'version_id': version,
                            'metrics': [{'operation': 'sum', 'column': 'sales', 'alias': 'sum_sales'}], 'limit': 10})
                        require(response.status_code == 200, 'DuckDB aggregate failed: ' + response.text[:400])
                        return response.json()['rows'][0]['sum_sales']
                    require(total('base') == 100, 'Incorrect base aggregate: expected 100')
                with progress.step('real-industrial-data-registration'):
                    from datetime import datetime, timedelta, timezone
                    from uuid import uuid4
                    records = 180
                    measure_path = home / 'equipment_measurements.parquet'
                    timestamps = [datetime(2026,11,9,8,0)+timedelta(minutes=i) for i in range(records)]
                    pq.write_table(pa.table({
                        'observed_at': pa.array(timestamps,type=pa.timestamp('ms')),
                        'device_id': pa.array(['E01'] * records),
                        'temperature_c': pa.array([35.0 + (i%25)*0.1 for i in range(records)],type=pa.float64()),
                        'pressure_kpa': pa.array([101 + (i%15)*0.3 for i in range(records)],type=pa.float64()),
                        'maintenance_event': pa.array(['filter_replaced' if i==67 else '' for i in range(records)]),
                        'regime': pa.array(['low' if i<90 else 'high' for i in range(records)]),
                    }),measure_path)
                    with measure_path.open('rb') as handle:
                        response=client.post('/api/v1/native-bi/assets/upload',data={'name':'Industrial acceptance'},
                            files={'file':('equipment_measurements.parquet',handle,'application/octet-stream')})
                    require(response.status_code==201,'Industrial Parquet import failed: '+response.text[:400])
                    industrial_asset=response.json()['asset_id']
                    response=client.post('/api/v1/projects',json={'name':'工业 BI 验收项目'})
                    require(response.status_code==201,'Industrial project creation failed: '+response.text[:400])
                    industrial_pid=response.json()['id']
                    # Acceptance-only seed: link an existing immutable native asset
                    # to the throwaway test project; no production catalog is altered.
                    with app.state.projects._db() as con:
                        con.execute('INSERT INTO project_native_assets VALUES(?,?,?,?)',
                            (industrial_pid,industrial_asset,str(uuid4()),datetime.now(timezone.utc).isoformat()))
                    industrial_spec={'asset_id':industrial_asset,'version_id':'base',
                        'template':'maintenance_overlay','time_field':'observed_at',
                        'metric_field':'temperature_c','device_field':'device_id',
                        'device_value':'E01','event_field':'maintenance_event',
                        'regime_field':'regime','interval':'1h','max_points':1000}
                    preview=client.post(f'/api/v1/projects/{industrial_pid}/industrial/preview',json=industrial_spec)
                    require(preview.status_code==200,'DuckDB industrial preview failed: '+preview.text[:600])
                    found=preview.json()
                    require(len(found.get('rows',[]))==3,'Industrial hourly bucket count should equal 3')
                    require(sum(int(b['valid_points']) for b in found['rows'])==records,
                            'Industrial analysis dropped or duplicated input records')
                    require(len(found.get('events',[]))==1,
                            'Maintenance event not retained at source timestamp')
                    require(found['regime_mixed_buckets']>=1,
                            'Regime switch not reported in mixed hourly bucket')
                    saved=client.post(f'/api/v1/projects/{industrial_pid}/industrial/views',
                        json={'name':'设备温度与维护','spec':industrial_spec})
                    require(saved.status_code==201,'Industrial saved template failed: '+saved.text[:500])
                    industrial_view_id=saved.json()['view_id']
                    progress.data['industrial_test']={'project_id':industrial_pid,'asset_id':industrial_asset,
                        'view_id':industrial_view_id,'events':found['events'],'buckets':len(found['rows'])}
                    progress.persist()
                with progress.step('real-industrial-arrow-response'):
                    arrow=client.post(f'/api/v1/projects/{industrial_pid}/industrial/arrow',json=industrial_spec)
                    require(arrow.status_code==200,'Industrial Arrow endpoint failed: '+arrow.text[:400])
                    require(arrow.headers.get('content-type','').startswith('application/vnd.apache.arrow.stream'),
                            'Industrial response is not Arrow IPC')
                    with pa.ipc.open_stream(pa.py_buffer(arrow.content)) as reader:
                        table=reader.read_all()
                    require(table.num_rows==3,'Industrial Arrow row count differs from DuckDB JSON preview')
                    require('maintenance_marker' in table.column_names,'Maintenance overlay marker absent')
                # Real Python protocol preflight identifies catalog/name mismatches
                # *before* opening Chrome. CREATE VIEW alone is not a protocol test.
                with progress.step('perspective-python-table-registry'):
                    import perspective
                    from timepilot.native_bi_api import (
                        create_timepilot_virtual_server, prepare_perspective_source,
                    )
                    native = app.state.native_bi
                    with native._connection() as con:
                        meta = native.asset(asset)
                        sql = native._effective_sql(asset, 'base', meta['columns'])
                        actual = prepare_perspective_source(con, sql)
                        virtual = create_timepilot_virtual_server(con)
                        session = virtual.new_session(lambda response: protocol.handle_response(response))
                        protocol = perspective.Client(session.handle_request)
                        try:
                            names = protocol.get_hosted_table_names()
                            require(actual in names, f'Perspective registry does not expose {actual}: {names}')
                            table = protocol.open_table(actual)
                            require(table.get_name() == actual, 'Perspective returned a different table identity')
                            snapshot = table.view(columns=['sales'])
                            try:
                                rows = snapshot.to_json()
                                require(sum(row['sales'] for row in rows) == 100,
                                        'Python Perspective virtual view returned incorrect sales')
                            finally:
                                snapshot.delete()
                            progress.data['python_virtual_table'] = actual
                            progress.persist()
                        finally:
                            session.close()

                with sync_playwright() as p:
                    with progress.step('installed-chrome-startup'):
                        browser = launch_installed_chrome(p)
                        progress.data['browser'] = {'product': 'Google Chrome', 'version': browser.version}
                        progress.note('Using installed Chrome: ' + browser.version)
                    try:
                        with progress.step('new-browser-page'):
                            page = browser.new_page(viewport={'width': 1440, 'height': 960})
                            page.set_default_timeout(12000)
                            page.set_default_navigation_timeout(15000)
                            bind_observers(page, progress)
                        def navigate(path):
                            response = page.goto(url + path, wait_until='domcontentloaded', timeout=15000)
                            require(response is not None and response.status == 200,
                                    f'Navigation failed: {path} ({response.status if response else "no response"})')
                        def workspace(plugin='Datagrid', grouped=False):
                            try:
                                return wait_workspace(page, plugin, grouped)
                            except Exception:
                                browser_debug(page, progress)
                                raise

                        with progress.step('load-frontend-shell'):
                            navigate('/bi/native')
                        with progress.step('compiled-es-module-contract'):
                            try:
                                contract = page.evaluate(BROWSER_EXPORT_CHECK)
                            except Exception:
                                browser_debug(page, progress)
                                raise
                            progress.data['browser_module_exports'] = contract
                            progress.persist()
                            require(not contract['missing'],
                                    'Perspective compiled ESM is missing callable exports: ' +
                                    ', '.join(contract['missing']) +
                                    '; actually available: ' + ', '.join(contract['exported'])[:400] +
                                    '. Rebuild the frontend with preserveEntrySignatures=strict.')
                        with progress.step('open-native-bi-page'):
                            navigate(f'/bi/native/{asset}')
                        with progress.step('perspective-websocket-and-datagrid'):
                            result = workspace()
                            require(bool(result['panels']), 'Datagrid has no Perspective panels')
                            hint = page.locator('#tp-native-viz-hint').inner_text(timeout=3000)
                            require('完整数据视图' in hint, 'Expected full DuckDB view; UI says: ' + hint[:300])
                            active_name = page.evaluate('async uri => (await import(uri)).getActiveTableName()',
                                                        contract['uri'])
                            require(active_name == progress.data['python_virtual_table'],
                                    'Chrome opened the wrong Perspective table: ' + str(active_name))
                            progress.data['browser_virtual_table'] = active_name
                            progress.persist()
                        with progress.step('native-pivot'):
                            page.locator('[data-native="preset"][data-preset="pivot"]').click()
                            result = workspace('Datagrid', grouped=True)
                            require(any(v.get('group_by') for v in result['panels'].values()), 'Pivot lacks group_by')
                        with progress.step('native-chart'):
                            page.locator('[data-native="preset"][data-preset="bar"]').click()
                            result = workspace('Y Bar')
                            require(any(v.get('plugin') == 'Y Bar' for v in result['panels'].values()), 'Chart not active')
                        with progress.step('industrial-project-native-bi-browser'):
                            navigate(f'/projects/{industrial_pid}/bi/native/{industrial_asset}')
                            workspace()
                            page.locator('#tp-industrial-form').wait_for(timeout=12000)
                            page.locator('#tp-industrial-form select[name="template"]').select_option('maintenance_overlay')
                            page.locator('#tp-industrial-form select[name="time_field"]').select_option('observed_at')
                            page.locator('#tp-industrial-form select[name="metric_field"]').select_option('temperature_c')
                            page.locator('#tp-industrial-form select[name="device_field"]').select_option('device_id')
                            page.locator('#tp-industrial-form input[name="device_value"]').fill('E01')
                            page.locator('#tp-industrial-form select[name="event_field"]').select_option('maintenance_event')
                            page.locator('#tp-industrial-form select[name="regime_field"]').select_option('regime')
                            page.locator('[data-native="industrial-analyze"]').click()
                            page.locator('#tp-industrial-result .tp-industrial-events td').first.wait_for(timeout=15000)
                            require('filter_replaced' in page.locator('#tp-industrial-result').inner_text(),
                                    'Maintenance annotation not rendered in the browser')
                            workspace('Y Line')
                            require('混合工况桶' in page.locator('#tp-industrial-result').inner_text(),
                                    'Industrial dashboard does not show mixed-regime alert')
                        with progress.step('industrial-save-and-reopen-template-browser'):
                            page.once('dialog',lambda d:d.accept('验收工业模板'))
                            page.locator('[data-native="industrial-save"]').click()
                            page.locator('[data-native="industrial-restore"]').first.wait_for(timeout=12000)
                            page.reload(wait_until='domcontentloaded',timeout=18000)
                            workspace()
                            page.locator('[data-native="industrial-restore"]').first.click()
                            page.locator('#tp-industrial-result .tp-industrial-events td').first.wait_for(timeout=15000)
                            require('filter_replaced' in page.locator('#tp-industrial-result').inner_text(),
                                    'Saved industrial view did not restore maintenance evidence')
                        with progress.step('return-to-legacy-native-bi'):
                            navigate(f'/bi/native/{asset}')
                            workspace()
                        with progress.step('save-workspace'):
                            page.once('dialog', lambda dlg: dlg.accept('Acceptance view'))
                            page.locator('[data-native="save-view"]').click()
                            views = []
                            for _ in range(40):
                                views_response = client.get(f'/api/v1/native-bi/assets/{asset}/views')
                                require(views_response.status_code == 200, views_response.text[:300])
                                views = views_response.json()
                                if views:
                                    break
                                time.sleep(.1)
                            require(bool(views), 'Saved Perspective workspace missing after 4 seconds')
                            view_id = views[0]['view_id']
                        with progress.step('refresh-restore-workspace'):
                            navigate(f'/bi/native/{asset}/view/{view_id}')
                            restored = workspace('Y Bar')
                            require(any(v.get('plugin') == 'Y Bar' for v in restored['panels'].values()),
                                    'Refresh failed to restore the chart')
                        with progress.step('websocket-reconnect-after-browser-refresh'):
                            opened_before=sum(x.startswith('opened:') for x in progress.data['websockets'])
                            page.reload(wait_until='domcontentloaded', timeout=20000)
                            restored=workspace('Y Bar')
                            opened_after=sum(x.startswith('opened:') for x in progress.data['websockets'])
                            require(opened_after > opened_before,
                                    'Browser reload did not open a fresh Perspective WebSocket')
                            require(any(v.get('plugin')=='Y Bar' for v in restored['panels'].values()),
                                    'Workspace was not restored after the WebSocket reconnect')
                        with progress.step('open-current-version-for-editing'):
                            navigate(f'/bi/native/{asset}')
                            workspace()
                            page.locator('[data-native="preset"][data-preset="bar"]').click()
                            page.locator('td[data-native="cell"][data-column="sales"]').first.wait_for(timeout=12000)
                        with progress.step('edit-preview-and-commit'):
                            page.locator('td[data-native="cell"][data-column="sales"]').first.click()
                            page.locator('#tp-native-cell-value').fill('100')
                            page.locator('[data-native="queue-cell"]').click()
                            page.locator('[data-native="preview-edit"]').click()
                            page.locator('#tp-native-commit-edit:not([disabled])').wait_for(timeout=12000)
                            page.once('dialog', lambda dlg: dlg.accept())
                            page.locator('#tp-native-commit-edit').click()
                            current = 'base'
                            for _ in range(40):
                                current = client.get(f'/api/v1/native-bi/assets/{asset}').json()['head_version']
                                if current != 'base':
                                    break
                                time.sleep(.15)
                            require(current != 'base', 'UI did not commit a new version')
                            require(total(current) == 190, 'Updated version expected aggregate 190')
                            require(total('base') == 100, 'Original data aggregate changed')
                            require(client.get(f'/api/v1/native-bi/assets/{asset}').json()['sha256'] == base_sha,
                                    'Original Parquet hash changed')
                        with progress.step('chart-preserved-after-edit'):
                            result = workspace('Y Bar')
                            require(current[:8] in page.locator('#tp-native-viz-hint').inner_text(),
                                    'Viewer still points to stale revision')
                        with progress.step('saved-workspace-original-revision'):
                            navigate(f'/bi/native/{asset}/view/{view_id}')
                            workspace('Y Bar')
                            require('原始快照' in page.locator('#tp-native-viz-hint').inner_text(),
                                    'Saved view moved away from original version')
                        with progress.step('rollback-to-original-and-preserve-history'):
                            navigate(f'/bi/native/{asset}')
                            workspace()
                            page.once('dialog', lambda dlg: dlg.accept())
                            page.locator('[data-native="rollback"][data-version="base"]').click()
                            head=current
                            for _ in range(45):
                                head=client.get(f'/api/v1/native-bi/assets/{asset}').json()['head_version']
                                if head=='base':
                                    break
                                time.sleep(.15)
                            require(head=='base', 'Rollback did not move version pointer to original')
                            require(total('base')==100, 'Rollback modified original immutable snapshot')
                            require(total(current)==190, 'Rollback deleted previous immutable edit version')
                            workspace()
                        with progress.step('create-realtime-stream'):
                            stream = client.post('/api/v1/native-bi/streams',
                                                 json={'name': 'Motor acceptance', 'limit': 100})
                            require(stream.status_code == 201, stream.text[:400])
                            stream_id = stream.json()['stream_id']
                        try:
                            with progress.step('load-realtime-perspective-datagrid'):
                                navigate(f'/bi/native/stream/{stream_id}')
                                workspace()
                            def append(value, timestamp):
                                response = client.post(f'/api/v1/native-bi/streams/{stream_id}/append',
                                                       json={'events': [{'event_time': timestamp, 'series_id': 'motor-A',
                                                                         'value': value, 'status': 'ok'}]})
                                require(response.status_code == 200, 'Realtime append failed: ' + response.text[:400])
                            with progress.step('realtime-incremental-updates'):
                                append(2., '2026-11-09T08:00:00+08:00')
                                def wait_for_value(v):
                                    page.wait_for_function("""async v => {
                                      const el = document.querySelector('#tp-native-perspective perspective-viewer');
                                      if (!el || typeof el.getView !== 'function') return false;
                                      const rows = await Promise.race([
                                        el.getView().then(view => view.to_json()).catch(() => []),
                                        new Promise(resolve => setTimeout(() => resolve([]), 750))
                                      ]);
                                      return rows.some(row => Number(row.value) === v);
                                    }""", arg=v, timeout=20000, polling=500)
                                wait_for_value(2.)
                                append(5., '2026-11-09T08:01:00+08:00')
                                wait_for_value(5.)
                            with progress.step('realtime-line-chart'):
                                page.locator('[data-native="stream-preset"][data-preset="line"]').click()
                                result = workspace('Y Line', grouped=True)
                                require(any('event_time' in (v.get('group_by') or []) for v in result['panels'].values()),
                                        'Realtime chart does not use event_time axis')
                        finally:
                            client.delete(f'/api/v1/native-bi/streams/{stream_id}')
                        progress.data['asset_current_version'] = current
                        progress.data['aggregates'] = {'base': 100, 'updated': 190}
                        progress.persist()
                    finally:
                        browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=7)


def worker(report_path: Path) -> int:
    progress = Progress(report_path)
    try:
        run_acceptance(progress)
        progress.complete()
        return 0
    except BaseException as exc:
        if progress.data['status'] != 'failed':
            progress.data['status'] = 'failed'
            progress.data['exception'] = {'type': type(exc).__name__, 'message': str(exc)}
        progress.persist()
        print(f'[FAILED] {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        print(f'[REPORT] {report_path}', flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Real Chrome + DuckDB + Perspective integration acceptance')
    parser.add_argument('--max-seconds', type=int, default=300, help='Hard total timeout for acceptance worker')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT, help='JSON only; no screenshots')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    report_path = args.report.expanduser().resolve()
    if args.worker:
        return worker(report_path)
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--report', str(report_path)]
    print('[ACCEPTANCE] Reuse current Python and installed Google Chrome; no downloads, no screenshots.', flush=True)
    try:
        proc = subprocess.Popen(command, cwd=ROOT, env={**os.environ, 'PYTHONUNBUFFERED': '1'},
                                start_new_session=(os.name != 'nt'))
        try:
            return proc.wait(timeout=max(30, args.max_seconds))
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            last_stage = 'unknown'
            if report_path.is_file():
                try:
                    state = json.loads(report_path.read_text(encoding='utf-8'))
                    last_stage = state.get('stage', 'unknown')
                    state['status'] = 'timeout'
                    state['exception'] = {'type': 'HardTimeout',
                                          'message': f'Acceptance exceeded {args.max_seconds}s at {last_stage}'}
                    temporary = report_path.with_name(report_path.name + '.tmp')
                    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    temporary.replace(report_path)
                except (ValueError, OSError):
                    pass
            print(f'[TIMEOUT] {args.max_seconds}s exceeded at stage: {last_stage}. Report: {report_path}',
                  file=sys.stderr, flush=True)
            return 124
    except KeyboardInterrupt:
        if proc.poll() is None:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        print(f'[INTERRUPTED] Report: {report_path}', file=sys.stderr, flush=True)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
