import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const code=fs.readFileSync(path.resolve(here,'../../../src/timepilot/static/data-analysis-ui.js'),'utf8');

function setup({source='sha-v1',ready=true}={}) {
  let html='', changes=null, navigate=null, query='', posts=0;
  const host={isConnected:true,addEventListener:(name,fn)=>{if(name==='change')changes=fn;}};
  Object.defineProperty(host,'innerHTML',{get:()=>html,set:v=>{html=v;}});
  const state={source,ready};
  const fields=[{field_id:'field-1',source_name:'Pressure',physical_type:'DOUBLE',null_rate:0},
                {field_id:'field-2',source_name:'Device',physical_type:'VARCHAR',null_rate:0}];
  const ctx={req:async (url,opts)=>{
    if(url.endsWith('/native-assets'))return [{native_asset_id:'asset-1',asset:{name:'line.csv',columns:[{name:'Pressure'}]}}];
    if(url.endsWith('/versions'))return {versions:[{version_id:'ver-2'}]};
    if(url.includes('/data-analysis/start')){posts++;return {status:'queued'};}
    if(url.includes('/data-analysis/chart')){query=url;return {status:'ready',field_id:url.match(/field_id=([^&]+)/)?.[1],mode:url.match(/mode=([^&]+)/)?.[1],source_sha256:state.source,contract_revision:1,points:[{label:'10',count:2},{label:'20',count:3}]};}
    if(url.includes('/data-analysis'))return {status:state.ready?'ready':'not_started',source_sha256:state.source,contract_revision:1,row_count:5,column_count:2,profile:{rows_scanned:5,fields},findings:[],recommendations:[{code:'basic_features',title:'生成特征',reason:'确认'}]};
    throw Error('unexpected '+url);
  },navigate:s=>{navigate=s;}};
  const window={history:{replaceState:(_a,_b,url)=>{window._url=url;}},location:{href:'http://localhost/projects/p/analysis',pathname:'/projects/p/analysis'}};
  const location={search:'?asset=asset-1&version=base&field=field-1',pathname:'/projects/p/analysis'};
  const sandbox={window,document:{getElementById:()=>host},location,URL,URLSearchParams,encodeURIComponent,Number,Math,clearTimeout,setTimeout:()=>0};
  vm.runInNewContext(code,sandbox);
  return {ui:window.TimePilotDataAnalysisUI,host,ctx,get html(){return html},get navigate(){return navigate},get query(){return query},get changes(){return changes},get posts(){return posts},state};
}

test('EDA renders source-pinned chart and recommender without model',async()=>{
 const t=setup();await t.ui.render(t.ctx,'p');
 assert.match(t.html,/数据概况/); assert.match(t.html,/字段统计与可视化/);
 assert.match(t.html,/生成特征/);assert.match(t.html,/Pressure/);
 assert.match(t.query,/field_id=field-1/);assert.match(t.html,/sha-v1/);
 t.ui.dispose();
});

test('data version switch navigates without carrying stale feature field',async()=>{
 const t=setup();await t.ui.render(t.ctx,'p');
 await t.changes({target:{dataset:{da:'version'},value:'ver-2'}});
 assert.match(t.navigate,/version=ver-2/);
 assert.doesNotMatch(t.navigate,/field=/);
 t.ui.dispose();
});

test('categorical fields switch to category chart and URL restores selector',async()=>{
 const t=setup();await t.ui.render(t.ctx,'p');
 await t.changes({target:{dataset:{da:'field'},value:'field-2'}});
 assert.match(t.query,/mode=category/);
 assert.match(t.html,/Device/);
 t.ui.dispose();
});

test('same source chart mismatch is refused by page',async()=>{
 const t=setup({source:'sha-v1'});
 const original=t.ctx.req;
 t.ctx.req=async (url,opts)=>{
    const r=await original(url,opts);
    if(url.includes('/data-analysis/chart'))return {...r,source_sha256:'tampered'};
    return r;
 };
 await t.ui.render(t.ctx,'p');
 assert.match(t.html,/图表来源与当前分析不一致/);
 t.ui.dispose();
});
