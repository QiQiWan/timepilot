import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const script=name=>readFileSync(new URL('../../../src/timepilot/static/'+name,import.meta.url),'utf8');
const html=script('feature-workbench-ui.js');
const shell=script('feature-shell.js');
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const h={e:esc,u:encodeURIComponent};
let views={window:{}};
vm.runInNewContext(html,views);
const view=views.window.TimePilotFeatureViews.analysis;
const base={pid:'pid',asset:'asset',version:'base',assets:[{native_asset_id:'asset',asset:{name:'quality.csv'}}],
 contract:{state:'confirmed',revision:3,fields:[{field_id:'col1'}]},uiFilter:'all',uiDraftSelection:[]};

const session=(over={})=>({session_id:'sid',asset_id:'asset',version_id:'base',revision:3,source_sha256:'a'.repeat(64),
 status:'failed',stage:'profile',error:'worker stopped',recovery:{retryable:true},...over});

test('v0.33.3 recovery UI separates failed job retry, stale source and evaluation error',()=>{
 assert.match(view({...base,autoSession:session()},h),/data-feature-v2="auto-retry"/);
 assert.doesNotMatch(view({...base,autoSession:session({stale:true})},h),/data-feature-v2="auto-retry"/);
 assert.match(view({...base,autoSession:session({status:'ready',stage:'materialized',
   evaluation_error:'model evaluation failed',evaluation_job_id:'job-eval'})},h),/重试失败阶段/);
 assert.doesNotMatch(view({...base,autoSession:session({status:'ready',stage:'materialized',recovery:{retryable:false},
   evaluation_error:'evaluation planning failed'})},h),/data-feature-v2="auto-retry"/);
 assert.match(view({...base,autoSession:session({status:'profiling',stage:'profile',recovery:{retryable:false},progress:0.3})},h),/分析任务正在执行/);
});

test('v0.33.3 polling is read-only, and human retry uses a revision-checked POST',async()=>{
 const sha='a'.repeat(64);
 let url=new URL('http://localhost/projects/pid/features/analysis?asset=asset&version=base');
 const root={innerHTML:'',querySelector:()=>null};
 const document={getElementById:id=>id==='tp-feature-lab'?root:null,querySelector:()=>null};
 const location={get href(){return url.href;},get pathname(){return url.pathname;},get search(){return url.search;}};
 const history={state:{},replaceState(state,title,path){url=new URL(path,url.href);},pushState(state,title,path){url=new URL(path,url.href);}};
 const sandbox={window:{},document,location,history,URL,URLSearchParams,AbortController,setTimeout:()=>0,clearTimeout:()=>{},console,
  FormData:class{constructor(form){this.form=form;}get(){return null;}getAll(){return [];}}};
 vm.runInNewContext(html,sandbox);
 vm.runInNewContext(shell,sandbox);
 const ui=sandbox.window.TimePilotFeatureV2UI;
 const calls=[];let current=session({status:'profiling',stage:'profile',error:null});
 const ctx={toast(){},navigate(path){url=new URL(path,url.href);},async req(path,options={}){
  calls.push({path,method:options.method||'GET'});
  const p=new URL(path,'http://localhost').pathname;
  if(p.endsWith('/native-assets'))return [{native_asset_id:'asset',asset:{name:'quality.csv',columns:[{name:'pressure'}]}}];
  if(p.endsWith('/versions'))return {versions:[{version_id:'base'}]};
  if(p.endsWith('/contracts'))return {state:'confirmed',revision:3,source_sha256:sha,fields:[{field_id:'col1'}]};
  if(p.endsWith('/operators'))return {operators:[]};
  if(p.endsWith('/sets'))return [];
  if(p.endsWith('/workflow/summary'))return {};
  if(p.endsWith('/analysis/latest'))return {session:current};
  if(p.endsWith('/time-basis/infer'))return {fields:[]};
  if(p.endsWith('/supervision-options'))return {eligible:false};
  if(p.endsWith('/delivery'))return {status:'analysis_only'};
  if(p.endsWith('/analysis/sessions/sid'))return current;
  if(p.endsWith('/retry')&&options.method==='POST'){
   const request=JSON.parse(options.body);
   assert.equal(request.expected_revision,current.revision);
   current=session({status:'profiling',stage:'profile',revision:current.revision+1,error:null});
   return current;
  }
  if(p.endsWith('/candidate-sessions/latest'))return {session:null};
  if(p.endsWith('/field-suggestions'))return {fields:[]};
  if(p.endsWith('/profiles'))return {items:[]};
  if(p.endsWith('/profile-jobs'))return {items:[]};
  throw new Error('Unexpected '+path);
 }};
 const click=action=>({preventDefault(){},target:{closest(selector){return selector==='[data-feature-v2]'?{dataset:{featureV2:action}}:null;}}});
 await ui.render(ctx,'pid','analysis');
 await ui.click(click('auto-refresh'));
 assert(calls.some(c=>c.path.endsWith('/analysis/sessions/sid')&&c.method==='GET'));
 assert(!calls.some(c=>c.path.endsWith('/advance')));
 current=session({status:'failed',stage:'profile',revision:4});
 await ui.render(ctx,'pid','analysis');
 assert.match(root.innerHTML,/data-feature-v2="auto-retry"/);
 await ui.click(click('auto-retry'));
 assert(calls.some(c=>c.path.endsWith('/retry')&&c.method==='POST'));
 assert.match(root.innerHTML,/分析任务正在执行/);
 ui.dispose();
});

test('v0.33.3 BI navigation refuses a stale/mismatched hash, while report remains source-bound',()=>{
 assert.match(shell,/proof\.artifact\.sha256!==s\.autoSession\.artifact\.sha256/);
 assert.match(shell,/\/viewer-receipt/);
 assert.match(shell,/\&auto_bi=/);
 assert.match(shell,/\&set=/);
 assert.match(shell,/\&auto_sid=/);
 assert.match(shell,/\/analysis\/sessions\/'\+u\(sid\)\)/); // read-only poll
 assert.match(shell,/\/analysis\/sessions\/'\+u\(session\.session_id\)\+'\/retry/);
});
