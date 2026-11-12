import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const here=path.dirname(fileURLToPath(import.meta.url));
const script=fs.readFileSync(path.resolve(here,'../../../src/timepilot/static/feature-advanced-ui.js'),'utf8');
const refs={
  id:{field_id:'f_id',source_name:'sample',role:'record_id',confirmation:'confirmed'},
  time:{field_id:'f_time',source_name:'prediction_time',role:'prediction_time',confirmation:'confirmed'},
  target:{field_id:'f_label',source_name:'NG',role:'label',confirmation:'confirmed'},
  labeltime:{field_id:'f_labeltime',source_name:'label_available',role:'available_at',confirmation:'confirmed'},
  device:{field_id:'f_device',source_name:'device',role:'entity_key',confirmation:'confirmed'},
};
function context(){
  const window={TimePilotFeatureViews:{}};
  const sand={window,Date,URL,console,FormData:class {
    constructor(form){this.values=form.values;}
    get(k){return this.values[k]?.[0]??null;}
    getAll(k){return this.values[k]||[];}
    entries(){return Object.entries(this.values).map(([k,v])=>[k,v?.[0]]);}
  }};
  vm.runInNewContext(script,sand,{filename:'feature-advanced-ui.js'});
  return window.TimePilotFeatureAdvanced;
}
function scenario(){
  const a={engine:'tsfresh',job:{job_id:'jA',status:'succeeded'},
    artifact:{job_id:'jA',rows:3,columns:['__sample_id','signal_mean','__candidate_cutoff_utc'],
       native_asset_id:'nativeA',source_fingerprint:'abcde12345000000',pit:{pit:'verified_per_sample'}},
    capabilities:{engines:{tsfresh:true,featuretools:true}},joinDecision:'nativeDecision',
    joinContract:{state:'confirmed',fields:Object.values(refs),entity_field_ids:['f_device']}};
  return {pid:'projectABC',asset:'nativeA',version:'base',
    assets:[{native_asset_id:'nativeA',asset:{name:'测量'}},{native_asset_id:'nativeDecision',asset:{name:'预测实例'}}],
    contract:{state:'confirmed',fields:Object.values(refs)},advanced:a};
}

test('advanced result is paired with real PIT join approval form',()=>{
  const a=context(),s=scenario(),markup=a.render(s);
  assert.match(markup,/id="tp-adv-join-form"/);
  assert.match(markup,/name="chosen" value="signal_mean"/);
  assert.match(markup,/原始数据不会更改/);
  assert.doesNotMatch(markup,/name="chosen" value="__candidate_cutoff_utc"/);
  assert.doesNotMatch(markup,/id="tp-adv-eval-form"/);
  s.advanced.joined={job_id:'jJoin',native_asset_id:'nativeJoined',rows:3,joined_sha256:'a'.repeat(64)};
  const full=a.render(s);
  assert.match(full,/id="tp-adv-eval-form"/);
  assert.match(full,/bi\/native\/nativeJoined/);
});

test('PIT join validates before explicit approval, then evaluates same join id',async()=>{
  const a=context(),s=scenario(),calls=[];
  const api=async(url,opts)=>{
    calls.push({url,body:opts?.body?JSON.parse(opts.body):undefined});
    if(url==='/advanced/join/validate')return {candidate_sha256:'b'.repeat(64),decision_sha256:'c'.repeat(64)};
    if(url==='/advanced/join/jobs')return {status:'queued',job_id:'jJoin'};
    if(url==='/advanced/join/evaluations')return {status:'queued',job_id:'jEval'};
    throw Error(url);
  };
  const form=(id,values)=>({id,values});
  await a.submit(s,api,form('tp-adv-join-form',{chosen:['signal_mean'],decision_asset:['nativeDecision'],decision_key:['f_id'],prediction_time:['f_time']}),()=>{});
  assert.equal(s.advanced.joinDraft.decisions_asset_id,'nativeDecision');
  assert.equal(calls[0].url,'/advanced/join/validate');
  await a.click(s,api,{dataset:{featureAdvanced:'join-confirm'}},()=>{});
  assert.equal(calls[1].url,'/advanced/join/jobs');
  assert.equal(calls[1].body.confirm,true);
  s.advanced.joined={job_id:'jJoin',native_asset_id:'nativeJoined',rows:3,joined_sha256:'a'.repeat(64)};
  await a.submit(s,api,form('tp-adv-eval-form',{label:['f_label'],label_available:['f_labeltime'],slice:['f_device'],model:['both'],task_type:['binary_classification']}),()=>{});
  const payload=calls.at(-1).body;
  assert.equal(payload.join_job_id,'jJoin');
  assert.equal(payload.task.label_field_id,'f_label');
  assert.equal(payload.task.prediction_time_field_id,'f_time');
  assert.equal(payload.models.length,2);
});
