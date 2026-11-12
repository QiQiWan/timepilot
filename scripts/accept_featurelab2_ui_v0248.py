"""Offline FeatureLab 2.0 four-page UI check with installed Chromium.

Mocks only HTTP data; verifies user interaction and state rendering, never
claims a real DuckDB/Arrow/Perspective computational acceptance.
"""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'


def main():
    arg=argparse.ArgumentParser()
    arg.add_argument('--output',type=Path)
    opt=arg.parse_args()
    executable=shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('chromium-browser')
    if not executable:
        print('BLOCKED: Installed Chrome/Chromium unavailable')
        return 2
    from playwright.sync_api import sync_playwright
    results={};errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,headless=True,
                                  args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        page.on('pageerror',lambda err:errors.append(str(err)))
        page.set_content('<!doctype html><html lang="zh-CN"><body><main id="tp-feature-lab"></main></body></html>')
        page.add_style_tag(path=str(STATIC/'feature-lab2.css'))
        for file in ('feature-profile-ui.js','feature-builder-ui.js','feature-evaluation-ui.js',
                     'feature-catalog-ui.js','feature-shell.js'):
            page.add_script_tag(path=str(STATIC/file))
        page.evaluate('''() => {
            window.prompt=()=>{throw Error('Browser prompt is forbidden')};
            window.confirm=()=>{throw Error('Browser confirm is forbidden')};
            const fields=[{field_id:'fld_1',source_name:'A1',source_column_index:0,
               physical_type:'DOUBLE',semantic_type:'numeric',confirmation:'unconfirmed'},
              {field_id:'fld_2',source_name:'A2',source_column_index:1,
               physical_type:'DOUBLE',semantic_type:'numeric',confirmation:'unconfirmed'}];
            window.__flctx={
              navigate:(url)=>{window.__lastNav=url;},
              toast:(message)=>{window.__toast=message;},
              req:async(url,opts)=>{
                if(url.endsWith('/native-assets'))return [{native_asset_id:'a1',asset:{name:'设备 A',columns:[{name:'A1'},{name:'A2'}]}}];
                if(url.includes('/contracts'))return {asset_id:'a1',version_id:'base',
                     revision:0,state:'detected',source_sha256:'a'.repeat(64),fields};
                if(url.includes('/operators'))return {operators:[{operator_id:'arithmetic.binary',title:'两列计算',available:true}]};
                if(url.includes('/profiles')||url.includes('/profile-jobs'))return {items:[]};
                if(url.includes('/sets'))return [];
                if(url.includes('/agent/plans'))return {plan_id:'p1',status:'needs_clarification',
                   plan:{planner:'local',actions:[],reason:'请选择实际字段'},steps:[]};
                throw Error('Unexpected stub API '+url);
              }
            };
          }''')
        page.evaluate('TimePilotFeatureV2UI.render(window.__flctx,"p1","profile")')
        page.locator('nav.tp-fl2-tabs a').first.wait_for()
        assert page.locator('nav.tp-fl2-tabs a').count()==4
        assert page.get_by_text('数据画像',exact=True).count()>=1
        assert page.locator('[data-feature-v2="edit-contract"]').count()==1
        results['profile_tab']='passed'
        page.evaluate('TimePilotFeatureV2UI.click({target:document.querySelector("[data-feature-v2=edit-contract]"),preventDefault(){}})')
        page.locator('#tp-fl2-dialog-form').wait_for()
        assert page.locator('.tp-fl2-field-row').count()==2
        page.evaluate('TimePilotFeatureV2UI.click({target:document.querySelector("[data-feature-v2=close-modal]"),preventDefault(){}})')
        assert page.locator('#tp-fl2-dialog-form').count()==0
        results['contract_modal_no_native_prompt']='passed'
        page.evaluate('TimePilotFeatureV2UI.render(window.__flctx,"p1","build")')
        page.locator('#tp-fl2-add-step').wait_for()
        assert page.locator('select[name="first"]').count()==1
        assert page.locator('#tp-fl2-add-step input[name="name"]').count()==1
        results['builder_select_and_form']='passed'
        page.evaluate('TimePilotFeatureV2UI.render(window.__flctx,"p1","evaluate")')
        page.locator('#tp-fl2-evaluation-form').wait_for()
        assert page.get_by_text('无标签：质量与结构检查').count()>=1
        results['evaluation_tab']='passed'
        page.evaluate('TimePilotFeatureV2UI.render(window.__flctx,"p1","catalog")')
        assert page.get_by_text('还没有 v2 特征集',exact=False).count()>=1
        page.evaluate('TimePilotFeatureV2UI.click({target:document.querySelector(".tp-fl2-chat-toggle"),preventDefault(){}})')
        assert page.locator('#tp-fl2-agent-form').is_visible()
        results['catalog_and_assistant']='passed'
        assert not errors,errors
        results['javascript_errors']=errors
        browser.close()
    report={'status':'passed_component_chromium','checks':results,'chrome':executable,
       'limits':'Mock HTTP; no native Perspective, DuckDB or Arrow numerical E2E.'}
    if opt.output:
        opt.output.parent.mkdir(parents=True,exist_ok=True)
        opt.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
