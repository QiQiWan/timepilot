"""Real Chromium UI interaction for v0.23.1, using an isolated in-page HTTP stub.

The service-side DuckDB/Arrow integration is tested by accept_conversational_bi_v0231.py.
This DOM test does not pretend to validate real DuckDB, Perspective or networking.
Uses an existing system Chromium; never downloads browsers or stores screenshots.
"""
from __future__ import annotations
import json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from system_chrome import chrome_launch_options

JS='''
window.tpCalls=[];
const base={asset_id:'asset-test',version_id:'base',query:{columns:[],filters:[],group_by:[],metrics:[],order_by:[],limit:300,offset:0},
  chart:{type:'grid',x:null,y:[],color:null},dashboard:[],expressions:[],window:null,bucket:null,join:null,industrial:null};
let work={workspace_id:'abc-0001',asset_id:'asset-test',version_id:'base',revision:1,state:structuredClone(base)};
let views=[];
window.TimePilotNativeBI={renderAgentResult:async()=>{window.tpRendered=(window.tpRendered||0)+1},
  saveAgentPerspective:async()=>({panels:[{plugin:'Y Line'}]}),restoreAgentPerspective:async()=>{window.tpRestored=(window.tpRestored||0)+1}};
window.tpContext={projectId:'test-project',toast:message=>window.tpCalls.push({toast:message}),
 req:async(url,opts)=>{
   window.tpCalls.push({url,method:opts?.method||'GET'});
   const get=()=>structuredClone(work);
   if(/\\/workspaces\\?asset_id=/.test(url))return [];
   if(url.endsWith('/workspaces')&&opts){return get()}
   if(url.endsWith('/workspaces/abc-0001'))return get();
   if(url.includes('/views?asset_id='))return views;
   if(url.endsWith('/turns')&&!opts)return [];
   if(url.endsWith('/turns')&&opts){
     const body=JSON.parse(opts.body);
     const actions=[{tool:'bi.filter.set',arguments:{column:'A2',op:'gt',value:0}},
      {tool:'bi.sort.set',arguments:{field:'Time',descending:false}},
      {tool:'bi.chart.create',arguments:{type:'line',x:'Time',y:['A1','A5']}}];
     if(!body.execute)return {actions,executed:false,message:'工具计划已生成'};
     work.revision++;
     work.state.query.filters=[actions[0].arguments];
     work.state.chart=actions[2].arguments;work.state.dashboard=[actions[2].arguments];
     return {actions,planner:'规则',executed:true,workspace:get(),
       result:{columns:['Time','A1','A5'],rows:[{Time:1,A1:0.1,A5:0.5}],returned:1}};
   }
   if(url.endsWith('/views')&&opts){const doc={view_id:'view-1',name:JSON.parse(opts.body).name,version_id:'base'};views=[doc];return doc}
   if(url.endsWith('/undo')||url.endsWith('/redo')){
      work.revision++;
      work.state.chart.type=url.endsWith('/undo')?'grid':'line';
      return {workspace:get(),result:{rows:[{Time:1,A1:0.1}],returned:1}};
   }
   if(url.endsWith('/views/view-1'))return {...views[0],state:work.state};
   if(url.endsWith('/open-view')){work.revision++;return {workspace:get(),result:{rows:[{Time:1,A1:0.1}],returned:1},perspective:{panels:[{plugin:'Y Line'}]}}}
   throw new Error('Unexpected request: '+url);
 }};
'''


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print('BLOCKED: playwright is not installed: '+str(exc));return 2
    started=time.monotonic()
    try:
        with sync_playwright() as play:
            options=chrome_launch_options()
            if sys.platform.startswith('linux'):
                options['args']=['--no-sandbox','--disable-gpu']
            browser=play.chromium.launch(**options)
            page=browser.new_page(viewport={'width':1360,'height':900})
            page.set_content('<main style="width:100%;padding:20px"><section id="tp-bi-agent-panel"></section></main>')
            page.add_script_tag(content=JS)
            page.add_script_tag(path=str(ROOT/'src/timepilot/static/bi-conversation.js'))
            page.evaluate("""()=>{document.addEventListener('click',e=>window.TimePilotBIConversations.click(e));document.addEventListener('submit',e=>window.TimePilotBIConversations.submit(e));}""")
            page.evaluate('''async()=>await window.TimePilotBIConversations.mount(window.tpContext,
                {asset_id:'asset-test',name:'源字段表',row_count:506,
                 columns:[{name:'Time'},{name:'A1'},{name:'A2'},{name:'A5'}]},'base')''')
            assert page.get_by_text('对话式 BI 分析').count()==1
            text=page.locator('#tp-bi-agent-form textarea')
            text.fill('筛选 A2 > 0，按 Time 升序排序，绘制 A1、A5 折线图')
            page.get_by_role('button',name='先预览计划').click()
            page.get_by_text('工具计划已生成').wait_for(timeout=8000)
            text=page.locator('#tp-bi-agent-form textarea')
            text.fill('筛选 A2 > 0，按 Time 升序排序，绘制 A1、A5 折线图')
            page.get_by_role('button',name='执行分析').click()
            page.get_by_text('已执行：1 条预览记录').wait_for(timeout=8000)
            assert page.locator('.tp-bi-agent-tag').count()==1
            page.get_by_role('button',name='撤销',exact=True).click()
            page.get_by_text('修订 3').first.wait_for(timeout=8000)
            page.get_by_role('button',name='重做',exact=True).click()
            page.get_by_text('修订 4').first.wait_for(timeout=8000)
            page.on('dialog',lambda dialog: dialog.accept('工业温度趋势'))
            page.get_by_role('button',name='保存看板').click()
            page.get_by_role('button',name='工业温度趋势').wait_for(timeout=8000)
            page.get_by_role('button',name='工业温度趋势').click()
            page.wait_for_function('window.tpRestored >= 1',timeout=8000)
            assert page.evaluate('window.tpRendered')>=3
            calls=page.evaluate('window.tpCalls')
            assert any('/turns' in r.get('url','') for r in calls)
            assert any('/open-view' in r.get('url','') for r in calls)
            browser.close()
        print(json.dumps({'status':'passed','browser':str(options['executable_path']),'tests':['preview','chat',
            'history-undo-redo','save','restore','perspective-adapter-called'],
            'elapsed_seconds':round(time.monotonic()-started,2)},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error':f'{type(exc).__name__}: {exc}'[:300]},ensure_ascii=False))
        return 1
if __name__=='__main__':raise SystemExit(main())
