"""Installed Chromium component smoke test; does not imply Perspective JS/WASM E2E."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    opt=parser.parse_args()
    browser=shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('chromium-browser')
    if not browser:
        print(json.dumps({'status':'blocked_environment','reason':'No installed Chromium/Chrome'},ensure_ascii=False))
        return 2
    results={}
    with sync_playwright() as playwright:
        engine=playwright.chromium.launch(executable_path=browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=engine.new_page(viewport={'width':1366,'height':768})
        js_errors=[]
        page.on('pageerror',lambda err:js_errors.append(str(err)))
        page.set_content('''<!doctype html><html lang="zh-CN"><body>
          <main id="project">
            <div class="page-heading"><h1>设备数据表</h1><a href="/projects/p1">项目列表</a></div>
            <div id="native-explore" style="height:300px">原生 Perspective 挂载区域</div>
            <section id="native-query">兼容查询</section>
          </main></body></html>''')
        for css in ('bi-workspace-layout.css','bi-assistant-fab.css','bi-dialogs.css','bi-help.css'):
            page.add_style_tag(path=str(STATIC/css))
        for js in ('bi-dialogs.js','bi-help.js','bi-assistant-fab.js','bi-workspace-layout.js'):
            page.add_script_tag(path=str(STATIC/js))
        page.evaluate('''() => {
          window.prompt=()=>{throw Error('Native browser prompt must not be used')};
          window.confirm=()=>{throw Error('Native browser confirm must not be used')};
          window.TimePilotBIWorkspaceLayout.mount(document.getElementById('project'));
        }''')
        assert page.locator('[data-workspace-action="manual"]').count()==1
        page.click('[data-workspace-action="manual"]')
        assert page.locator('.tp-bi-help-overlay').count()==1
        page.fill('.tp-bi-help-search','筛选')
        assert page.locator('.tp-bi-help-topic').count()>0
        page.keyboard.press('Escape')
        assert page.locator('.tp-bi-help-overlay').count()==0
        results['manual_search_and_escape']='pass'
        page.evaluate('''() => {
          const ctx={projectId:'p1',req:async(url,options)=>options?{}:{mode:'closed',width:500,height:480}};
          window.__mounted=TimePilotBIFloating.mount(ctx,{asset_id:'a1'});
        }''')
        page.evaluate('window.__mounted')
        page.click('[data-fab-toggle]')
        assert page.locator('.tp-fab-dialog').is_visible()
        assert page.locator('.tp-fab-head button').count()==1
        style=page.locator('.tp-fab-dialog').evaluate('(e)=>getComputedStyle(e).resize')
        assert style=='both',style
        assert page.locator('.tp-fab-dialog').evaluate('(e)=>Math.round(e.getBoundingClientRect().width)')in (500,501,502)
        assert page.locator('.tp-fab-dialog').evaluate('(e)=>Math.round(e.getBoundingClientRect().height)')in (480,481,482)
        page.evaluate('''() => {
          window.__modal=TimePilotBIDialogs.open({title:'选择聚合字段',description:'从源数据字段中选择',fields:[
            {name:'field',label:'数据字段',type:'select',options:['A1','A2','A14'],value:'A2'},
            {name:'interval',label:'统计窗口',type:'number',value:5,min:2,max:240}
          ]});
        }''')
        assert page.locator('.tp-bi-dialog-shade').is_visible()
        page.select_option('.tp-bi-dialog select','A14')
        page.fill('.tp-bi-dialog input[type=number]','7')
        page.click('.tp-bi-dialog-actions button[type=submit]')
        assert page.evaluate('window.__modal')=={'field':'A14','interval':7}
        assert page.locator('.tp-bi-dialog-shade').count()==0
        results['visual_field_choice_and_sizing']='pass'
        page.locator('.tp-fab-close').click()
        assert page.locator('.tp-fab-dialog').is_hidden()
        page.locator('[data-fab-toggle]').click()
        assert page.locator('.tp-fab-dialog').is_visible()
        results['single_close_reopen']='pass'
        page.evaluate('TimePilotBIFloating.leave()')
        assert page.locator('.tp-bi-fab-shell').count()==0
        assert not js_errors,js_errors
        results['javascript_errors']=[]
        engine.close()
    output={'status':'passed_component_chromium','browser':browser,'checks':results,
            'limits':'No real Perspective WASM/DuckDB engine exercised.'}
    if opt.output:
        opt.output.parent.mkdir(parents=True,exist_ok=True)
        opt.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
