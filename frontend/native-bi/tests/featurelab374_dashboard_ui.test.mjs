import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const source=fs.readFileSync(path.join(root,'src/timepilot/static/data-dashboard-v0372.js'),'utf8');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function fixture(){
  let html='',url='http://localhost/projects/p/analysis?asset=asset&version=base&section=statistics';
  const listeners={},calls=[];
  const host={isConnected:true,addEventListener:(type,cb)=>{listeners[type]=cb;}};
  Object.defineProperty(host,'innerHTML',{set:x=>html=x,get:()=>html});
  const fields=[{field_id:'f1',source_name:'A1',physical_type:'DOUBLE'},
    {field_id:'f2',source_name:'A2',physical_type:'DOUBLE'},
    {field_id:'f3',source_name:'Time',physical_type:'INTEGER'}];
  const windows={history:{state:null,replaceState:(_,__,next)=>{url=new URL(next,url).href}}};
  Object.defineProperty(windows,'location',{get:()=>new URL(url)});
  const scope='b'.repeat(64),sha='a'.repeat(64);
  const ctx={req:async(endpoint,options)=>{
    const data=JSON.parse(options.body);calls.push({endpoint,data});
    const base={asset_id:'asset',version_id:'base',source_sha256:sha,contract_revision:2,scope_sha256:scope};
    if(endpoint.endsWith('/dashboard'))return {...base,rows_total:100,rows_filtered:100,
      metrics:{records:100,source_records:100,columns:3,numeric_fields:3,missing_percent:0,
        missing_cells:0,coverage_percent:100,confirmed_time:false},
      filters:data.filters,evidence_analysis:{status:'insufficient_evidence',message:'无法判断'},
      fields:fields.map((f,i)=>({...f,field_id:f.field_id,name:f.source_name,numeric:true,
        finite_count:100,mean:10+i,stddev:2,min:i,max:i+8,p25:i+2,median:i+4,p75:i+6,
        non_null:100,null_count:0})),
      correlations:(data.association_ids||[]).flatMap((x,i,a)=>a.slice(i+1).map(y=>({x_field_id:x,y_field_id:y,pearson:.75,pair_count:100}))),
      selection:{fields_truncated:false}};
    if(endpoint.endsWith('/dashboard/chart'))return {...base,field_name:data.field_id,kind:data.kind,
      points:data.kind==='scatter'?[{x:2,y:4}]:[{lower:0,upper:1,count:50}],rows_filtered:100};
    throw Error(endpoint);
  }};
  vm.runInNewContext(source,{window:windows,URL,URLSearchParams,Math,Number,JSON,encodeURIComponent,document:{}});
  windows.TimePilotDataDashboard372.mount(host,{pid:'p',aid:'asset',vid:'base',source_sha256:sha,
    contract_revision:2,fields,ctx});
  return {listeners,calls,host,windows,get html(){return html},get url(){return url}};
}
test('one full-size grouped box figure and aligned symmetric Pearson matrix',async()=>{
 const t=fixture();await pause(70);
 assert.match(t.html,/多字段箱线图/);assert.match(t.html,/aria-label="多字段箱线图"/);
 assert.match(t.html,/字段相关性热力图/);
 assert.match(t.html,/data-matrix-diagonal="true">1<\/td>/);
 assert.match(t.html,/A1 × A2/);
 assert.match(t.html,/每行独立/);
 const overview=t.calls.find(c=>c.endpoint.endsWith('/dashboard'));
 assert.deepEqual(Array.from(overview.data.association_ids),['f1','f2','f3']);
});
test('switch a chart triggers only one new chart request, preserves heatmap and URL',async()=>{
 const t=fixture();await pause(70);
 const before=t.calls.filter(c=>c.endpoint.endsWith('/dashboard/chart')).length;
 t.listeners.change({target:{dataset:{dash:'card-type',index:'0'},value:'box'}});
 await pause(50);
 const next=t.calls.filter(c=>c.endpoint.endsWith('/dashboard/chart')).slice(before);
 assert.equal(next.length,1);assert.equal(next[0].data.kind,'box');
 assert.match(t.url,/da_cards=/);
 assert.match(t.html,/字段相关性热力图/);
});
test('select correlation fields triggers scoped full-data recompute without changing old source hash',async()=>{
 const t=fixture();await pause(60);
 t.listeners.change({target:{dataset:{dash:'corr-field'},value:'f3',checked:false}});
 await pause(70);
 const r=t.calls.filter(c=>c.endpoint.endsWith('/dashboard')).at(-1);
 assert.deepEqual(Array.from(r.data.association_ids),['f1','f2']);
 assert.match(t.url,/da_corr=/);
 assert.equal(r.data.expected_sha256,'a'.repeat(64));
});
