"""Build-to-browser contract regressions: prevent valid WASM / empty JS exports."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from timepilot.native_bi_bundle import (PUBLIC_EXPORTS, bundle_status,
                                         generate_manifest, frontend_source_hash)

ROOT = Path(__file__).resolve().parents[1]


def minimal_bundle(folder: Path, *, exports=PUBLIC_EXPORTS):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'perspective-entry.js').write_text('\n'.join(
        f'export function {name}(){{}}' for name in exports), encoding='utf-8')
    (folder / 'perspective-entry.css').write_text('perspective-viewer{display:block}', encoding='utf-8')
    (folder / 'perspective-contract.json').write_text(json.dumps({
        'format': 1, 'entry': 'perspective-entry.js', 'exports': list(exports)}), encoding='utf-8')
    (folder / 'assets').mkdir(exist_ok=True)
    (folder / 'assets/perspective-server-test.wasm').write_bytes(b'\x00asmabcd')
    (folder / 'assets/perspective-viewer-test.wasm').write_bytes(b'\x00asmefgh')


def test_build_contract_requires_all_compiled_browser_exports(tmp_path):
    minimal_bundle(tmp_path)
    manifest = generate_manifest(tmp_path)
    assert 'perspective-contract.json' in manifest['files']
    assert bundle_status(tmp_path)['ready']
    (tmp_path / 'perspective-contract.json').write_text(json.dumps({
        'format':1, 'entry':'perspective-entry.js', 'exports':['mountPerspective']}))
    problems = bundle_status(tmp_path)['problems']
    assert any('mountPerspectiveRemote' in problem for problem in problems)
    with pytest.raises(ValueError, match='mountPerspectiveRemote'):
        generate_manifest(tmp_path)


def test_valid_wasm_and_sha_never_override_missing_js_exports(tmp_path):
    minimal_bundle(tmp_path)
    generate_manifest(tmp_path)
    (tmp_path / 'perspective-entry.js').write_text('export const timepilot = 1;', encoding='utf-8')
    reasons = bundle_status(tmp_path)['problems']
    assert any('JS 文件中缺少' in reason for reason in reasons)


def test_frontend_fingerprint_covers_build_contract_source(tmp_path):
    front = tmp_path / 'frontend'
    (front / 'src').mkdir(parents=True)
    p = front / 'src/build-contract.mjs'
    p.write_text('export const PUBLIC_EXPORTS = ["mountPerspectiveRemote"];')
    before = frontend_source_hash(front)
    p.write_text('export const PUBLIC_EXPORTS = [];')
    assert frontend_source_hash(front) != before


def test_build_config_and_main_define_same_required_browser_exports():
    config = (ROOT / 'frontend/native-bi/vite.config.mjs').read_text(encoding='utf-8')
    main = (ROOT / 'frontend/native-bi/src/main.js').read_text(encoding='utf-8')
    plugin = (ROOT / 'frontend/native-bi/src/build-contract.mjs').read_text(encoding='utf-8')
    js = (ROOT / 'src/timepilot/static/native-bi.js').read_text(encoding='utf-8')
    assert "preserveEntrySignatures: 'strict'" in config
    assert 'perspectiveExportContractPlugin()' in config
    for function in PUBLIC_EXPORTS:
        assert f'export async function {function}(' in main
        assert f"'{function}'" in plugin
        assert f"'{function}'" in js
    assert 'perspective-build.json' in js and "entry.sha256.slice(0,16)" in js


def test_real_browser_acceptance_checks_module_before_duckdb_datagrid():
    path = ROOT / 'scripts/accept_native_bi_live.py'
    content = path.read_text(encoding='utf-8')
    assert content.index("step('compiled-es-module-contract')") < content.index("step('perspective-websocket-and-datagrid')")
    assert 'BROWSER_EXPORT_CHECK' in content
    assert 'Object.keys(module)' in content
    assert 'contract[\'missing\']' in content
    assert 'playwright install chromium' not in content

@pytest.mark.skipif(__import__('os').environ.get('TIMEPILOT_TEST_INSTALLED_BROWSER') != '1',
                    reason='Explicit installed-Chrome validation only; no browser downloads')
def test_playwright_checks_actual_module_namespace_over_http_without_downloads():
    """A browser import of compiled-style JS must be checked before BI UI starts."""
    import importlib.util
    import sys
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, str(ROOT / 'scripts'))
    from system_chrome import launch_installed_chrome
    spec = importlib.util.spec_from_file_location('tp_accept_v058', ROOT / 'scripts/accept_native_bi_live.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    entry_sha = 'a' * 64
    fake_manifest = json.dumps({'files': {'perspective-entry.js': {'sha256':entry_sha}}})
    good_js = '\n'.join(f'export function {name}(){{}}' for name in PUBLIC_EXPORTS)
    bad_js = 'export function mountPerspective(){}'

    def run_once(browser, js):
        page = browser.new_page()
        base = 'http://127.0.0.1:65432'
        def route(req):
            url = req.request.url
            if url.endswith('/perspective-build.json'):
                req.fulfill(status=200, content_type='application/json', body=fake_manifest)
            elif '/perspective-entry.js' in url:
                req.fulfill(status=200, content_type='application/javascript', body=js)
            else:
                req.fulfill(status=200, content_type='text/html', body='<html><body>contract test</body></html>')
        page.route('**/*', route)
        try:
            try:
                page.goto(base + '/', wait_until='domcontentloaded', timeout=8000)
            except Exception as exc:
                if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                    pytest.skip('This runner blocks local HTTP navigation; run on the target Mac with Chrome')
                raise
            return page.evaluate(runner.BROWSER_EXPORT_CHECK)
        finally:
            page.close()

    with sync_playwright() as p:
        browser = launch_installed_chrome(p)
        try:
            good = run_once(browser, good_js)
            assert not good['missing']
            assert 'mountPerspectiveRemote' in good['exported']
            bad = run_once(browser, bad_js)
            assert 'mountPerspectiveRemote' in bad['missing']
        finally:
            browser.close()
