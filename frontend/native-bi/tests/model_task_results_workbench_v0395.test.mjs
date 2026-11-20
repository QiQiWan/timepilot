import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const source=readFileSync(resolve(import.meta.dirname,'../../../src/timepilot/static/modeling-ui.js'),'utf8');
const ID='00000000-0000-4000-8000-000000000019';
const SHA='c'.repeat(64);
function click(modeling,taskId=ID){return {preventDefault(){},target:{closest(){return {dataset:{modeling,taskId}}}}};}
function harness(){
  const calls=[];let html='',status='',stale=false,failed=false;
  let controls={diag_model:'forest',diag_dimension:'month',diag_value:'2024-03'};
  const task={task_id:ID,revision:4,status:'approved',effective_status:'approved',
    spec:{asset_id:'source',version_id:'v3',task_kind:'tabular_classification',name:'Defect early warning',
      business_goal:'scrap avoidance',target:{column:'defect'},features:[{column:'sensor'}],validation:{kind:'rolling_origin'}}};
  const diag={status:'available',task_revision:4,selected_model:'forest',best_observed_model:'forest',
    result_sha256:SHA,metric_provenance:{metric_scope:'unweighted_mean_of_validation_folds'},
    selection:{dimension:'month',value:'2024-03'},
    fold_scores:[{average_precision:0.8,recall:0.7},{average_precision:0.81,recall:0.76}],
    fold_diagnostics:[{kind:'confusion_matrix',fold:1,tn:30,fp:3,fn:4,tp:9,evaluated_rows:46},
      {kind:'confusion_matrix',fold:2,tn:28,fp:5,fn:2,tp:11,evaluated_rows:46}],
    feature_contribution:{status:'validation_only_permutation',primary_metric:'average_precision',items:[{feature:'sensor',delta_metric:0.2}]},
    slices:[{value:'2024-03',validation_rows:46,metrics:{recall:0.78}}]};
  const dom={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(s){html=s;},get innerHTML(){return html;},
      querySelector(selector){const k=selector.match(/^\[name=(\w+)\]$/)?.[1];return k?{value:controls[k]}:null;},
      querySelectorAll(){return [];}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-status')return {set textContent(v){status=v;},dataset:{}};
    return null;
  }};
  const ctx={req:async(path,opts)=>{
    calls.push({path,opts});
    if(path.includes('/suggestions?'))return {profile:{source_rows:240,sample_rows:120,fields:[{name:'defect'}]},task_designer:{tasks:[]}};
    if(path.endsWith('/model-tasks'))return {items:[task]};
    if(path.endsWith('/model-tasks/'+ID))return task;
    if(path.endsWith('/runs'))return {items:[]};
    if(path.includes('/execution/diagnostics?'))return {...diag,feature_contribution:new URL('http://x/?'+path.split('?')[1]).searchParams.get('model')==='forest'?diag.feature_contribution:null,selected_model:new URL('http://x/?'+path.split('?')[1]).searchParams.get('model'),
      selection:{dimension:new URL('http://x/?'+path.split('?')[1]).searchParams.get('dimension'),
                 value:new URL('http://x/?'+path.split('?')[1]).searchParams.get('value')}};
    if(path.endsWith('/execution'))return stale
      ?{state:'stale',current:false,executor:'sklearn_tabular',source_sha256:SHA,task_kind:'tabular_classification',
          data_version:'v3',revision:4,metric_sets:{comparison:{forest:{recall:0.7}}},
          diagnostics_available:false,report_available:false,evidence_sha256:SHA}
      :failed?{state:'failed',current:true,executor:'sklearn_tabular',data_version:'v3',revision:4,
          failure_code:'ModelTaskError',failure_reason:'PIT violation in sensor',metric_sets:{},evidence_sha256:SHA}
      :{state:'succeeded',current:true,executor:'sklearn_tabular',task_kind:'tabular_classification',
          validation_protocol:'rolling_origin',data_version:'v3',revision:4,result_sha256:SHA,
          metric_provenance:{metric_scope:'unweighted_mean_of_validation_folds'},
          availability_ledger:{status:'checked',decision_column:'decision_ts',rows:240,timezone:'Asia/Shanghai',
            label_source:{kind:'source_column'},assumption_count:0,
            features:[{feature:'sensor',source:{kind:'source_column',column:'sensor_ts'},checked_rows:240,late_rows:0}]},
          metric_sets:{comparison:{forest:{recall:0.7}}},diagnostics_available:true,
          report_available:true,evidence_sha256:SHA};
    throw Error('Unexpected request: '+path);
  }};
  const window={location:{assign(){}}};
  runInNewContext(source,{window,document:dom,console,confirm:()=>true,URLSearchParams,FormData:class {}});
  return {api:window.TimePilotModeling,ctx,calls,html:()=>html,status:()=>status,
    controls(v){controls={...controls,...v}},setStale(){stale=true},setFailed(){failed=true}};
}

async function resume(h){await h.api.open({ctx:h.ctx,projectId:'factory',assetId:'source',versionId:'v3'});
  await h.api.click(click('resume-spec'));}

test('v0.39.5 renders verified classification confusion, fold scores, per-X ledger and report link',async()=>{
  const h=harness();await resume(h);
  assert.match(h.html(),/model-availability-ledger/);
  assert.match(h.html(),/sensor_ts/);
  assert.match(h.html(),/model-confusion-matrix/);
  assert.match(h.html(),/混淆矩阵/);
  assert.match(h.html(),/特征贡献/);
  assert.match(h.html(),/sensor/);
  assert.match(h.html(),/2024-03/);
  assert.match(h.html(),/导出评价摘要 MD/);
  assert.match(h.html(),/bi\/native\/source/);
  assert.match(h.html(),new RegExp(SHA));
  assert.equal(h.calls.filter(x=>x.path.includes('/diagnostics?')).length,1);
});

test('BI validation filters preserve their selection and only call bounded diagnostics',async()=>{
  const h=harness();await resume(h);
  h.controls({diag_model:'linear',diag_dimension:'device',diag_value:'SMD&line=3'});
  await h.api.click(click('load-diagnostics'));
  const call=h.calls.at(-1);
  assert.match(call.path,/model=linear/);
  assert.match(call.path,/dimension=device/);
  assert.match(call.path,/value=SMD%26line%3D3/);
  assert.match(h.html(),/option value="device" selected/);
  assert.match(h.html(),/value="SMD&amp;line=3"/);
  assert.match(h.html(),/仅对当前验证最优模型计算置换贡献/);
});

test('stale or failed execution clears all fold evidence, report and filters',async()=>{
  const h=harness();await resume(h);
  const before=h.calls.filter(x=>x.path.includes('/diagnostics?')).length;
  h.setStale();await h.api.click(click('refresh-run'));
  assert.match(h.html(),/历史指标不可继续使用/);
  assert.doesNotMatch(h.html(),/model-confusion-matrix/);
  assert.doesNotMatch(h.html(),/sensor_ts/);
  assert.doesNotMatch(h.html(),/导出评价摘要 MD/);
  assert.equal(h.calls.filter(x=>x.path.includes('/diagnostics?')).length,before);
  h.setFailed();await h.api.click(click('refresh-run'));
  // Current response is still stale, so the more restrictive status wins.
  assert.match(h.html(),/历史指标不可继续使用/);
});
