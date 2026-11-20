import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const ROOT=resolve(import.meta.dirname,'../../..');
const SRC=readFileSync(resolve(ROOT,'src/timepilot/static/modeling-ui.js'),'utf8');
const APP=readFileSync(resolve(ROOT,'src/timepilot/static/app.js'),'utf8');
const FEATURE=readFileSync(resolve(ROOT,'src/timepilot/static/feature-shell.js'),'utf8');
const AUTO=readFileSync(resolve(ROOT,'src/timepilot/static/feature-auto-ui.js'),'utf8');
const proposal={
  profile:{source_rows:1000,sample_rows:100,fields:[
    {name:'采集日期'},{name:'车型'},{name:'销量'},{name:'价格'},{name:'记录ID'},
    {name:'终检不良'},{name:'future_inventory'}],
    recommendation:{time:'采集日期',entity:'车型',frequency:{frequency:'D'},target:'销量'}},
  task_designer:{
    version:'0.38.9',targets:{preferred_numeric:'销量',preferred_label:'终检不良'},
    suggested_features:['价格'],
    role_candidates:[
      {name:'采集日期',role:'time',warnings:['时间字段用于时序或可用时间约束'],suggest_x:false},
      {name:'车型',role:'entity',warnings:[],suggest_x:false},
      {name:'销量',role:'numeric',warnings:[],suggest_x:true},
      {name:'价格',role:'numeric',warnings:[],suggest_x:true},
      {name:'记录ID',role:'entity',warnings:['标识符不能直接作为数值自变量'],suggest_x:false},
      {name:'终检不良',role:'label',warnings:['可能为监督标签'],suggest_x:false},
      {name:'future_inventory',role:'numeric',warnings:['名称暗示事后结果'],suggest_x:false}],
    tasks:['continuous_forecast','tabular_regression','classification','tabular_classification']
      .map(id=>({id,title:id,subtitle:'',explanation:'',route:id.startsWith('tabular')?'featurelab':'modeling',runnable:true}))}
};
function harness(){
  let bodyHTML='',statusMessage='';
  const body={insertAdjacentHTML:()=>{}};
  const scope={
    body,
    getElementById(id){
      if(id==='tp-modeling-body')return {set innerHTML(v){bodyHTML=v;},get innerHTML(){return bodyHTML;},querySelector(){return null;}};
      if(id==='tp-modeling-dialog')return {remove(){}};
      if(id==='tp-modeling-status')return {set textContent(v){statusMessage=v;},dataset:{}};
      return null;
    }
  };
  const window={location:{assign(){throw Error('Test does not navigate')}},TimePilotModeling:null};
  runInNewContext(SRC,{window,document:scope,console,FormData:class{},confirm:()=>true});
  const ctx={req:async uri=>{assert.match(uri,/\/suggestions\?/);return proposal;}};
  return {api:window.TimePilotModeling,ctx,html:()=>bodyHTML,status:()=>statusMessage};
}
function eventFor(kind){return {preventDefault(){},target:{closest(){return {dataset:{modeling:'choose-task',mode:kind}};}}};}

test('new modeling selector shows four distinct job types and business goal',async()=>{
  const h=harness();
  await h.api.open({ctx:h.ctx,projectId:'p',assetId:'asset',versionId:'base'});
  assert.match(h.html(),/data-testid="model-task-selector"/);
  for(const task of proposal.task_designer.tasks)assert.match(h.html(),new RegExp('model-task-'+task.id));
  assert.match(h.html(),/name="business_goal"/);
  assert.match(h.status(),/字段建议/);
});

test('general regression preselects explainable X and Y; never selects future/label/id',async()=>{
  const h=harness();await h.api.open({ctx:h.ctx,projectId:'p',assetId:'a'});
  await h.api.click(eventFor('tabular_regression'));
  const html=h.html();
  assert.match(html,/id="tp-modeling-general-form"/);
  assert.match(html,/<option value="销量" selected>销量<\/option>/);
  assert.match(html,/<input name="observed_features" type="checkbox" value="价格" checked\s*\/>/);
  for(const name of ['记录ID','终检不良','future_inventory']) {
    const start=html.indexOf(`name="observed_features" type="checkbox" value="${name}"`);assert.ok(start>0);
    assert.match(html.slice(start,start+120),/disabled/);
  }
  assert.match(html,/静态.*K 折/);
});

test('switching generic task resets the previous target in favor of label',async()=>{
  const h=harness();await h.api.open({ctx:h.ctx,projectId:'p',assetId:'a'});
  await h.api.click(eventFor('tabular_regression'));
  await h.api.click(eventFor('tabular_classification'));
  assert.match(h.html(),/<option value="终检不良" selected>终检不良<\/option>/);
  assert.match(h.html(),/分层 K 折/);
});

test('legacy form uses strict event delegation; FeatureLab handoff is explicit and review-only',()=>{
  assert.match(APP,/tp-modeling-general-form/);
  assert.match(FEATURE,/tp_features/);
  assert.match(FEATURE,/autoTargetHint/);
  assert.match(AUTO,/仅为待核对的字段意向/);
  assert.match(SRC,/xs.length>32/);
  assert.match(SRC,/不能直接参与训练的 X/);
});
