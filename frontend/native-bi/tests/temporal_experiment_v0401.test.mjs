import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {runInNewContext} from 'node:vm';

const code = readFileSync(resolve(import.meta.dirname,
  '../../../src/timepilot/static/modeling-ui.js'), 'utf8');

function previewDraft(validation_strategy='leave_device_out') {
  let html='',message='';
  const form={
    querySelector(selector) {
      if (selector==='[name=task_kind]')return {value:'classification'};
      if (selector==='[name=availability_policy]')return {value:'declared_lag'};
      if (selector==='[name=feature_policy]')return {value:'snapshot_at_decision'};
      return {value:''};
    },
    querySelectorAll() {return [];},
  };
  const slot={set innerHTML(v){html=v;},get innerHTML(){return html;},
    querySelector(selector){return selector==='form'?form:null;},querySelectorAll(){return [];}};
  const document={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-body')return slot;
    if(id==='tp-modeling-form')return form;
    if(id==='tp-modeling-status')return {set textContent(value){message=value;},dataset:{}};
    return null;
  }};
  const draft={draft_id:'00000000-0000-4000-8000-000000000004',
    asset_id:'asset',version_id:'base',name:'Early defect OOD',status:'review_required',
    mapping:{time:'节点时间',entity:'设备',target:'是否不良',observed_features:['锡膏厚度'],
      availability_policy:'declared_lag',publication_lag_days:0,timezone:'Asia/Shanghai'},
    plan:{task_kind:'classification',task_family:'equipment_state',feature_policy:'snapshot_at_decision',
      validation_strategy,validation_windows:3,frequency:'h',
      classification:{positive_values:['NG'],negative_values:['OK'],
        label_available_at:'标签确认时间',min_recall:0.9}}};
  const suggestion={profile:{source_rows:480,sample_rows:240,fields:
    ['节点时间','设备','是否不良','锡膏厚度','标签确认时间'].map(name=>({name})),
    recommendation:{}},task_designer:{tasks:[],role_candidates:[],suggested_features:[]}};
  const ctx={req:async(path)=>{
    if(path.includes('/drafts/'))return draft;
    if(path.includes('/suggestions?'))return suggestion;
    throw Error('Unexpected route '+path);
  }};
  const window={};
  runInNewContext(code,{window,document,console,FormData:class{},confirm:()=>true});
  return {open:()=>window.TimePilotModeling.openDraft({ctx,projectId:'p',draftId:draft.draft_id}),
    html:()=>html,message:()=>message};
}

test('saved temporal classification OOD protocol and number of folds render in actual UI',async()=>{
  const ui=previewDraft();await ui.open();
  assert.match(ui.html(),/name="validation_strategy"/);
  assert.match(ui.html(),/value="leave_device_out" selected/);
  assert.match(ui.html(),/name="classification_folds"[^>]*value="3"/);
  assert.match(ui.html(),/独立测试仍为后续时段的时间测试/);
  assert.match(ui.html(),/零样本设备外推目前仅支持二分类/);
});

test('saved time-forward classification keeps time protocol selected',async()=>{
  const ui=previewDraft('time_forward');await ui.open();
  assert.match(ui.html(),/value="time_forward" selected/);
  assert.doesNotMatch(ui.html(),/value="leave_device_out" selected/);
});

test('temporal submit wires selected protocol to BOTH aligned task contracts',()=>{
  assert.match(code,/validation:\{kind:kind==='classification'&&f\.validation_strategy==='leave_device_out'\?'leave_device_out'/);
  assert.match(code,/validation_strategy:kind==='classification'&&f\.validation_strategy==='leave_device_out'\?'leave_device_out':'time_forward'/);
  assert.match(code,/if\(kind==='classification'&&f\.validation_strategy==='leave_device_out'&&!f\.entity\)/);
});
