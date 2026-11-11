"""Offline Chromium smoke test of the real modeling confirmation drawer.
No browser download, network API, DuckDB or user data is required.
"""
from pathlib import Path
import shutil

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_modeling_drawer_browser_without_downloads():
    playwright=pytest.importorskip('playwright.sync_api')
    chrome=shutil.which('chromium') or shutil.which('google-chrome')
    if not chrome:
        pytest.skip('No existing browser available')
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=chrome,headless=True,
                                   args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page=browser.new_page()
            page.on('dialog',lambda dialog:dialog.accept())
            page.set_content('<!doctype html><html><body><main id="app"></main></body></html>')
            page.add_script_tag(content=(ROOT/'src/timepilot/static/modeling-ui.js').read_text(encoding='utf-8'))
            page.evaluate('''() => {
                window.calls=[];
                window.ctx={toast:(x)=>{window.lastToast=x},req:async(path,opts={})=>{
                    window.calls.push([path,opts.method||'GET']);
                    if(path.includes('/suggestions?'))return {profile:{source_rows:210,sample_rows:210,
                      fields:[{name:'设备编号',suggested_roles:[{role:'entity',confidence:.88}]},
                              {name:'采集时间',suggested_roles:[{role:'time',confidence:.98}]},
                              {name:'温度(℃)',suggested_roles:[{role:'target_numeric',confidence:.9}]}],
                      recommendation:{time:'采集时间',entity:'设备编号',target:'温度(℃)',
                        available_at:null,frequency:{frequency:'h'}}},
                      proposals:[{task_kind:'continuous_forecast',title:'设备温度趋势预测',
                        status:'review_required',reason:'需要确认预测目标'}]};
                    if(path.endsWith('/drafts')&&opts.method==='POST'){
                      const body=JSON.parse(opts.body);window.rawBody=body;
                      return {draft_id:'11111111-1111-4111-8111-111111111111',revision:1,status:'review_required',
                              version_id:body.version_id,name:body.name,view_id:body.view_id,
                              selection:{source:{row_count:210},selection_hash:'abc'},
                              mapping:body.mapping,plan:body.plan};
                    }
                    if(path.endsWith('/preflight'))return {ready:true,source_rows:210,
                       selected_rows:105,eligible_rows:105,series_count:1,
                       time_coverage:{start:'2026-03-01',end:'2026-03-05'},
                       evaluation:{validation_windows:3,test_horizon:3,primary_metric:'MAE'},
                       blockers:[],warnings:[]};
                    if(path.endsWith('/approve'))return {...window.rawBody,revision:1,
                        draft_id:'11111111-1111-4111-8111-111111111111',version_id:'base',
                        status:'approved',preflight:{ready:true,source_rows:210,selected_rows:105,eligible_rows:105,series_count:1,blockers:[],warnings:[],evaluation:{validation_windows:3,test_horizon:3,primary_metric:'MAE'}},mapping:window.rawBody.mapping,
                        plan:window.rawBody.plan,name:window.rawBody.name};
                    throw new Error('Unexpected '+path);
                }};
                document.addEventListener('click',e=>{
                  if(e.target.closest('[data-modeling]'))window.TimePilotModeling.click(e)
                });
                document.addEventListener('submit',e=>{
                  if(e.target.id==='tp-modeling-form')window.TimePilotModeling.submit(e)
                });
                document.addEventListener('change',e=>window.TimePilotModeling.change(e));
            }''')
            page.evaluate("TimePilotModeling.open({ctx:window.ctx,projectId:'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',assetId:'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb',versionId:'base'})")
            page.locator('button[data-modeling="recommend"]').click()
            assert page.locator('select[name="target"]').input_value()=='温度(℃)'
            assert page.locator('select[name="time"]').input_value()=='采集时间'
            assert page.locator('select[name="entity"]').input_value()=='设备编号'
            assert page.locator('select[name="frequency"]').input_value()=='h'
            assert page.locator('input[name="target_unit"]').input_value()=='℃'
            assert page.locator('select[name="task_family"]').input_value()=='equipment_state'
            page.locator('select[name="availability_policy"]').select_option('declared_lag')
            page.locator('input[name="publication_lag_days"]').fill('0')
            page.locator('input[name="confirm_scope"]').check()
            page.locator('button[type="submit"]').click()
            page.locator('button[data-modeling="approve"]').wait_for()
            body=page.evaluate('window.rawBody')
            assert body['mapping']['target']=='温度(℃)'
            assert body['mapping']['availability_policy']=='declared_lag'
            assert body['plan']['frequency']=='h'
            assert body['mapping']['target_unit']=='℃'
            assert body['plan']['task_family']=='equipment_state'
            assert body['confirm_scope'] is True
            page.locator('button[data-modeling="approve"]').click()
            page.locator('button[data-modeling="materialize"]').wait_for()
            assert page.locator('#tp-modeling-body').count()==1
        finally:
            browser.close()
