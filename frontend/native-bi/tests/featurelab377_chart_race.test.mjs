import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const source=fs.readFileSync(path.join(root,'src/timepilot/static/data-dashboard-v0372.js'),'utf8');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture({withAbort=false}={}){
 let html='',href='http://localhost/projects/p/analysis?section=statistics';
 const listeners={},pending=[],calls=[];
 const host={isConnected:true,addEventListener(type,fn){listeners[type]=fn;},removeEventListener(type,fn){if(listeners[type]===fn)delete listeners[type];}};
 Object.defineProperty(host,'innerHTML',{get:()=>html,set(v){html=v;}});
 const w={history:{state:null,replaceState(_a,_b,v){href=new URL(v,href).href;}}};
 Object.defineProperty(w,'location',{get:()=>new URL(href)});
 const fields=[{field_id:'f1',source_name:'measure',physical_type:'DOUBLE'},
               {field_id:'f2',source_name:'temperature',physical_type:'DOUBLE'}];
 const sha='a'.repeat(64),scope='b'.repeat(64);
 const ctx={req:async (url,opt)=>{
   const data=JSON.parse(opt.body);calls.push({url,data,signal:opt.signal});
   const base={asset_id:'asset',version_id:'base',source_sha256:sha,contract_revision:1,scope_sha256:scope};
   if(url.endsWith('/dashboard'))return {...base,rows_total:100,rows_filtered:100,
     fields:fields.map((f,i)=>({field_id:f.field_id,name:f.source_name,numeric:true,finite_count:100,
       stddev:2,min:i,max:i+10,p25:i+2,median:i+4,p75:i+6,whisker_low:i+1,whisker_high:i+8})),
     metrics:{records:100,source_records:100,columns:2,numeric_fields:2,missing_percent:0,missing_cells:0,coverage_percent:100,confirmed_time:false},
     selection:{fields_truncated:false},correlations:[],evidence_analysis:{status:'insufficient_evidence',message:'无法判断'},filters:[]};
   if(url.endsWith('/dashboard/chart'))return new Promise(resolve=>pending.push({data,resolve:r=>resolve({...base,kind:data.kind,field_name:data.field_id,rows_filtered:100,points:[],...r})}));
   throw Error(url);
 }};
 vm.runInNewContext(source,{window:w,URL,URLSearchParams,encodeURIComponent,document:{},Math,Number,JSON,Promise,...(withAbort?{AbortController}:{})});
 w.TimePilotDataDashboard372.mount(host,{pid:'p',aid:'asset',vid:'base',source_sha256:sha,contract_revision:1,fields,ctx});
 return {w,listeners,pending,calls,host,ctx,fields,get html(){return html}};
}
test('30 fast chart switches re-query only changed card, old response never wins',async()=>{
 const t=fixture();await sleep(30);
 assert.equal(t.pending.length,2); // max two initial chart requests
 for(let i=0;i<30;i++)t.listeners.change({target:{dataset:{dash:'card-type',index:'0'},value:'box'}});
 await sleep(15);
 assert.equal(t.calls.filter(x=>x.url.endsWith('/dashboard/chart')).length,2);
 assert.equal(t.pending.length,2);
 t.pending[0].resolve();t.pending[1].resolve();await sleep(45);
 const pendingAfter=t.pending.slice(2);
 assert.equal(pendingAfter.length,1,'one last card request, not thirty and no unrelated card');
 assert.equal(pendingAfter[0].data.kind,'box');
 pendingAfter[0].resolve({stats:{min:0,p25:1,p50:2,p75:3,max:5,whisker_low:0,whisker_high:4},points:[{percentile:50,value:2}]});
 await sleep(20);
 assert.match(t.html,/箱线图/);
 assert.equal(t.calls.filter(x=>x.url.endsWith('/dashboard/chart')).length,3);
});
test('default correlation selection excludes unconfirmed sequence column',async()=>{
 const t=fixture();await sleep(30);
 const r=t.calls.find(x=>x.url.endsWith('/dashboard'));
 assert.deepEqual(Array.from(r.data.association_ids),['f1','f2']);
 t.w.TimePilotDataDashboard372.dispose();
});

test('version switch rebinds all dashboard controls on the reused host',async()=>{
 const t=fixture();await sleep(10);
 const first=t.listeners.click;
 assert.equal(typeof first,'function');
 t.w.TimePilotDataDashboard372.mount(t.host,{pid:'p',aid:'asset',vid:'v2',source_sha256:'c'.repeat(64),contract_revision:2,fields:t.fields,ctx:t.ctx});
 assert.notEqual(t.listeners.click,first,'old closure cannot capture stale dashboard on same DOM host');
 assert.equal(typeof t.listeners.change,'function');
 assert.equal(typeof t.listeners.input,'function');
 t.w.TimePilotDataDashboard372.dispose();
});
test('a slow superseded chart epoch does not block refreshed filter requests',async()=>{
 const t=fixture();await sleep(35);
 assert.equal(t.pending.length,2);
 const stale=t.pending.slice();
 t.listeners.click({target:{closest:()=>({dataset:{dashAction:'refresh',index:'0'}})},preventDefault(){}});
 await sleep(35);
 assert.equal(t.pending.length,4,'fresh epoch starts new requests without waiting for stale network responses');
 const fresh=t.pending.slice(2);
 fresh[0].resolve({points:[{lower:0,upper:1,count:9}]});
 fresh[1].resolve({points:[{lower:1,upper:2,count:7}]});
 await sleep(25);
 const html=t.html;
 stale[0].resolve({points:[{lower:0,upper:1,count:9000}]});
 stale[1].resolve({points:[{lower:1,upper:2,count:9000}]});
 await sleep(30);
 assert.equal(t.html,html,'late old network responses cannot overwrite current figures');
 t.w.TimePilotDataDashboard372.dispose();
});

test('superseded requests are actively aborted when AbortController is supported',async()=>{
 const t=fixture({withAbort:true});await sleep(35);
 const initial=t.calls.filter(x=>x.url.endsWith('/dashboard/chart'));
 assert.equal(initial.length,2);
 assert.ok(initial.every(x=>x.signal && !x.signal.aborted));
 t.listeners.click({target:{closest:()=>({dataset:{dashAction:'refresh',index:'0'}})},preventDefault(){}});
 await sleep(35);
 assert.ok(initial.every(x=>x.signal.aborted),'the superseded chart requests must be aborted');
 const next=t.calls.filter(x=>x.url.endsWith('/dashboard/chart')).slice(2);
 assert.equal(next.length,2);
 assert.ok(next.every(x=>x.signal && !x.signal.aborted));
 t.w.TimePilotDataDashboard372.dispose();
 assert.ok(next.every(x=>x.signal.aborted),'unmount must abort active chart requests');
});
