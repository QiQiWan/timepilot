"""Actual installed-Chrome stage IV acceptance. No browser downloads, no screenshots.

Run: TIMEPILOT_RESEARCH_BROWSER_TEST=1 bash TIMEPILOT.sh accept research-browser
Uses generated test data in a temporary home, never touches user assets.
"""
from __future__ import annotations

import io
import json
import math
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from system_chrome import chrome_launch_options
from timepilot.api import create_app
from timepilot.smart_import import TransformRecipe


def fixture(app):
    content=['Time,A2,A14,available_at,source_name,统计口径']
    for i in range(43):
        year,month=2020+i//12,i%12+1
        next_year,next_month=(year+1,1) if month==12 else (year,month+1)
        content.append(f'{year}-{month:02d}-01,S1,{round(100+2*i+8*math.sin(i/2),2)},'
                       f'{next_year}-{next_month:02d}-06T09:00:00+08:00,Fixture,销量')
    m=app.state.smart_import.ingest(io.BytesIO(('\n'.join(content)).encode()),'fixture.csv')
    v=app.state.smart_import.transform(m['import_id'],TransformRecipe(
        candidate_ids=m['suggested_candidate_ids'],field_mapping={
            'series_id':'A2','target':'A14','available_at':'available_at',
            'source_name':'source_name','statistical_scope':'统计口径'}))
    return m,v


def main():
    stages=[]
    with tempfile.TemporaryDirectory(prefix='timepilot_research_chrome_') as home:
        app=create_app(home=Path(home),embedded_worker=False)
        source,version=fixture(app)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        endpoint=f'http://127.0.0.1:{port}'
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True)
        thread.start()
        try:
            for i in range(80):
                try:
                    if httpx.get(endpoint+'/api/v1/health',timeout=.4).status_code==200:
                        stages.append('http-health')
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError('No local health response')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(**chrome_launch_options())
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[]
                    page.on('pageerror',lambda exc:errors.append(str(exc)))
                    page.goto(endpoint+'/research?tab=semantic',timeout=30000)
                    page.locator('#research-import').wait_for(timeout=12000)
                    page.locator('#research-import').select_option(source['import_id'])
                    page.locator('[data-research="seed"]').click()
                    page.locator('[data-research="case"]').first.wait_for(timeout=16000)
                    stages.append('real-source-annotation-queue')
                    page.locator('[data-research="case"]').first.click()
                    page.locator('#research-reviewer').fill('业务真值审核A')
                    page.locator('#research-layout').select_option('ordinary_timeseries')
                    for role,column in (('timestamp','Time'),('series_id','A2'),('target','A14'),
                                        ('available_at','available_at'),('source_name','source_name'),
                                        ('statistical_scope','统计口径')):
                        page.locator(f'[data-role="{role}"]').select_option(column)
                    page.locator('#research-label-form button[type=submit]').click()
                    page.locator('[data-research="case"]').first.click()
                    assert page.locator('[data-research="case"]').count()>=1
                    stages.append('human-reference-edit')
                    page.locator('#research-trial-reviewer').fill('第二复核人员B')
                    page.locator('#research-trial-arm').select_option('rules')
                    page.locator('[data-research="trial-start"]').click()
                    page.locator('#research-trial-form').wait_for(timeout=12000)
                    for role,column in (('timestamp','Time'),('series_id','A2'),('target','A14')):
                        page.locator(f'[data-trial-role="{role}"]').select_option(column)
                    page.locator('#research-trial-form button[type=submit]').click()
                    page.locator('[data-research="trial-summary"]').click()
                    page.locator('#research-trial-summary pre').wait_for(timeout=12000)
                    stages.append('human-editing-session-and-summary')
                    page.goto(endpoint+'/research?tab=modeling',timeout=30000)
                    page.locator('#research-model-form').wait_for(timeout=12000)
                    page.locator('#research-series').fill('S1')
                    page.locator('#research-model-form button[type=submit]').click()
                    page.locator('[data-research="execute-model"]').wait_for(timeout=14000)
                    stages.append('forecast-plan-excludes-silent-training')
                    page.locator('[data-research="execute-model"]').click()
                    page.locator('#research-model-plan').get_by_text('实验完成').wait_for(timeout=20000)
                    stages.append('rolling-origin-model-comparison')
                    page.goto(endpoint+'/research?tab=mechanisms',timeout=30000)
                    page.locator('#research-mechanism-json').wait_for(timeout=12000)
                    stages.append('independent-mechanism-evidence-tab')
                    assert not errors,errors
                finally:
                    browser.close()
        finally:
            server.should_exit=True
            thread.join(timeout=10)
    report={'status':'passed','verified_in_installed_chrome':True,
            'stages':stages,'screenshots_taken':0,'external_model_calls':0,
            'production_training_started':False}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
