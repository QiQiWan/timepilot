import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const read=name=>fs.readFileSync(path.join(root,'src/timepilot/static',name),'utf8');
const source='a'.repeat(64),scope='b'.repeat(64);
const claim={id:'f'.repeat(64),title:'缺失字段',observation:'A1 缺失 10 / 100 条记录。',
  evidence:[{path:'fields[0].null_count',value:10,unit:'count'}],
  scope:{source_sha256:source,version_id:'base',scope_sha256:scope},limitations:[]};
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function setup({valid=true, tamper=false, evidence=true}={}){
  let html='',page='http://localhost/projects/p/analysis?asset=asset&version=base&section=statistics';
  const events={},queries=[],files=[];
  const host={isConnected:true,addEventListener:(name,fn)=>{events[name]=fn;}};
  Object.defineProperty(host,'innerHTML',{get:()=>html,set:s=>html=s});
  const fields=[{field_id:'A1',name:'A1',source_name:'A1',physical_type:'DOUBLE'}];
  const window={history:{state:null,replaceState:(_s,_t,next)=>page=new URL(next,page).href}};
  Object.defineProperty(window,'location',{get:()=>new URL(page)});
  const doc={createElement:()=>({click(){files.push({clicked:true,href:this.href,filename:this.download});}})};
  const body=()=>({asset_id:'asset',version_id:'base',source_sha256:source,scope_sha256:scope,
    contract_revision:5,rows_total:100,rows_filtered:100,filters:[],
    metrics:{records:100,source_records:100,missing_cells:10,columns:1,numeric_fields:1,
      missing_percent:10,coverage_percent:100,confirmed_time:false},
    fields:[{...fields[0],null_count:10,non_null:90,null_rate:.1,median:2,p90:8}],
    correlations:[],evidence_analysis:evidence?{status:'supported',claims:[claim]}:{status:'insufficient_evidence',message:'无法判断'}});
  const markdown='# 数据一览\n\n真实数值证据';
  const right=crypto.createHash('sha256').update(markdown).digest('hex');
  const req=async (endpoint,options)=>{
    const data=JSON.parse(options.body);queries.push({endpoint,data});
    if(endpoint.endsWith('/dashboard'))return body();
    if(endpoint.endsWith('/dashboard/chart'))return {...body(),kind:data.kind,field_name:'A1',points:[{count:3,lower:0,upper:2}]};
    if(endpoint.endsWith('/dashboard/report'))return {markdown,report_sha256:tamper?'bad':right,
      source_sha256:valid?source:'0'.repeat(64),scope_sha256:scope};
    throw Error(endpoint);
  };
  vm.runInNewContext(read('data-dashboard-v0372.js'),{
    window,document:doc,URL,URLSearchParams,Math,JSON,Number,encodeURIComponent,
    Blob,TextEncoder,Uint8Array,globalThis:{crypto:crypto.webcrypto},crypto:crypto.webcrypto,
    setTimeout,clearTimeout
  });
  window.TimePilotDataDashboard372.mount(host,{pid:'p',aid:'asset',vid:'base',
    source_sha256:source,contract_revision:5,fields,ctx:{req}});
  return {host,events,queries,files,get html(){return html}};
}

test('dashboard only renders server signed numeric claims, never browser heuristics',async()=>{
  const t=setup();await wait(15);
  assert.match(t.html,/A1 缺失 10 \/ 100 条记录/);
  assert.match(t.html,/fields\[0\].null_count/);
  assert.match(t.html,new RegExp(claim.id));
  assert.match(t.html,new RegExp(source));
});
test('insufficient evidence displays cannot determine, hides export button',async()=>{
  const t=setup({evidence:false});await wait(15);
  assert.match(t.html,/无法判断/);
  assert.match(t.html,/export-report" disabled/);
  assert.doesNotMatch(t.html,/A1 缺失 10 \/ 100 条记录/);
});
test('report export reuses scoped server claims and verifies SHA256',async()=>{
  const t=setup();await wait(20);
  t.events.click({target:{closest:()=>({dataset:{dashAction:'export-report'}})},preventDefault(){}});
  await wait(20);
  assert.equal(t.files.length,1);
  assert.equal(t.queries.at(-1).data.expected_sha256,source);
  assert.ok(t.queries.at(-1).endpoint.endsWith('/dashboard/report'));
});
test('wrong SHA256 blocks export and exposes error',async()=>{
  const t=setup({tamper:true});await wait(15);
  t.events.click({target:{closest:()=>({dataset:{dashAction:'export-report'}})},preventDefault(){}});
  await wait(20);
  assert.equal(t.files.length,0);
  assert.match(t.html,/SHA256 核验失败/);
});
test('feature and model UIs consume server comparison evidence',()=>{
  const feature=read('feature-workbench-ui.js');
  assert.match(feature,/original\.evidence_analysis/);
  assert.doesNotMatch(feature,/当前显示 \$\{rows\.length\} 个统计分组/);
  const model=read('analysis-insights-ui.js');
  assert.match(model,/model\.variant_deltas/);
  assert.match(model,/验证表现退化/);
});
