import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const ROOT=resolve(import.meta.dirname,'../../..');
const CODE=readFileSync(resolve(ROOT,'src/timepilot/static/modeling-ui.js'),'utf8');
const TID='00000000-0000-4000-8000-000000000001';
const DID='00000000-0000-4000-8000-000000000002';

function harness(stale=false){
  let markup='',message='';
  const calls=[];
  const doc={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(s){markup=s;},get innerHTML(){return markup;},
      querySelector(){return null},querySelectorAll(){return []}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-status')return {set textContent(s){message=s;},dataset:{}};
    return null;
  }};
  const spec={name:'Factory forecast',asset_id:'asset',version_id:'base',task_kind:'continuous_forecast',
    target:{column:'sale',horizon_steps:3},features:[],business_goal:'Forecast future sales',
    time:{event_time_column:'time',frequency:'D',publication_lag_days:0},
    validation:{kind:'rolling_origin',folds:3}};
  const task={task_id:TID,revision:1,status:'approved',effective_status:'approved',spec};
  const draft={draft_id:DID,asset_id:'asset',version_id:'base',name:'Factory forecast',status:'queued',
    mapping:{time:'time',target:'sale'},plan:{task_kind:'continuous_forecast'},
    preflight:{source_rows:300,selected_rows:300,eligible_rows:280,series_count:1,
      feature_count:0,ready:true,evaluation:{split:'rolling_origin'},blockers:[],warnings:[]},run_id:'run'};
  const designer={role_candidates:[{name:'sale',role:'numeric',suggest_x:true,warnings:[]}],
    tasks:[],suggested_features:[],targets:{preferred_numeric:'sale'}};
  const ctx={req:async(path,opts)=>{
    calls.push(path);
    if(path.includes('/suggestions?'))return {profile:{source_rows:300,sample_rows:100,fields:[{name:'sale'}]},task_designer:designer};
    if(path.endsWith('/model-tasks'))return {items:[task]};
    if(path.endsWith('/model-tasks/'+TID))return task;
    if(path.endsWith('/temporal-link'))return {status:stale?'stale':'linked',draft_id:DID};
    if(path.endsWith('/execution'))return {status:'ok',state:stale?'stale':'succeeded',
      executor:'existing_temporal_pit',validation_protocol:'rolling_origin_pit_v1',
      metric_sets:stale?{}:{test:{MAE:1.23,WAPE:0.11}},
      data_version:'base',current:!stale,evidence_sha256:'a'.repeat(64),
      report_available:!stale};
    if(path.endsWith('/drafts/'+DID))return draft;
    if(path.endsWith('/evidence'))return {status:'succeeded',evidence_level:'independent_temporal_test',
      validation_protocol:'rolling_origin_pit_v1',metrics:{MAE:1.23,WAPE:0.11}};
    throw Error('Unknown route: '+path);
  }};
  runInNewContext(CODE,{window:{location:{assign(){}}},document:doc,
    console,confirm:()=>true,FormData:class {}});
  return {ctx,calls,task,read:()=>({markup,message}),api:globalThis.NO_API||null,
    getApi(){// sandbox window captured below
      return null;
    },window:null};
}

// Rebuild the same minimal UI fixture with a captured sandbox window.
function makeUI(stale=false){
  const s=harness(stale);  // Ensures the real script parses with this stub.
  let html='',status='';const calls=[];
  const win={location:{assign(){}}};
  const doc={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(v){html=v;},get innerHTML(){return html;},querySelector(){return null},querySelectorAll(){return []}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-status')return {set textContent(v){status=v;},dataset:{}};
    return null;
  }};
  runInNewContext(CODE,{window:win,document:doc,console,confirm:()=>true,FormData:class{}});
  return {api:win.TimePilotModeling,ctx:s.ctx,calls:s.calls,html:()=>html,status:()=>status};
}
function action(name,taskId){return {preventDefault(){},target:{closest(){return {dataset:{modeling:name,taskId}}}}};}

test('resume saved temporal task from verified legacy draft, show evidence without prediction rows',async()=>{
  const ui=makeUI();
  await ui.api.open({ctx:ui.ctx,projectId:'project',assetId:'asset',versionId:'base'});
  await ui.api.click(action('resume-spec',TID));
  assert.ok(ui.calls.some(x=>x.endsWith('/temporal-link')));
  assert.ok(ui.calls.some(x=>x.endsWith('/drafts/'+DID)));
  assert.match(ui.html(),/Factory forecast/);
  await ui.api.click(action('refresh-evidence'));
  assert.match(ui.html(),/1\.23/);
  assert.match(ui.html(),/rolling_origin_pit_v1/);
  assert.doesNotMatch(ui.html(),/predictions/);
});

test('stale temporal binding does not resume or display historical metrics',async()=>{
  const ui=makeUI(true);
  await ui.api.open({ctx:ui.ctx,projectId:'project',assetId:'asset',versionId:'base'});
  await ui.api.click(action('resume-spec',TID));
  assert.doesNotMatch(ui.html(),/1\.23/);
  assert.match(ui.status(),/历史建模草稿或源版本发生变化/);
});
