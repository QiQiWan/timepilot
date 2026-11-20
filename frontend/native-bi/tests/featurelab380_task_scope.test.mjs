import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=name=>fs.readFileSync(new URL('../../../src/timepilot/static/'+name,import.meta.url),'utf8');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sha='f'.repeat(64),scope='a'.repeat(64);
const fields=Array.from({length:31},(_,i)=>({field_id:'f'+i,source_name:'Temperature '+i,physical_type:'DOUBLE',unit:'C'}));
function dashboardFixture({href='http://localhost/projects/p/analysis?section=statistics&asset=a&version=base', responseFieldIds=null}={}){
  let url=new URL(href),html='';const listeners={},requests=[];
  const host={isConnected:true,addEventListener(t,f){listeners[t]=f;},removeEventListener(t,f){if(listeners[t]===f)delete listeners[t];}};
  Object.defineProperty(host,'innerHTML',{get(){return html;},set(v){html=v;}});
  const w={history:{state:null,replaceState(_a,_b,s){url=new URL(s,url);}}};
  Object.defineProperty(w,'location',{get:()=>url});
  const ctx={req:async (route,opt)=>{
    const data=JSON.parse(opt.body);requests.push({route,data});
    const base={asset_id:'a',version_id:'base',source_sha256:sha,contract_revision:3,scope_sha256:scope};
    if(route.endsWith('/dashboard')){
      const ids=responseFieldIds||data.field_ids;
      return {...base,filters:data.filters,selection:{field_ids:ids,fields_truncated:31>ids.length},
        metrics:{records:11,source_records:11,columns:31,numeric_fields:31,selected_fields:ids.length,
          missing_percent:0,missing_cells:0,invalid_numeric_cells:0,non_finite_numeric_cells:0,conversion_failed_cells:0,coverage_percent:100,
          metric_scope:{field_ids:ids,rows:11,denominator_cells:11*ids.length}},
        fields:ids.map(field_id=>({field_id,name:field_id,numeric:true,finite_count:11,min:0,max:1,stddev:0.5})),
        correlations:[],evidence_analysis:{status:'insufficient_evidence',message:'No evidence'}};
    }
    return {...base,field_name:data.field_id,kind:data.kind,points:[],rows_filtered:11};
  }};
  vm.runInNewContext(source('data-dashboard-v0372.js'),{window:w,URL,URLSearchParams,AbortController,Number,Math,JSON,Promise,document:{},encodeURIComponent});
  const mount=(asset='a',version='base',digest=sha)=>w.TimePilotDataDashboard372.mount(host,{pid:'p',aid:asset,vid:version,source_sha256:digest,contract_revision:3,fields,ctx});
  return {w,host,listeners,requests,mount,get url(){return url;},get html(){return html;}};
}
test('31-column source has explicit 24-column SQL budget and supports selecting another column',async()=>{
  const f=dashboardFixture();f.mount();await sleep(30);
  let sql=f.requests.find(x=>x.route.endsWith('/dashboard'));
  assert.equal(sql.data.field_ids.length,24);
  assert.deepEqual([...sql.data.field_ids],fields.slice(0,24).map(x=>x.field_id));
  assert.match(f.html,/本次统计字段（24\/31/);
  f.listeners.change({target:{dataset:{dash:'stat-field'},checked:false,value:'f0'}});
  await sleep(25);
  f.listeners.change({target:{dataset:{dash:'stat-field'},checked:true,value:'f25'}});
  await sleep(35);
  const requests=f.requests.filter(x=>x.route.endsWith('/dashboard'));
  sql=requests.at(-1);
  assert.equal(sql.data.field_ids.length,24);
  assert.ok(sql.data.field_ids.includes('f25'));
  assert.ok(!sql.data.field_ids.includes('f0'));
  assert.deepEqual(JSON.parse(f.url.searchParams.get('da_fields')),sql.data.field_ids);
  assert.deepEqual(JSON.parse(f.url.searchParams.get('da_source')),['p','a','base',sha,3]);
  f.w.TimePilotDataDashboard372.dispose();
});
test('modified URL version/source cannot replay an old source-local filter',async()=>{
  const old=JSON.stringify(['p','a','base',sha,3]);
  const url=new URL('http://localhost/projects/p/analysis?asset=a&version=base&section=statistics');
  url.searchParams.set('da_source',old);
  url.searchParams.set('da_filters',JSON.stringify([{field_id:'f0',op:'eq',value:'1000'}]));
  url.searchParams.set('da_fields',JSON.stringify(['f25']));
  const changed=dashboardFixture({href:url.toString()});
  changed.mount('a','base','e'.repeat(64));await sleep(25);
  const overview=changed.requests.find(x=>x.route.endsWith('/dashboard'));
  assert.equal(overview.data.filters.length,0);
  assert.equal(overview.data.field_ids.length,24);
  changed.w.TimePilotDataDashboard372.dispose();
});
test('saved URL state survives a same-source refresh and fails shut on mismatched aggregation scope',async()=>{
  const a=dashboardFixture();a.mount();await sleep(15);
  a.listeners.change({target:{dataset:{dash:'stat-field'},checked:false,value:'f0'}});
  await sleep(20);
  const href=a.url.href;
  a.w.TimePilotDataDashboard372.dispose();
  const b=dashboardFixture({href});b.mount();await sleep(25);
  const q=b.requests.find(x=>x.route.endsWith('/dashboard'));
  assert.equal(q.data.field_ids.length,23);
  assert.ok(!q.data.field_ids.includes('f0'));
  b.w.TimePilotDataDashboard372.dispose();
  const c=dashboardFixture({responseFieldIds:['f0']});c.mount();await sleep(25);
  assert.match(c.html,/统计字段范围与本次选择不一致/);
  assert.doesNotMatch(c.html,/筛选后记录/);
  c.w.TimePilotDataDashboard372.dispose();
});
function analysisFixture(initialSection='overview',delayedFeature=false){
  let calls=[], pendingResolve;
  const workspace={dataset:{section:initialSection}},listeners={};
  const host={isConnected:true,innerHTML:'',closest:()=>workspace,addEventListener(t,fn){listeners[t]=fn;},querySelector(){return null;}};
  const doc={getElementById:()=>host,dispatchEvent(){}};
  const w={location:{href:'http://localhost/projects/p/analysis?asset=a&version=base',search:'?asset=a&version=base'},
    TimePilotDataDashboard372:{dispose(){}},TimePilotAnalysisWorkspace:{activate(){}},history:{state:null,replaceState(){}}};
  const ctx={req:async (path,options)=>{
    calls.push(path);
    if(path.endsWith('/native-assets'))return [{native_asset_id:'a',asset:{columns:['col'],name:'Production'}}];
    if(path.includes('/native-bi/assets/'))return {versions:[]};
    if(path.includes('/data-analysis?'))return {status:'ready',source_sha256:sha,contract_revision:3,row_count:11,column_count:1,
      profile:{fields:[{field_id:'f0',source_name:'Temperature',physical_type:'DOUBLE',null_rate:0}]},findings:[],recommendations:[]};
    if(path.includes('/feature-lab/workspace?')){
      if(delayedFeature)return await new Promise(resolve=>{pendingResolve=resolve});
      return {feature_preview:{status:'not_started'}};
    }
    if(path.endsWith('/feature-lab/workspace/start'))return {feature_preview:{status:'finished'}};
    throw Error('Unexpected route '+path);
  }};
  vm.runInNewContext(source('data-analysis-ui.js'),{window:w,location:w.location,document:doc,URL,URLSearchParams,encodeURIComponent,CustomEvent:class{},setTimeout,clearTimeout,Number,JSON});
  return {api:w.TimePilotDataAnalysisUI,ctx,workspace,calls,resolve:()=>pendingResolve?.({feature_preview:{status:'not_started'}})};
}
test('overview and quality never start a feature preview, features task starts once',async()=>{
  const f=analysisFixture();await f.api.render(f.ctx,'p');
  assert.ok(!f.calls.some(x=>x.includes('feature-lab/workspace')));
  f.workspace.dataset.section='quality';f.api.onSectionChange('quality');
  assert.ok(!f.calls.some(x=>x.includes('feature-lab/workspace')));
  f.workspace.dataset.section='features';f.api.onSectionChange('features');await sleep(15);
  assert.equal(f.calls.filter(x=>x.endsWith('/feature-lab/workspace/start')).length,1);
  f.api.onSectionChange('features');await sleep(15);
  // Existing previews are only read; they should not trigger a second start.
  assert.equal(f.calls.filter(x=>x.endsWith('/feature-lab/workspace/start')).length,1);
  f.api.dispose();
});
test('leaving features before preview GET completes cancels the deferred start',async()=>{
  const f=analysisFixture('overview',true);await f.api.render(f.ctx,'p');
  f.workspace.dataset.section='features';f.api.onSectionChange('features');await sleep(5);
  f.workspace.dataset.section='overview';f.api.onSectionChange('overview');f.resolve();await sleep(15);
  assert.equal(f.calls.filter(x=>x.endsWith('/feature-lab/workspace/start')).length,0);
  f.api.dispose();
});
