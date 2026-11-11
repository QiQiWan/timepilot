"""Real v0.7 BI-to-forecast browser acceptance using the installed Chrome.

No browser download, no Python environment creation, no screenshots, no model
training. Tests the complete HTTP and UI integration in an isolated temp home.
Use: TIMEPILOT_WORKBENCH_BROWSER_TEST=1 bash TIMEPILOT.sh accept smart
"""
from __future__ import annotations

import io
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from openpyxl import Workbook
from playwright.sync_api import sync_playwright

from system_chrome import chrome_launch_options
from timepilot.api import create_app


def _example_workbook() -> bytes:
    wb = Workbook()
    wb.active.title = '说明'
    wb.active['A1'] = '业务年度记录；仅用于自动化测试'
    sheet = wb.create_sheet('车型月度明细')
    sheet.append(['国家/地区', '整车厂/品牌', '车型', '统计口径', '来源', '年份'] + [f'{m}月' for m in range(1, 13)])
    for y in (2022, 2023, 2024):
        sheet.append(['中国', '比亚迪', '汉', '销量', 'source-A', y] + [10 + y - 2022 + m for m in range(1, 13)])
    sheet.append(['中国', '比亚迪', '宋', '产量', 'source-A', 2024] + list(range(10, 22)))
    sheet.append(['美国', '丰田', 'Camry', '销量', 'source-B', 2024] + list(range(20, 32)))
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _wait_for_server(url: str) -> None:
    for _ in range(100):
        try:
            if httpx.get(url + '/api/v1/health', timeout=0.4).status_code == 200:
                return
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.1)
    raise RuntimeError('TimePilot HTTP server did not become healthy')


def _choose(page, field: str, option: str) -> None:
    selector = f'#wb-filter-{field}'
    page.locator(selector).select_option(option, timeout=15000)
    # Wait for the asynchronous analysis response and cascade refresh.
    page.wait_for_function("() => !!document.querySelector('#wb-summary .wb-kpis')", timeout=20000)
    page.locator(selector).wait_for(timeout=10000)
    assert page.locator(selector).input_value() == option


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='timepilot_v070_browser_') as folder:
        app = create_app(home=Path(folder), embedded_worker=False)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            _wait_for_server(url)
            print('[PASS] HTTP application health', flush=True)
            with httpx.Client(base_url=url, timeout=30) as http:
                result = http.post('/api/v1/smart-import/uploads', files={
                    'file': ('年度数据.xlsx', _example_workbook(),
                             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
                result.raise_for_status()
                info = result.json()
                identifier = info['import_id']
                result = http.post(f'/api/v1/smart-import/imports/{identifier}/transform', json={
                    'candidate_ids': info['suggested_candidate_ids']})
                result.raise_for_status()
                version = result.json()['version_id']
                assert result.json()['counts']['output_rows'] == 60
            print('[PASS] Multi-year Excel discovery and normalized version', flush=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**chrome_launch_options())
                try:
                    page = browser.new_page(viewport={'width': 1420, 'height': 960})
                    errors: list[str] = []
                    page.on('pageerror', lambda err: errors.append(str(err)))
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.goto(url + f'/imports/{identifier}/versions/{version}/workbench',
                              wait_until='domcontentloaded', timeout=30000)
                    page.locator('#wb-filter-country').wait_for(timeout=25000)
                    page.wait_for_function("() => !!document.querySelector('#wb-summary .wb-kpis')", timeout=25000)
                    assert page.locator('#wb-summary .wb-kpi strong').first.inner_text() == '60'
                    print('[PASS] Workbench, indexed facets and quality overview', flush=True)
                    for field, value in [('country', '中国'), ('brand', '比亚迪'),
                                         ('vehicle', '汉'), ('source_name', 'source-A'),
                                         ('statistical_scope', '销量')]:
                        _choose(page, field, value)
                    page.wait_for_function("() => document.querySelector('#wb-summary .wb-kpi strong')?.textContent === '36'", timeout=20000)
                    assert page.locator('#wb-trend svg').count() == 1
                    assert '年度数据.xlsx' in page.locator('#wb-samples').inner_text()
                    print('[PASS] Five-dimensional filter, monthly trend, and cell provenance', flush=True)
                    page.locator('#wb-view-name').fill('中国比亚迪汉销量')
                    page.locator('[data-wb="save-view"]').click()
                    page.locator('#wb-forecast #wb-draft-form').wait_for(timeout=15000)
                    assert '已关联视图' in page.locator('#wb-forecast').inner_text()
                    print('[PASS] Immutable saved selection view', flush=True)
                    page.locator('#wb-draft-form button[type=submit]').click()
                    page.wait_for_function("() => document.querySelector('#wb-forecast')?.textContent?.includes('没有真实发布时间')", timeout=20000)
                    assert page.locator('[data-wb="materialize"]').count() == 0
                    print('[PASS] Prediction draft blocks unknown publication times', flush=True)
                    page.locator('#wb-draft-form [name=publication_lag_days]').fill('35')
                    page.locator('#wb-draft-form button[type=submit]').click()
                    page.locator('#wb-forecast [data-wb="materialize"]').wait_for(timeout=25000)
                    page.locator('#wb-forecast [data-wb="materialize"]').click()
                    link = page.locator('#wb-forecast a[href^="/new?"]')
                    link.wait_for(timeout=30000)
                    with httpx.Client(base_url=url, timeout=15) as http:
                        assert http.get('/api/v1/runs').json() == [], 'Creating a dataset must not enqueue training'
                    link.click()
                    page.locator('#new-task-form').wait_for(timeout=20000)
                    assert page.locator('#task-dataset').input_value()
                    assert page.locator('#new-task-form [name=horizon]').input_value() == '3'
                    assert '来自已保存的 BI 筛选视图' in page.locator('#new-task-form').inner_text()
                    assert not errors, errors
                    print('[PASS] Ready draft, materialized dataset and manual model configuration; no training', flush=True)
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=8)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
