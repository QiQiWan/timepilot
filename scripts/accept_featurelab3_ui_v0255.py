"""Installed Chromium component check: preserve a mounted Viewer DOM island.

No Perspective JS/WASM, network, or fake numerical output is claimed here.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'

def main():
    result={'test':'FeatureLab browser shell component','real_perspective':False,'checks':{}}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':900})
        errors=[]
        page.on('pageerror',lambda ex:errors.append(str(ex)))
        page.set_content('<div id="tp-feature-lab"></div>')
        page.add_script_tag(path=str(STATIC/'feature-builder-ui.js'))
        page.add_script_tag(path=str(STATIC/'feature-shell.js'))
        page.evaluate('''async () => {
          const fields=[{field_id:'fld_time',source_name:'Time',physical_type:'DOUBLE',role:'unspecified'},
                        {field_id:'fld_a1',source_name:'A1',physical_type:'DOUBLE',role:'value'},
                        {field_id:'fld_a2',source_name:'A2',physical_type:'DOUBLE',role:'value'}];
          window.ctx={toast:()=>{},navigate:()=>{},req:async url=>{
            if(url.endsWith('/native-assets'))return [{native_asset_id:'asset-one',asset:{name:'设备样例',columns:fields.map(f=>({name:f.source_name}))}}];
            if(url.includes('/contracts?'))return {fields,source_sha256:'a'.repeat(64),revision:1,state:'detected'};
            if(url.includes('/operators?'))return {operators:[{operator_id:'rolling.mean',title:'移动平均',available:true}]};
            if(url.includes('/sets?'))return [{set_id:'set-one',asset_id:'asset-one',version_id:'base',revision:1,name:'设备特征'}];
            if(url.includes('/profiles?')||url.includes('/profile-jobs?'))return {items:[]};
            return {status:'ok'};
          }};
          window.TimePilotFeaturePerspective={mount:async()=>{
            const container=document.getElementById('tp-fl2-perspective');
            container.innerHTML='<div id="mounted-perspective">Viewer stays connected</div>';
            window.originalMarker=document.getElementById('mounted-perspective');
          },save:async()=>{},dispose:async()=>{}};
          document.addEventListener('click',e=>{window.TimePilotFeatureV2UI.click(e)});
          await window.TimePilotFeatureV2UI.render(window.ctx,'project-one','build');
        }''')
        assert page.locator('#tp-fl2-perspective').count()==1
        result['checks']['builder_has_single_viewer_slot']=True
        page.locator('[data-feature-v2="open-viewer"]').click()
        assert page.locator('#mounted-perspective').count()==1
        result['checks']['opens_one_mock_host']=True
        page.locator('[data-feature-v2="open-set"]').first.click()
        page.locator('[data-feature-v2="toggle-assistant"]').click()
        assert page.evaluate('window.originalMarker.isConnected && document.querySelector("#mounted-perspective")===window.originalMarker')
        result['checks']['viewer_node_not_disconnected_on_forms']=True
        assert page.locator('#tp-fl2-message').count()==1
        result['checks']['assistant_after_rerender']=True
        assert not errors,errors
        result['checks']['page_errors']=errors
        browser.close()
    result['status']='passed_component'
    path=ROOT/'reports/featurelab/v0256/browser_component.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
