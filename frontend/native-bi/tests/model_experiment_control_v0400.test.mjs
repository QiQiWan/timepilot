import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const source=readFileSync(resolve(import.meta.dirname,'../../../src/timepilot/static/modeling-ui.js'),'utf8');
const taskId='00000000-0000-4000-8000-000000000019';
const runId='00000000-0000-4000-8000-000000000123';
function harness(mode='running'){
  const calls=[];let html='',message='';
  const task={task_id:taskId,revision:2,status:'approved',spec:{
    name:'Equipment regression',asset_id:'factory',version_id:'base',
    task_kind:'tabular_regression',target:{column:'y'},features:[{column:'x'}],
    validation:{kind:'rolling_origin'}}};
  const current=()=>mode==='ready'?{state:'ready_to_run',current:true,run_id:null}:
    {state:mode,current:true,run_id:runId,revision:2,task_kind:'tabular_regression',
     executor:'sklearn_tabular',validation_protocol:'rolling_origin',
     data_version:'base',metric_sets:{},report_available:false,
     experiment:{run_id:runId,budget:{run_wall_seconds_total:900,budget_profile:'15m'},
        remaining_wall_seconds:210,cancel_requested:mode==='cancel_requested',
        attempts:1,lease_epoch:1,state:mode}};
  const document={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(s){html=s;},get innerHTML(){return html;},
      querySelector(selector){return selector==='[name=budget_profile]'?{value:'5m'}:null;},querySelectorAll(){return []}};
    if(id==='tp-modeling-status')return {set textContent(s){message=s;},dataset:{}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    return null;
  }};
  const ctx={req:async(path,opts)=>{
    calls.push({path,opts});
    if(path.includes('/suggestions?'))return {profile:{source_rows:100,sample_rows:100,fields:[{name:'y'},{name:'x'}]},task_designer:{tasks:[]}};
    if(path.endsWith('/model-tasks'))return {items:[task]};
    if(path.endsWith('/model-tasks/'+taskId))return task;
    if(path.endsWith('/runs'))return {items:[]};
    if(path.endsWith('/execution/steps'))return {run_id:runId,items:[{model_id:'linear',fold:1,state:'succeeded',wall_seconds:1.2,cpu_seconds:.9}]};
    if(path.endsWith('/execution/cancel')){mode='cancel_requested';return {run_id:runId,state:'cancel_requested'}};
    if(path.endsWith('/execution')&&opts?.method==='POST'){mode='running';return current();}
    if(path.endsWith('/execution'))return current();
    throw Error('Unexpected fetch '+path);
  }};
  const window={location:{assign(){}}};
  runInNewContext(source,{window,document,console,confirm:()=>true,URLSearchParams,FormData:class {}});
  const click=action=>window.TimePilotModeling.click({preventDefault(){},target:{closest(){return {dataset:{modeling:action,taskId}}}}});
  return {api:window.TimePilotModeling,calls,click,ctx,html:()=>html,message:()=>message};
}

async function resume(h){await h.api.open({ctx:h.ctx,projectId:'plant',assetId:'factory',versionId:'base'});await h.click('resume-spec');}

test('running experiment shows a fixed wall budget and allows cancelling through unified endpoint',async()=>{
  const h=harness();await resume(h);
  assert.match(h.html(),/experiment-budget-panel/);
  assert.match(h.html(),/900 秒/);
  assert.match(h.html(),/取消训练/);
  await h.click('refresh-steps');
  assert.match(h.html(),/linear/);
  assert.match(h.html(),/1.2/);
  await h.click('cancel-execution');
  const request=h.calls.find(x=>x.path.endsWith('/execution/cancel'));
  assert.equal(request.opts.method,'POST');
  assert.deepEqual(JSON.parse(request.opts.body),{expected_revision:2,run_id:runId});
  assert.match(h.message(),/取消请求已提交/);
});

test('budget selector persists 5m choice to approved execution, no raw API escape',async()=>{
  const h=harness('ready');await resume(h);
  assert.match(h.html(),/实验总预算/);
  await h.click('run-task');
  const request=h.calls.find(x=>x.path.endsWith('/execution')&&x.opts?.method==='POST');
  assert.equal(JSON.parse(request.opts.body).budget_profile,'5m');
  assert.equal(JSON.parse(request.opts.body).expected_revision,2);
});
