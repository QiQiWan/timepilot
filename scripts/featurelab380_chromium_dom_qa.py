"""Real Chromium DOM acceptance for the task-first frontend with deterministic mocked APIs.

This is a browser interaction / routing test, NOT physical DuckDB, Perspective,
or enterprise authentication acceptance. No new browser is downloaded.
"""
from __future__ import annotations

import argparse
import json
import shutil
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'

HTML='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="/assets/styles.css"><link rel="stylesheet" href="/assets/analysis-workspace.css">
<link rel="stylesheet" href="/assets/data-dashboard-v0372.css"><link rel="stylesheet" href="/assets/data-analysis-ui.css">
</head><body style="max-width:1300px;margin:auto"><div id="tp-analysis-workspace"></div>
<script src="/assets/data-dashboard-v0372.js" defer></script>
<script src="/assets/data-analysis-ui.js" defer></script>
<script src="/assets/analysis-workspace.js" defer></script>
<script>
window.__calls=[];window.__renders={feature:0,output:0};
const allFields=Array.from({length:31},(_,i)=>({field_id:'f'+i,source_name:'Channel '+i,
  physical_type:'DOUBLE',unit:'C',null_rate:0,role:'unspecified'}));
const versionSha=()=>(new URLSearchParams(location.search).get('version')==='v2'?'b':'a').repeat(64);
window.TimePilotFeatureV2UI={render:async function(){window.__renders.feature++;const el=document.querySelector('#tp-feature-lab');
 if(el)el.innerHTML='<h2>特征构造（测试控制器）</h2>';},dispose(){}};
window.TimePilotAnalysisInsightsUI={mount(){},refresh:async()=>{}};
window.TimePilotAnalysisDeliveryUI={render:async function(_ctx,_pid,host){window.__renders.output++;
 if(host)host.innerHTML='<h2>同源成果（测试控制器）</h2>';}};
const ctx={
  req:async function(path,opt){
    const payload=opt?.body?JSON.parse(opt.body):null;
    window.__calls.push({path,payload});
    if(path.endsWith('/native-assets'))return [{native_asset_id:'a',asset:{name:'Factory Data',columns:['c']}}];
    if(path.includes('/native-bi/assets/a/versions'))return {versions:[{version_id:'v2'}]};
    if(path.includes('/data-analysis?'))return {status:'ready',source_sha256:versionSha(),contract_revision:3,
      row_count:100,column_count:31,profile:{fields:allFields,topology:{safe_for_windows:false}},
      findings:[],recommendations:[],charts:{missing:[]}};
    if(path.includes('/feature-lab/workspace?'))return {feature_preview:{status:'not_started'}};
    if(path.endsWith('/feature-lab/workspace/start'))return {feature_preview:{status:'finished'}};
    const base={asset_id:'a',version_id:new URLSearchParams(location.search).get('version')||'base',
      source_sha256:versionSha(),contract_revision:3,scope_sha256:'c'.repeat(64)};
    if(path.endsWith('/dashboard')){
      const ids=payload.field_ids;
      return {...base,selection:{field_ids:ids,fields_truncated:ids.length<31},filters:payload.filters||[],
       fields:ids.map(field_id=>({field_id,name:field_id,numeric:true,finite_count:100,non_null:100,
         null_count:0,null_rate:0,stddev:1,median:3,p25:1,p75:4,min:0,max:6})),correlations:[],
       metrics:{records:100,source_records:100,columns:31,numeric_fields:31,selected_fields:ids.length,
        missing_percent:0,missing_cells:0,invalid_numeric_cells:0,non_finite_numeric_cells:0,
        conversion_failed_cells:0,coverage_percent:100,confirmed_time:false,
        metric_scope:{field_ids:ids,rows:100,denominator_cells:100*ids.length}},
       evidence_analysis:{status:'insufficient_evidence',message:'No verified observations'}};
    }
    if(path.endsWith('/dashboard/chart'))return {...base,field_name:payload.field_id,kind:payload.kind,
       rows_filtered:100,points:[]};
    throw Error('Mock route missing: '+path);
  },
  navigate:async function(href){history.pushState({},'',href);await render();}
};
async function render(){await window.TimePilotAnalysisWorkspace.render(ctx,'p');}
window.addEventListener('popstate',()=>render());
window.addEventListener('DOMContentLoaded',()=>render());
</script></body></html>'''

def run(browser_path,screenshot=None):
    from playwright.sync_api import sync_playwright
    # Chromium in restricted runners may block all socket navigation. Keep
    # this DOM test fully offline and load *actual* shipped JS via set_content.
    html=re.sub(r'<script src="/assets/([^"/]+)" defer></script>',
        lambda m:'<script>'+ (STATIC/m.group(1)).read_text(encoding='utf-8')+'</script>',HTML)
    html=re.sub(r'<link rel="stylesheet" href="/assets/([^"/]+)">',
        lambda m:'<style>'+(STATIC/m.group(1)).read_text(encoding='utf-8')+'</style>',html)
    html=html.replace('window.__calls=[];',
        "window.__routeChanges=[];history.pushState=(a,b,u)=>window.__routeChanges.push(['push',u]);"+
        "history.replaceState=(a,b,u)=>window.__routeChanges.push(['replace',u]);window.__calls=[];")
    evidence={'browser_path':browser_path,'steps':[],'errors':[],'url':None}
    def step(name,passed,details=None):
        evidence['steps'].append({'check':name,'passed':bool(passed),'detail':details})
        if not passed:raise AssertionError(name+' failed '+str(details))
    try:
      with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=browser_path,headless=True,
             args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(viewport={'width':1365,'height':900})
        page=context.new_page()
        page.on('pageerror',lambda e:evidence['errors'].append(str(e)))
        page.set_content(html,wait_until='load')
        page.locator('#tp-analysis-overview').wait_for(timeout=10000)
        calls=page.evaluate('window.__calls')
        step('overview does not run preview',not any('feature-lab/workspace' in r['path'] for r in calls))
        page.locator('[data-analysis-section="quality"]').click()
        page.locator('#tp-analysis-quality').wait_for()
        page.locator('[data-analysis-section="statistics"]').click()
        page.locator('[data-testid="data-dashboard-372"]').wait_for()
        page.wait_for_function('window.__calls.some(r => r.path.endsWith("/dashboard"))')
        initial=page.evaluate('window.__calls.filter(r=>r.path.endsWith("/dashboard")).at(-1).payload.field_ids')
        step('statistics initial bounded field selection',len(initial)==24,len(initial))
        # Selection of a 25th field is blocked until an old one is deselected.
        page.locator('details.tp-dash-field-picker summary').click()
        page.locator('input[data-dash="stat-field"][value="f0"]').uncheck()
        # Picker stays expanded while users select multiple channels.
        step('field picker remains expanded after change',
             page.locator('details.tp-dash-field-picker').evaluate('(el)=>el.open'))
        page.locator('input[data-dash="stat-field"][value="f25"]').check()
        selected=page.evaluate('window.__calls.filter(r=>r.path.endsWith("/dashboard")).at(-1).payload.field_ids')
        step('31-field custom selection reaches API',len(selected)==24 and 'f25' in selected and 'f0' not in selected)
        step('source-scoped URL state was written',bool(page.evaluate('window.__routeChanges.some(x=>x[1]?.includes("da_source"))')))
        for i in range(30):
            page.locator('[data-analysis-section="quality"]').click()
            page.locator('#tp-analysis-quality').wait_for()
            page.locator('[data-analysis-section="statistics"]').click()
            page.locator('[data-testid="data-dashboard-372"]').wait_for()
        step('30 real Chromium quality/statistics switches',True)
        step('overview/quality/statistics never starts feature preview',
             page.evaluate('window.__calls.filter(r=>r.path.endsWith("/feature-lab/workspace/start")).length')==0)
        page.locator('[data-analysis-section="outputs"]').click()
        page.get_by_text('同源成果（测试控制器）').wait_for()
        step('output-only lazy loads without feature controller',
             page.evaluate('window.__renders.feature===0 && window.__renders.output===1'))
        page.locator('[data-analysis-section="features"]').click()
        page.get_by_text('特征构造（测试控制器）').wait_for()
        page.wait_for_function('window.__calls.some(r=>r.path.endsWith("/feature-lab/workspace/start"))')
        step('feature explicitly starts preview exactly once',
             page.evaluate('window.__calls.filter(r=>r.path.endsWith("/feature-lab/workspace/start")).length')==1)
        page.locator('[data-analysis-section="overview"]').click()
        page.locator('#tp-analysis-overview').wait_for()
        step('return to overview does not run duplicate previews',
             page.evaluate('window.__calls.filter(r=>r.path.endsWith("/feature-lab/workspace/start")).length')==1)
        step('Chromium no JS exceptions',not evidence['errors'],evidence['errors'])
        evidence['url']=page.url
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshot),full_page=True)
            evidence['screenshot']=str(screenshot)
        browser.close()
    finally:
      pass
    return evidence


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--chromium',default=shutil.which('chromium') or shutil.which('google-chrome') or '')
    p.add_argument('--out',default='reports/v0380/dom_browser_qa.json')
    p.add_argument('--screenshot',default=None)
    args=p.parse_args()
    out=Path(args.out) if Path(args.out).is_absolute() else ROOT/args.out
    out.parent.mkdir(parents=True,exist_ok=True)
    payload={'mode':'mock_api_real_chromium_dom','version':'0.38.0','utc':datetime.now(timezone.utc).isoformat()}
    if not args.chromium or not Path(args.chromium).is_file():
        payload.update(status='blocked',reason='Existing Chromium/Chrome missing')
        status=2
    else:
        try:
            result=run(args.chromium,args.screenshot)
            payload.update(result,status='passed');status=0
        except Exception as exc:
            payload.update(status='failed',reason=str(exc));status=1
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'path':str(out),'reason':payload.get('reason')},ensure_ascii=False))
    return status

if __name__=='__main__':raise SystemExit(main())
