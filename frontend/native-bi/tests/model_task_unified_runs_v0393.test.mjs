import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {resolve} from 'node:path';

const CODE=readFileSync(resolve(import.meta.dirname,'../../../src/timepilot/static/modeling-ui.js'),'utf8');
const TID='00000000-0000-4000-8000-000000000011';
const DID='00000000-0000-4000-8000-000000000012';
function action(name,id){return {preventDefault(){},target:{closest(){return {dataset:{modeling:name,taskId:id}}}}};}
function harness(stale=false){
  const calls=[];let markup='',message='',execution='needs_approval';
  const task={task_id:TID,revision:1,status:'approved',effective_status:'approved',
    spec:{name:'Factory quality',asset_id:'asset',version_id:'base',task_kind:'classification',
      business_goal:'Quality monitoring',target:{column:'result'},features:[]}};
  const draft={draft_id:DID,asset_id:'asset',version_id:'base',name:'Factory quality',status:'ready',
    mapping:{time:'event_time',target:'result'},plan:{task_kind:'classification'},
    preflight:{ready:true,source_rows:250,selected_rows:250,eligible_rows:240,
      series_count:2,feature_count:2,blockers:[],warnings:[]},run_id:null};
  const win={location:{assign(){}}};
  const dom={body:{insertAdjacentHTML(){}},getElementById(id){
    if(id==='tp-modeling-body')return {set innerHTML(v){markup=v;},get innerHTML(){return markup;},querySelector(){return null},querySelectorAll(){return []}};
    if(id==='tp-modeling-dialog')return {remove(){}};
    if(id==='tp-modeling-status')return {set textContent(v){message=v;},dataset:{}};
    return null;
  }};
  const ctx={req:async(path,opts)=>{
    calls.push({path,opts});
    if(path.includes('/suggestions?'))return {profile:{source_rows:250,sample_rows:120,fields:[{name:'result'}]},
      task_designer:{tasks:[],role_candidates:[],suggested_features:[],targets:{}}};
    if(path.endsWith('/model-tasks'))return {items:[task]};
    if(path.endsWith('/model-tasks/'+TID))return task;
    if(path.endsWith('/temporal-link'))return {status:stale?'stale':'needs_approval',draft_id:DID};
    if(path.endsWith('/drafts/'+DID))return draft;
    if(path.endsWith('/execution')){
      if(opts?.method==='POST'){execution='queued';}
      return {schema_version:'model-run-v1',state:stale?'stale':execution,
        run_id:execution==='queued'?'00000000-0000-4000-8000-000000000013':null,
        executor:'existing_temporal_pit',validation_protocol:'temporal_holdout',
        data_version:'base',current:!stale,metric_sets:{},report_available:false,
        evidence_sha256:'a'.repeat(64)};
    }
    if(path.endsWith('/evidence'))return {status:stale?'stale':'not_submitted',metrics:null};
    throw Error('Unexpected '+path);
  }};
  runInNewContext(CODE,{window:win,document:dom,console,confirm:()=>true,FormData:class {}});
  return {api:win.TimePilotModeling,ctx,calls,html:()=>markup,status:()=>message};
}

test('temporal model has a single visible run action and sends explicit consent',async()=>{
  const h=harness();
  await h.api.open({ctx:h.ctx,projectId:'project',assetId:'asset',versionId:'base'});
  await h.api.click(action('resume-spec',TID));
  assert.match(h.html(),/开始模型评价/);
  assert.doesNotMatch(h.html(),/生成训练数据/);
  await h.api.click(action('start-unified'));
  const entry=h.calls.find(x=>x.path.endsWith('/execution')&&x.opts?.method==='POST');
  assert.ok(entry);
  const body=JSON.parse(entry.opts.body);
  assert.equal(body.expected_revision,1);
  assert.equal(body.confirm_temporal_execution,true);
  assert.match(h.html(),/刷新模型结果/);
  assert.match(h.html(),/model-run-v1|模型运行/);
});

test('stale link cannot show a one-click run button',async()=>{
  const h=harness(true);
  await h.api.open({ctx:h.ctx,projectId:'project',assetId:'asset',versionId:'base'});
  await h.api.click(action('resume-spec',TID));
  assert.doesNotMatch(h.html(),/开始模型评价/);
  assert.match(h.status(),/历史建模草稿或源版本发生变化/);
});
