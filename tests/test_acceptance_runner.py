"""Stable acceptance runner regressions without requiring Chrome/DuckDB binaries."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location('timepilot_acceptance', ROOT / 'scripts/accept_native_bi_live.py')
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_progress_persists_stages_and_failure(tmp_path):
    runner = load_runner()
    path = tmp_path / 'reports' / 'result.json'
    p = runner.Progress(path)
    with p.step('load-bundle'):
        pass
    state = json.loads(path.read_text())
    assert state['events'][0]['stage'] == 'load-bundle'
    assert state['events'][0]['status'] == 'passed'
    with pytest.raises(ValueError):
        with p.step('websocket-connect'):
            raise ValueError('remote table unavailable')
    state = json.loads(path.read_text())
    assert state['status'] == 'failed'
    assert state['stage'] == 'websocket-connect'
    assert state['exception']['type'] == 'ValueError'
    assert state['events'][-1]['status'] == 'failed'


def test_workspace_state_predicate_is_synchronous():
    runner = load_runner()
    assert 'async' not in runner.WORKSPACE_CHECK
    assert 'saveWorkspace' not in runner.WORKSPACE_CHECK
    assert "state === 'ready'" in runner.WORKSPACE_CHECK
    assert "state === 'error'" in runner.WORKSPACE_CHECK


def test_workspace_poll_waits_for_ready_then_serializes_once():
    runner = load_runner()
    class Page:
        def wait_for_function(self, expression, **kw):
            assert expression == runner.WORKSPACE_CHECK
            assert kw['polling'] == 250
            assert kw['timeout'] == 7500
            class Handle:
                def json_value(self):
                    return {'kind': 'ready'}
            return Handle()
        def locator(self, selector):
            assert selector == '#tp-native-perspective perspective-viewer'
            class Locator:
                def evaluate(self, js, timeout):
                    assert 'Promise.race' in js and timeout == 7000
                    return {'panels': {'p': {'plugin': 'Y Bar'}}}
            return Locator()
    assert runner.wait_workspace(Page(), 'Y Bar', grouped=False, timeout=7500)['panels']


def test_workspace_reports_frontend_error_without_waiting_for_missing_viewer():
    runner = load_runner()
    class Page:
        def wait_for_function(self, expression, **kwargs):
            assert 'perspectiveState' in expression
            assert 'kind' in expression
            class Handle:
                def json_value(self):
                    return {'kind': 'error', 'message': 'DuckDB WebSocket was closed: 1011'}
            return Handle()
        def locator(self, selector):
            raise AssertionError('The missing viewer must not be inspected after frontend failure')
    with pytest.raises(runner.AcceptanceFailure, match='DuckDB WebSocket was closed'):
        runner.wait_workspace(Page())


def test_workspace_early_false_is_explicit_failure_not_attribute_error():
    runner = load_runner()
    class Page:
        def wait_for_function(self, *_args, **_kwargs):
            class Handle:
                def json_value(self):
                    return False
            return Handle()
        def locator(self, selector):
            raise AssertionError('The missing viewer must not be inspected')
    with pytest.raises(runner.AcceptanceFailure, match='状态返回异常'):
        runner.wait_workspace(Page(), timeout=700)


def test_workspace_waits_for_plugin_and_group_by_completion():
    runner = load_runner()
    class Page:
        attempts=0
        def wait_for_function(self, *_args, **_kwargs):
            class Handle:
                def json_value(self): return {'kind': 'ready'}
            return Handle()
        def locator(self, _selector):
            parent = self
            class Locator:
                def evaluate(self, *_args, **_kwargs):
                    parent.attempts += 1
                    return {'panels': {'a': {'plugin': 'Datagrid',
                                            'group_by': [] if parent.attempts < 3 else ['region']}}}
            return Locator()
        def wait_for_timeout(self, milliseconds):
            assert milliseconds <= 300
    page=Page()
    result=runner.wait_workspace(page, 'Datagrid', grouped=True, timeout=3000)
    assert result['panels']['a']['group_by'] == ['region']
    assert page.attempts == 3


def test_real_playwright_does_not_accept_pending_async_false_as_ready():
    # Runs only in opted-in smoke checks; does not fetch any browser/downloads.
    import os, shutil
    if os.environ.get('TIMEPILOT_TEST_INSTALLED_BROWSER') != '1':
        pytest.skip('Set TIMEPILOT_TEST_INSTALLED_BROWSER=1 to exercise existing Chromium')
    binary = shutil.which('chromium') or shutil.which('google-chrome')
    if not binary:
        pytest.skip('No installed Chromium binary in test machine')
    from playwright.sync_api import sync_playwright, TimeoutError as PTimeout
    runner = load_runner()
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=binary, headless=True, args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content('<div id="app">TimePilot is still rendering</div>')
            # The old async predicate demonstrably returns `false` as a JSHandle.
            assert page.wait_for_function('async () => false', timeout=500).json_value() is False
            with pytest.raises(PTimeout):
                page.wait_for_function(runner.WORKSPACE_CHECK, timeout=400, polling=50)
            page.evaluate("""() => {
                const root = document.createElement('div');
                root.id='tp-native-perspective';root.dataset.perspectiveState='ready';
                const viewer=document.createElement('perspective-viewer');
                viewer.saveWorkspace=async ()=>({panels:{main:{plugin:'Datagrid',group_by:[]}}});
                root.appendChild(viewer);document.body.appendChild(root);
            }""")
            assert runner.wait_workspace(page, timeout=1500)['panels']['main']['plugin']=='Datagrid'
        finally:
            browser.close()

def test_perspective_v5_uses_named_client_and_surfaces_mount_failure():
    source = (ROOT / 'frontend/native-bi/src/main.js').read_text(encoding='utf-8')
    ui = (ROOT / 'src/timepilot/static/native-bi.js').read_text(encoding='utf-8')
    contract = (ROOT / 'frontend/native-bi/src/viewer-contract.mjs').read_text(encoding='utf-8')
    assert 'loadNamedClient(elem, client, tableRef, bounded)' in source
    assert 'viewer.load(client)' in contract
    assert 'viewer.restore(gridConfig(schema, tableName))' in contract
    assert 'attachViewer(container, socket, table' in source
    assert 'attachViewer(container, worker, table)' in source
    assert "setPerspectiveState(slot,'error',err.message)" in ui
    assert "setPerspectiveState(panel,'error',err.message)" in ui


def test_browser_acceptance_is_offline_and_has_hard_deadline():
    runner = (ROOT / 'scripts/accept_native_bi_live.py').read_text()
    launcher = (ROOT / 'scripts/launcher.py').read_text()
    front = (ROOT / 'frontend/native-bi/src/main.js').read_text()
    assert 'proc.wait(timeout=' in runner
    assert 'start_new_session=' in runner
    assert 'TIMEPILOT_ACCEPTANCE_MAX_SECONDS' in launcher
    assert 'TIMEPILOT_REBUILD_BI' not in launcher
    assert 'Promise.race' in front and 'Perspective WebSocket' in front
    assert 'async' not in load_runner().WORKSPACE_CHECK
    assert 'playwright install chromium' not in launcher
    assert 'page.screenshot(' not in runner
    assert 'docs/screenshots' not in runner
