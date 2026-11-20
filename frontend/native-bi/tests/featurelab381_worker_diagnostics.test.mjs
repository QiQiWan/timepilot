import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const src=fs.readFileSync(new URL('../../../src/timepilot/static/system-logs-ui.js',import.meta.url),'utf8');
function page(){
  const elements={};
  const host={innerHTML:'',isConnected:true,__tpLogClick:null,
    addEventListener(_t,fn){this.listener=fn;}, removeEventListener(){},
    querySelector(id){return elements[id]??(elements[id]={innerHTML:'',value:''});}};
  const calls=[];
  const req=async (path)=>{
    calls.push(path);
    if(path.startsWith('/system/logs'))return {items:[],matched:0};
    if(path==='/system/workers')return {deployment:'external',online_count:3,required_count:5,
      roles:{forecast:{online:true,status:'idle',claimed_cycles:4,error_cycles:0,p95_ms:21},
       agent:{online:true,status:'busy',claimed_cycles:2,error_cycles:1,p95_ms:312},
       upload:{online:false,status:'offline',claimed_cycles:1,error_cycles:1,p95_ms:null},
       publication:{online:false,status:'offline'},feature:{online:true,status:'memory_paused'}},
      queues:{feature_jobs:{available:true,counts:{queued:5,running:1}}}};
    throw Error('unexpected path '+path);
  };
  const window={};vm.runInNewContext(src,{window,document:{},URLSearchParams,Number,Object,Promise});
  return {api:window.TimePilotSystemLogs,host,elements,calls,ctx:{root:host,req}};
}
test('system logs provide compact worker health, queue counts and honest p95 units', async()=>{
  const a=page();await a.api.render(a.ctx);
  assert.ok(a.calls.includes('/system/workers'));
  const output=a.elements['#tp-worker-rows'].innerHTML;
  assert.match(output,/在线 3\/5/);assert.match(output,/内存预算暂停/);
  assert.match(output,/特征计算：queued 5 · running 1/);
  assert.match(output,/312 ms/);
  assert.match(output,/轮次耗时不代表任务完成时间/);
  assert.doesNotMatch(output,/undefined/);
});
test('refresh independently retries both worker status and log endpoints', async()=>{
  const a=page();await a.api.render(a.ctx);
  await a.host.listener({target:{closest:()=>({dataset:{log:'refresh'}})}});
  assert.equal(a.calls.filter(p=>p==='/system/workers').length,2);
  assert.equal(a.calls.filter(p=>p.startsWith('/system/logs')).length,2);
});
