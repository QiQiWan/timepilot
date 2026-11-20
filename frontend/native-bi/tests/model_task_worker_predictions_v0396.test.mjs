import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const source=readFileSync(resolve(import.meta.dirname,'../../../src/timepilot/static/modeling-ui.js'),'utf8');
const TASK='00000000-0000-4000-8000-000000000019';
const ASSET='00000000-0000-4000-8000-000000000087';
const SHA='b'.repeat(64);
function click(modeling){return {preventDefault(){},target:{closest(){return {dataset:{modeling,taskId:TASK}};}}};}
function harness(){
  let html='',state='succeeded',status='';
  const calls=[],mounted=[];
  const controls={diag_model:'last_value',diag_dimension:'month',diag_value:'2024-04'};
  const task={task_id:TASK,revision:1,status:'approved',effective_status:'approved',
    spec:{asset_id:'source',version_id:'v3',task_kind:'continuous_forecast',name:'Temperature forecast',
      business_goal:'predict before shift',target:{column:'temperature'},features:[{column:'sensor'}],
      validation:{kind:'rolling_origin'}}};
  const diagnostic={status:'available',task_revision:1,selected_model:'last_value',result_sha256:SHA,
      metric_provenance:{metric_scope:'fold_level_validation_prediction'},
      fold_scores:[{mae:0.3,fold:1}],fold_diagnostics:[],feature_contribution:null,
      available_dimensions:['month','device'],slices:[],selection:{dimension:null,value:null}};
  const dom={body:{insertAdjacentHTML(){}},getElementById(id){
      if(id==='tp-modeling-body')return {set innerHTML(s){html=s;},get innerHTML(){return html;},
        querySelector(selector){const key=selector.match(/^\[name=(\w+)\]$/)?.[1];return key?{value:controls[key]}:null;},
        querySelectorAll(){return [];}};
      if(id==='tp-modeling-dialog')return {remove(){}};
      if(id==='tp-modeling-status')return {set textContent(v){status=v;},dataset:{}};
      if(id==='tp-prediction-perspective')return {set textContent(v){mounted.push({loading:v});}};
      return null;
  }};
  const execution=()=>({state,current:state==='succeeded',executor:'temporal_engine',
      task_id:TASK,run_id:'00000000-0000-4000-8000-000000000088',source_sha256:SHA,
      data_version:'v3',revision:1,task_kind:'continuous_forecast',validation_protocol:'rolling_origin',
      metric_sets:{comparison:{last_value:{mae:0.3},lag_ridge:{mae:0.28}}},
      available_models:['last_value','lag_ridge'],
      diagnostics_available:state==='succeeded',report_available:state==='succeeded',
      result_sha256:SHA,evidence_sha256:SHA,
      metric_provenance:{metric_scope:'fold_level_validation_prediction'},
      prediction_asset:state==='succeeded'?{asset_id:ASSET,source_sha256:SHA,manifest_sha256:SHA,
       rows:100,perspective_format:'verified_csv_fallback',perspective_ready:true}:null});
  const ctx={req:async(path,opts)=>{
    calls.push({path,opts});
    if(path.includes('/suggestions?'))return {profile:{source_rows:240,sample_rows:120,fields:[{name:'temperature'}]},task_designer:{tasks:[]}};
    if(path.endsWith('/model-tasks'))return {items:[task]};
    if(path.endsWith('/model-tasks/'+TASK))return task;
    if(path.endsWith('/runs'))return {items:[]};
    if(path.endsWith('/temporal-link'))return {status:'linked',draft_id:'draft1'};
    if(path.endsWith('/drafts/draft1'))return {draft_id:'draft1',name:'Temperature forecast',version_id:'v3',status:'approved',mapping:{target:'temperature'},plan:{task_kind:'continuous_forecast'},preflight:{task_kind:'continuous_forecast',ready:true,feature_count:1,series_count:1,blockers:[],warnings:[]}};
    if(path.includes('/diagnostics?'))return {...diagnostic,selected_model:new URLSearchParams(path.split('?')[1]).get('model')};
    if(path.endsWith('/execution'))return execution();
    throw Error('Unexpected '+path);
  }};
  const window={location:{protocol:'https:',host:'timepilot.example:443',assign(){}},
    TimePilotPerspectiveLoader:{async load(){return {async mountPerspectiveRemote(target,ws,opts){mounted.push({target,ws,opts});}};}}};
  runInNewContext(source,{window,document:dom,console,confirm:()=>true,URLSearchParams,FormData:class{}});
  return {api:window.TimePilotModeling,ctx,calls,html:()=>html,mounted,status:()=>status,
    stale(){state='stale';}};
}
async function ready(h){await h.api.open({ctx:h.ctx,projectId:'00000000-0000-4000-8000-000000000033',assetId:'source',versionId:'v3'});
  await h.api.click(click('resume-spec'));}

test('temporal fold model selector, signed assets, and native Perspective mount use approved scoped IDs',async()=>{
  const h=harness();await ready(h);
  assert.match(h.html(),/model-predictions-asset/, 'status='+h.status()+' calls='+JSON.stringify(h.calls));
  assert.match(h.html(),/验证集逐样本预测/);
  assert.match(h.html(),/last_value/);
  assert.match(h.html(),/lag_ridge/);
  assert.match(h.html(),new RegExp(SHA));
  assert.equal(h.calls.filter(x=>x.path.includes('/diagnostics?')).length,1);
  assert.match(h.calls.find(x=>x.path.includes('/diagnostics?')).path,/model=last_value/);
  await h.api.click(click('open-predictions'));
  const remote=h.mounted.at(-1);
  assert.equal(remote.opts.tableName,'timepilot_active');
  assert.equal(remote.ws,`wss://timepilot.example:443/api/v1/native-bi/model-predictions/00000000-0000-4000-8000-000000000033/${TASK}/${ASSET}/ws?project_id=00000000-0000-4000-8000-000000000033`);
  assert.match(h.status(),/验证预测已加载/);
  assert.equal(typeof remote.opts.onCell,'function');
});

test('source becoming stale hides prediction explorer and verified fold details',async()=>{
  const h=harness();await ready(h);h.stale();await h.api.click(click('refresh-run'));
  assert.match(h.html(),/历史指标不可继续使用/);
  assert.doesNotMatch(h.html(),/model-predictions-asset/);
  assert.doesNotMatch(h.html(),/model-diagnostics-panel/);
  const n=h.mounted.length;
  await h.api.click(click('open-predictions'));
  assert.equal(h.mounted.length,n);
});
