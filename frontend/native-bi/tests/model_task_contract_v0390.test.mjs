import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const ROOT=resolve(import.meta.dirname,'../../..');
const MODEL=readFileSync(resolve(ROOT,'src/timepilot/static/modeling-ui.js'),'utf8');
const FEATURE=readFileSync(resolve(ROOT,'src/timepilot/static/feature-shell.js'),'utf8');
const profile={source_rows:100,sample_rows:100,fields:[
  {name:'sale'},{name:'price'},{name:'timestamp'},{name:'group'}],
  recommendation:{time:'timestamp',entity:'group',frequency:{frequency:'D'}}};
const designer={role_candidates:[
  {name:'sale',role:'numeric',suggest_x:true,warnings:[]},
  {name:'price',role:'numeric',suggest_x:true,warnings:[]},
  {name:'timestamp',role:'time',suggest_x:false,warnings:[]},
  {name:'group',role:'entity',suggest_x:false,warnings:[]}],
  suggested_features:['price'],targets:{preferred_numeric:'sale'},
  tasks:[{id:'tabular_regression',route:'featurelab',title:'Reg',subtitle:'',explanation:'',runnable:true}]};

function harness({ready=true}={}) {
  let markup='',message='',redirect=null;
  const calls=[];
  const form={id:'tp-modeling-general-form',querySelector(q){
      const name=q.match(/\[name=([^\]]+)/)?.[1];
      const values={target:'sale',split_kind:'kfold',group_column:'group',time_column:'timestamp'};
      if(q.endsWith(':checked'))return null;
      return name?{value:values[name]}:null;
    },querySelectorAll(q){return q==='[name=observed_features]:checked'?[{value:'price'}]:[];}};
  const doc={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(v){markup=v;},get innerHTML(){return markup;},
      querySelector(q){return q==='form'?form:null;},querySelectorAll(){return []}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-status')return {set textContent(v){message=v;},dataset:{}};
    return null;
  }};
  const window={location:{assign(url){redirect=url;}}};
  const callsResult={task_id:'00000000-0000-4000-8000-000000000001',revision:1,
    spec:{asset_id:'asset',version_id:'base'},status:'review_required'};
  const ctx={req:async(path,opts)=>{
    calls.push({path,opts});
    if(path.includes('/suggestions?'))return {profile,task_designer:designer};
    if(path.endsWith('/model-tasks'))return opts?.method==='POST'?callsResult:{items:[]};
    if(path.endsWith('/preflight'))return {ready,blockers:ready?[]:['Unsafe column']};
    if(path.endsWith('/approve'))return {...callsResult,status:'approved',effective_status:'approved'};
    if(path.endsWith('/execution')&&opts?.method==='POST')return {run_id:'00000000-0000-4000-8000-000000000002',state:'queued',current:true,metric_sets:{}};
    if(path.endsWith('/execution'))return {
      run_id:'00000000-0000-4000-8000-000000000002',state:'succeeded',current:true,
      executor:'sklearn_tabular',validation_protocol:'kfold',data_version:'base',
      source_sha256:'a'.repeat(64), evidence_sha256:'b'.repeat(64),report_available:true,
      metric_sets:{comparison:{baseline:{mae:4.2},linear:{mae:1.2},forest:{mae:0.9}}}};
    throw Error('Unexpected route '+path);
  }};
  class FormDataMock {getAll(){return ['price'];}}
  runInNewContext(MODEL,{window,document:doc,console,FormData:FormDataMock,
    confirm:()=>true});
  return {api:window.TimePilotModeling,ctx,calls,form,redirect:()=>redirect,
    html:()=>markup,status:()=>message};
}
async function exercise(ready){
  const h=harness({ready});
  await h.api.open({ctx:h.ctx,projectId:'project',assetId:'asset',versionId:'base'});
  await h.api.click({preventDefault(){},target:{closest(){return {dataset:{modeling:'choose-task',mode:'tabular_regression'}}}}});
  await h.api.submit({target:h.form,preventDefault(){}});
  return h;
}

test('tabular task is persisted and preflighted before optional navigation',async()=>{
  const h=await exercise(true);
  const create=h.calls.find(c=>c.path.endsWith('/model-tasks')&&c.opts?.method==='POST');
  assert.ok(create);
  const spec=JSON.parse(create.opts.body);
  assert.equal(spec.task_kind,'tabular_regression');
  assert.equal(spec.target.column,'sale');
  assert.deepEqual(Array.from(spec.features,x=>x.column),['price']);
  assert.equal(spec.validation.kind,'kfold');
  assert.equal(spec.asset_id,'asset');
  assert.equal(h.redirect(),null);
  assert.match(h.html(),/model-run-workbench/);
  await h.api.click({preventDefault(){},target:{closest(){return {dataset:{modeling:'continue-featurelab'}}}}});
  assert.match(h.redirect(),/tp_model_task_id=/);
  assert.doesNotMatch(h.redirect(),/tp_features=|tp_target=/);
});

test('blocked task is saved but does not navigate to FeatureLab',async()=>{
  const h=await exercise(false);
  assert.equal(h.redirect(),null);
  assert.match(h.status(),/Unsafe column/);
});

test('FeatureLab refuses user-supplied Y/X URL when model task identifier is present',()=>{
  assert.match(FEATURE,/tp_model_task_id/);
  assert.ok(FEATURE.includes("model-tasks/'+u(s.modelTaskId)"));
  assert.match(FEATURE,/source_state!==['"]current['"]/);
  assert.match(FEATURE,/spec\.asset_id!==asset\|\|spec\.version_id!==version/);
  assert.match(FEATURE,/s\.autoGoal='';s\.autoObjective='auto';s\.autoTargetHint=''/);
  assert.match(FEATURE,/bound\.status!==['"]approved['"]/);
});

test('approved general task queues durable model run and displays real-fold metric rows',async()=>{
  const h=await exercise(true);
  const action=name=>({preventDefault(){},target:{closest(){return {dataset:{modeling:name}}}}});
  await h.api.click(action('approve-spec'));
  assert.match(h.html(),/开始模型对照/);
  await h.api.click(action('run-task'));
  assert.match(h.html(),/刷新运行结果/);
  assert.ok(h.calls.some(x=>x.path.endsWith('/execution')&&x.opts?.method==='POST'));
  await h.api.click(action('refresh-run'));
  assert.match(h.html(),/同折模型对照/);
  assert.match(h.html(),/0\.9/);
  assert.match(h.html(),/回顾性指标不代表生产收益/);
  assert.equal(h.redirect(),null);
});
