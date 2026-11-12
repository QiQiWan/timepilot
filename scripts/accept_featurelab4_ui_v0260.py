"""Installed Chromium component test: supervised task binding and Viewer ACK.

This intentionally uses mocked HTTP and a mocked Perspective bridge. It does
not claim real DuckDB, Arrow or JS/WASM analysis.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'


def main():
    checks={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content('<div id="tp-feature-lab"></div>')
        for script in ['feature-builder-ui.js','feature-evaluation-ui.js','feature-shell.js']:
            page.add_script_tag(path=str(STATIC/script))
        page.evaluate('''async () => {
           const fields=[
             ['device','VARCHAR','id','entity_key'],['event_at','TIMESTAMP','datetime','event_time'],
             ['available_at','TIMESTAMP','datetime','available_at'],
             ['prediction_at','TIMESTAMP','datetime','prediction_time'],
             ['label_available','TIMESTAMP','datetime','available_at'],
             ['A1','DOUBLE','numeric','value'],['A2','DOUBLE','numeric','value'],
             ['target','DOUBLE','numeric','label']].map(([name,type,semantic,role],i)=>({
               field_id:'fld_'+name,source_name:name,physical_type:type,
               semantic_type:semantic,role,confirmation:'confirmed',source_column_index:i}));
           window.calls=[];window.records=[];
           window.contract={fields,asset_id:'asset-one',version_id:'base',revision:3,
              state:'confirmed',timezone:'Asia/Shanghai',source_sha256:'a'.repeat(64),
              entity_field_ids:['fld_device'],event_time_field_id:'fld_event_at',
              available_at_field_id:'fld_available_at'};
           window.ctx={toast:()=>{},navigate:()=>{},req:async (url,opts={})=>{
              window.calls.push({url,body:opts.body?JSON.parse(opts.body):null});
              if(url.endsWith('/native-assets'))return [{native_asset_id:'asset-one',
                 asset:{name:'生产设备样例',columns:fields.map(x=>({name:x.source_name}))}}];
              if(url.includes('/contracts?'))return window.contract;
              if(url.includes('/operators?'))return {operators:[{operator_id:'rolling.mean',
                   title:'移动平均',available:true}]};
              if(url.includes('/sets/validate'))return {status:'ready'};
              if(url.endsWith('/sets')&&opts.method==='POST'){
                  const record={set_id:'set-one',asset_id:'asset-one',version_id:'base',
                      name:opts.body?JSON.parse(opts.body).spec.name:'设备特征',revision:1,
                      source_sha256:'a'.repeat(64),spec:JSON.parse(opts.body).spec};
                  window.records.push(record);return record;
              }
              if(url.includes('/sets?'))return window.records;
              if(url.includes('/profiles?')||url.includes('/profile-jobs?'))return {items:[]};
              if(url.includes('/evaluations/plans'))return {plan_id:'eval-one',state:'ready'};
              if(url.endsWith('/agent/plans')&&opts.method==='POST'){
                  return {plan_id:'plan-one',status:'awaiting_viewer',plan:{reason:null},
                    steps:[{status:'awaiting_viewer',tool_id:'feature.viewer.open',result:{
                      render_request_id:'render-one',artifact_id:'artifact-one',set_id:'set-one',
                      source_sha256:'a'.repeat(64)}}]};
              }
              if(url.includes('/render-receipt'))return {status:'succeeded',steps:[],plan:{}};
              return {status:'ok'};
            }};
           window.TimePilotFeaturePerspective={mount:async()=>{},captureReceipt:async()=>({
              viewer_version:'5.6.1',panel_count:2,workspace:{panels:{source:{},result:{}}}}),
              dispose:async()=>{},save:async()=>{}};
           document.addEventListener('click',event=>window.TimePilotFeatureV2UI.click(event));
           document.addEventListener('submit',event=>window.TimePilotFeatureV2UI.submit(event));
           await window.TimePilotFeatureV2UI.render(window.ctx,'project-one','build');
        }''')
        page.locator('#tp-fl2-add-step input[name="name"]').fill('A1_ma5')
        page.locator('#tp-fl2-add-step button[type="submit"]').click()
        page.locator('#tp-fl2-save-set input[name="name"]').fill('设备监督特征')
        page.locator('#tp-fl2-save-set select[name="task_mode"]').select_option('supervised')
        page.locator('#tp-fl2-save-set details.tp-fl2-task-binding').evaluate('node=>node.open=true')
        page.locator('#tp-fl2-save-set select[name="prediction_time"]').select_option('fld_prediction_at')
        page.locator('#tp-fl2-save-set select[name="label"]').select_option('fld_target')
        page.locator('#tp-fl2-save-set select[name="label_available"]').select_option('fld_label_available')
        page.locator('#tp-fl2-save-set button[type="submit"]').click()
        page.wait_for_function('window.records.length === 1')
        spec=page.evaluate('window.records[0].spec')
        assert spec['task']['mode']=='supervised' and spec['source']['version_id']=='base'
        assert spec['task']['label_available_at_field_id']=='fld_label_available'
        checks['supervised_spec_bound_at_creation']=True
        page.evaluate("window.TimePilotFeatureV2UI.render(window.ctx,'project-one','evaluate')")
        page.locator('#tp-fl2-evaluation-form button[type="submit"]').click()
        page.wait_for_function('window.calls.some(x=>x.url.includes("/evaluations/plans"))')
        task=page.evaluate('window.calls.find(x=>x.url.includes("/evaluations/plans")).body.protocol.task')
        assert task==spec['task']
        checks['evaluation_reuses_immutable_supervised_task']=True
        page.evaluate("window.TimePilotFeatureV2UI.render(window.ctx,'project-one','build')")
        page.locator('[data-feature-v2="toggle-assistant"]').click()
        page.locator('#tp-fl2-message').fill('请在 BI 显示此特征')
        page.locator('#tp-fl2-agent-form button[type="submit"]').click()
        page.locator('[data-feature-v2="render-agent-result"]').wait_for()
        page.locator('[data-feature-v2="render-agent-result"]').click()
        page.wait_for_function('window.calls.some(x=>x.url.includes("/render-receipt"))')
        receipt=page.evaluate('window.calls.find(x=>x.url.includes("/render-receipt")).body')
        assert receipt['artifact_id']=='artifact-one' and receipt['panel_count']==2
        checks['viewer_ack_submitted_only_after_bridge_capture']=True
        assert not errors,errors
        checks['javascript_errors']=errors
        browser.close()
    out=ROOT/'reports/featurelab/v0260/chromium_featurelab4_component.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    result={'status':'passed_component','real_perspective':False,
            'real_duckdb':False,'checks':checks}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
