"""v0.8.0 UI acceptance with the OS-installed Chrome, no downloads or training."""
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


def workbook():
    book=Workbook();book.active.title='说明';book.active['A1']='示例文件'
    sheet=book.create_sheet('车型月度明细')
    sheet.append(['国家/地区','品牌','车型','统计口径','来源','年份','1月','2月','3月'])
    sheet.append(['中国','某品牌','车型甲','销量','source-A',2025,10,12,13])
    sheet.append(['中国','某品牌','车型乙','产量','source-B',2025,7,8,9])
    file=io.BytesIO();book.save(file);return file.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix='timepilot_v080_accept_') as home:
        app=create_app(home=Path(home),embedded_worker=False)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            for _ in range(80):
                try:
                    if httpx.get(url+'/api/v1/health',timeout=.5).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('HTTP service did not start')
            with httpx.Client(base_url=url,timeout=25) as http:
                upload=http.post('/api/v1/smart-import/uploads',files={'file':('sample.xlsx',workbook(),
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
                upload.raise_for_status()
                iid=upload.json()['import_id']
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(**chrome_launch_options())
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[]
                    page.on('pageerror',lambda exc:errors.append(str(exc)))
                    page.goto(url+f'/imports/{iid}',wait_until='domcontentloaded',timeout=30000)
                    page.locator('#data-agent-panel').wait_for(timeout=25000)
                    assert page.locator('input[name=smart-region]:checked').count()>=1
                    page.locator('#agent-user-request').fill('检查月度销量的统计口径并准备趋势分析')
                    page.locator('[data-smart="agent-propose"]').click()
                    page.locator('.agent-report .agent-flow').wait_for(timeout=30000)
                    assert page.locator('.agent-step').count()==7
                    assert page.locator('.agent-issue').count()>=1
                    print('[PASS] 6 controlled tools and reviewable plan',flush=True)
                    page.locator('#agent-scope').select_option('__keep__')
                    for item in page.locator('input[name="agent-review"]').all():item.check()
                    page.locator('#agent-confirm').check()
                    page.locator('[data-smart="agent-execute"]').click()
                    page.locator('#agent-execution-status .success a').wait_for(timeout=40000)
                    print('[PASS] Explicit consent, version conversion and job progress',flush=True)
                    with httpx.Client(base_url=url,timeout=20) as http:
                        versions=http.get(f'/api/v1/smart-import/imports/{iid}/versions').json()
                        assert len(versions)==1
                        assert versions[0]['counts']['output_rows']==6
                        assert set(versions[0]['statistical_scopes'])=={'销量','产量'}
                        assert http.get('/api/v1/runs').json()==[]
                    page.locator('#agent-execution-status .success a').click()
                    page.locator('#wb-filter-country').wait_for(timeout=25000)
                    assert not errors,errors
                    print('[PASS] Agent -> immutable version -> BI; no model training',flush=True)
                finally:browser.close()
        finally:
            server.should_exit=True
            thread.join(timeout=8)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
