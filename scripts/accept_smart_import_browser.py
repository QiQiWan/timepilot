"""Real installed-Chrome/Chromium smoke: ZIP -> visual map -> version -> pivot.

Uses current Python and locally installed browser. No Playwright browser downloads,
no npm, no screenshots and no synthetic images in release files.
"""
from __future__ import annotations

import io
import os
import socket
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import uvicorn
from openpyxl import Workbook
from playwright.sync_api import sync_playwright

from timepilot.api import create_app
from system_chrome import chrome_launch_options


def _sample():
    def workbook(year, value):
        wb = Workbook()
        notes = wb.active
        notes.title = '说明'
        notes.append(['年度说明', year])
        sheet = wb.create_sheet('车型月度明细')
        sheet.append(['车型', '年份', '统计口径', '1月', '2月', '3月'])
        sheet.append(['Sedan', year, '销量', value, value+1, value+2])
        file = io.BytesIO()
        wb.save(file)
        return file.getvalue()
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('MarkLines_2024.xlsx', workbook(2024, 10))
        archive.writestr('MarkLines_2025.xlsx', workbook(2025, 20))
    return data.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix='tp_smart_browser_') as folder:
        app = create_app(home=Path(folder), embedded_worker=False)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        url=f'http://127.0.0.1:{port}'
        try:
            import httpx
            for _ in range(100):
                try:
                    if httpx.get(url+'/api/v1/health',timeout=.4).status_code==200:break
                except Exception:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError('Local API failed to start')
            print('[PASS] API health', flush=True)
            with sync_playwright() as playwright:
                options=chrome_launch_options()
                browser=playwright.chromium.launch(**options)
                try:
                    page=browser.new_page(viewport={'width':1480,'height':960})
                    errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(url+'/imports',wait_until='domcontentloaded',timeout=20000)
                    page.locator('#smart-upload-form input[type="file"]').wait_for(timeout=20000)
                    print('[PASS] Smart import landing UI',flush=True)
                    page.locator('input[name="file"]').set_input_files({
                        'name':'years.zip','mimeType':'application/zip','buffer':_sample()})
                    page.locator('#smart-upload-form button[type="submit"]').click()
                    page.wait_for_url('**/imports/*',timeout=20000)
                    page.locator('input[name="smart-region"]:checked').first.wait_for(timeout=20000)
                    assert page.locator('input[name="smart-region"]:checked').count()==1
                    assert '车型月度明细' in page.locator('body').inner_text()
                    print('[PASS] 2-workbook discovery and preferred group selection',flush=True)
                    page.locator('#smart-transform-btn').click()
                    page.locator('#smart-version-preview .smart-kpis strong').first.wait_for(timeout=25000)
                    assert page.locator('#smart-version-preview .smart-kpis strong').first.inner_text()=='6'
                    assert page.locator('.smart-chart svg').count()>0
                    assert 'MarkLines_2024.xlsx' in page.locator('#smart-version-preview').inner_text()
                    print('[PASS] Background conversion, provenance and fallback BI chart',flush=True)
                    page.locator('#smart-scope').select_option('销量')
                    page.locator('#smart-version-preview .smart-chart svg').wait_for(timeout=10000)
                    assert not errors, errors
                    print('[PASS] Scope selection and no page errors',flush=True)
                    page.reload(wait_until='domcontentloaded')
                    page.locator('#smart-version-preview .smart-chart svg').wait_for(timeout=20000)
                    print('[PASS] Reload / version restoration',flush=True)
                finally:
                    browser.close()
        finally:
            server.should_exit=True
            worker.join(timeout=6)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
