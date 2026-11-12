"""Browser smoke for v0.23.3 layout/FAB, using installed Chromium, no download.

This is a *fixture browser test*. It is not the DuckDB/Perspective real-data
acceptance and its report says so explicitly.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'


def main():
    from playwright.sync_api import sync_playwright
    exe=os.environ.get('TIMEPILOT_CHROME_EXECUTABLE')
    if not exe:
        for path in ('/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium','/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'):
            if Path(path).exists():exe=path;break
    if not exe or not Path(exe).exists():
        print('[BLOCKED] No installed Chrome/Chromium binary; no browser downloaded',file=sys.stderr)
        return 2
    sizes=[(1440,900),(1366,768),(1920,1080),(760,900),(390,844)]
    results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=exe,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            for width,height in sizes:
                page=browser.new_page(viewport={'width':width,'height':height})
                page.set_content('''<!doctype html><html><head><meta charset="utf-8"></head><body>
                  <div class="shell"><aside class="sidebar">导航</aside>
                  <div class="main-column"><header class="topbar">TimePilot</header>
                  <main class="content" id="app"><div class="page-heading"><h1>数据分析</h1></div>
                  <div class="tp-native-tabs">分析 / 编辑</div>
                  <div id="native-explore" class="panel"><div class="bi-card-title">实时画布</div>
                  <div class="tp-native-tools">工具栏</div>
                  <div class="tp-native-perspective" id="tp-native-perspective"><div id="placeholder">样本视图</div></div>
                  </div></main></div></div><div id="floating-task" class="floating-task is-hidden"></div></body></html>''')
                for name in ('styles.css','experience.css','native-bi.css','bi-workspace-layout.css','bi-assistant-fab.css'):
                    page.add_style_tag(path=str(STATIC/name))
                for name in ('bi-workspace-layout.js','bi-assistant-fab.js'):
                    page.add_script_tag(path=str(STATIC/name))
                data=page.evaluate('''async () => {
                  document.body.dataset.workspaceMode='native-bi';
                  window.TimePilotBIWorkspaceLayout.mount(document.getElementById('app'));
                  await window.TimePilotBIFloating.mount({projectId:'example',req:async ()=>({mode:'closed',width:440})},
                    {asset_id:'asset'});
                  const panel=document.getElementById('tp-native-perspective');
                  const start=panel.getBoundingClientRect();
                  document.querySelector('[data-fab-toggle]').click();
                  const dialog=document.querySelector('.tp-fab-dialog');
                  const open=!dialog.hidden;
                  document.querySelector('[data-fab-action="minimize"]').click();
                  const min=dialog.hidden;
                  document.querySelector('[data-fab-toggle]').click();
                  const restored=!dialog.hidden;
                  const result={panelHeight:start.height,panelWidth:start.width,windowHeight:innerHeight,
                    open,min,restored,canvasCount:document.querySelectorAll('#tp-bi-workspace-root').length,
                    assistantCount:document.querySelectorAll('.tp-bi-fab-shell').length,
                    overflow:document.documentElement.scrollWidth>innerWidth+2};
                  window.TimePilotBIFloating.leave();
                  for(let i=0;i<20;i++){
                    await window.TimePilotBIFloating.mount({projectId:'example',req:async ()=>({mode:'closed',width:440})},{asset_id:'asset'});
                    if(document.querySelectorAll('.tp-bi-fab-shell').length!==1) throw Error('Duplicate mount on cycle '+i);
                    window.TimePilotBIFloating.leave();
                  }
                  return {...result,remaining:document.querySelectorAll('.tp-bi-fab-shell').length,mountCycles:20};
                }''')
                assert data['open'] and data['min'] and data['restored'],data
                assert data['canvasCount']==1 and data['assistantCount']==1 and data['remaining']==0,data
                assert not data['overflow'],data
                if width==1440:assert data['panelHeight']>=620,data
                results.append({'size':f'{width}x{height}',**data})
                page.close()
            # Synthetic matrix renderer verification in installed Chromium.
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.set_content('<!doctype html><html><body><main id="plot"></main></body></html>')
            page.add_script_tag(path=str(STATIC/'correlation-heatmap.js'))
            heatmap=page.evaluate('''() => {
              const matrix=[{field_x:'A1',field_y:'A1',coefficient:1,n_pair:4,status:'small_sample'},
                {field_x:'A1',field_y:'A2',coefficient:-.6,n_pair:4,status:'small_sample'},
                {field_x:'A2',field_y:'A1',coefficient:-.6,n_pair:4,status:'small_sample'},
                {field_x:'A2',field_y:'A2',coefficient:1,n_pair:5,status:'small_sample'}];
              const rendered=window.TimePilotCorrelationHeatmap.render(document.getElementById('plot'),{field_ids:['A1','A2'],matrix});
              return {...rendered,cellLabels:[...document.querySelectorAll('.tp-heatmap-cell')].map(x=>x.textContent),
                tooltip:document.querySelector('.tp-heatmap-cell').title,
                exportPresent:!![...document.querySelectorAll('button')].find(x=>x.textContent.includes('CSV'))};
            }''')
            assert heatmap['rendered'] and heatmap['cells']==4,heatmap
            assert '-0.60' in heatmap['cellLabels'] and heatmap['exportPresent'],heatmap
            assert '有效样本' in heatmap['tooltip'],heatmap
            results.append({'heatmap_fixture':heatmap})
            page.close()
        finally:browser.close()
    report={'mode':'fixture_chromium','real_data_e2e':False,'perspective_built':False,
            'binary':exe,'cases':results,'status':'passed'}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
