import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const script=fs.readFileSync(path.join(root,'src/timepilot/static/data-dashboard-v0372.js'),'utf8');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function setup({stale=false}={}) {
  const events={};let html='', queries=[],url='http://localhost/projects/p/analysis?asset=aa&version=base&section=statistics';
  const host={isConnected:true,addEventListener:(name,fn)=>{events[name]=fn;}};
  Object.defineProperty(host,'innerHTML',{set:s=>html=s,get:()=>html});
  const fs=[{field_id:'field2',source_name:'measurement',physical_type:'DOUBLE'},
    {field_id:'field3',source_name:'temperature',physical_type:'DOUBLE'},
    {field_id:'field1',source_name:'device',physical_type:'VARCHAR'}];
  const window={};
  Object.defineProperty(window,'location',{get:()=>new URL(url)});
  window.history={state:null,replaceState:(_state,_title,next)=>{url=new URL(next,url).href;}};
  const ctx={req:async (endpoint,opts)=>{
    const body=JSON.parse(opts.body);queries.push({endpoint,body});
    const same={asset_id:'aa',version_id:'base',source_sha256:stale?'different':'source123',contract_revision:3,scope_sha256:JSON.stringify(body.filters)};
    if(endpoint.endsWith('/dashboard'))return {...same,metrics:{records:body.filters.length?4:12,columns:3,numeric_fields:2,missing_percent:0,source_records:12,coverage_percent:100,missing_cells:0,confirmed_time:false},
      fields:fs.map(f=>({...f,name:f.source_name,mean:10,median:10,stddev:2,distinct_count:4,non_null:12,null_rate:0})),correlations:[{x_field_id:'field2',y_field_id:'field3',pearson:0.5,pair_count:12}],filters:body.filters,selection:{fields_truncated:false}};
    if(endpoint.endsWith('/dashboard/chart'))return {...same,kind:body.kind,field_id:body.field_id,field_name:'measurement',rows_filtered:4,points:[{lower:1,upper:2,count:3},{lower:2,upper:3,count:1}],stats:{min:1,max:3,p25:1.5,p50:2,p75:2.5},scope_sha256:JSON.stringify(body.filters)};
    throw new Error(endpoint);
  }};
  vm.runInNewContext(script,{window,document:{},URL,URLSearchParams,Math,Number,JSON,encodeURIComponent});
  const mount=()=>window.TimePilotDataDashboard372.mount(host,{pid:'p',aid:'aa',vid:'base',source_sha256:'source123',contract_revision:3,fields:fs,ctx});
  return {mount,ctx,host,events,window,get html(){return html},get queries(){return queries},get url(){return url}};
}

test('dashboard mounts real API-driven totals and source-pinned four visual cards',async()=>{
 const t=setup();t.mount();await sleep(20);
 assert.match(t.html,/数据一览/);assert.match(t.html,/可配置图表/);
 assert.match(t.html,/字段相关性热力图/);assert.match(t.html,/筛选后记录/);
 assert.match(t.html,/Pearson/);
 assert.ok(t.queries.some(q=>q.endpoint.endsWith('/dashboard')));
 assert.ok(t.queries.some(q=>q.endpoint.endsWith('/dashboard/chart')));
 assert.ok(t.html.includes('source123'));
});

test('new filter is passed to every chart, survives refresh and is URL-pinned',async()=>{
 const t=setup();t.mount();await sleep(15);
 t.events.change({target:{dataset:{dash:'filter-field'},value:'field1'}});
 t.events.input({target:{dataset:{dash:'filter-value'},value:'A'}});
 t.events.click({target:{closest:()=>({dataset:{dashAction:'add-filter'}})},preventDefault(){}});
 await sleep(80);
 assert.match(t.url,/da_filters=/);
 const late=t.queries.filter(q=>q.endpoint.endsWith('/dashboard/chart')&&q.body.filters?.length);
 assert.ok(late.length>=2);
 for(const q of late)assert.equal(q.body.filters[0].value,'A');
 assert.ok(t.queries.some(q=>q.endpoint.endsWith('/dashboard')&&q.body.filters[0]?.value==='A'));
 // Recreating an identical host must not send the old unfiltered query.
 const old=t.queries.length;t.mount();await sleep(5);assert.equal(t.queries.length,old);
});

test('same SHA mismatch is rejected without showing stale statistics',async()=>{
 const t=setup({stale:true});t.mount();await sleep(20);
 assert.match(t.html,/数据或字段契约已改变/);
 assert.doesNotMatch(t.html,/tp-dash-metrics/);
});

test('user can add/remove cards and card selection persists in the URL',async()=>{
 const t=setup();t.mount();await sleep(15);
 const old=t.queries.length;
 t.events.click({target:{closest:()=>({dataset:{dashAction:'add-card'}})},preventDefault(){}});
 await sleep(15);
 assert.match(t.url,/da_cards=/);assert.ok(t.queries.length>old);
 t.events.click({target:{closest:()=>({dataset:{dashAction:'remove-card',index:'0'}})},preventDefault(){}});
 await sleep(15);
 assert.match(t.url,/da_cards=/);
});
