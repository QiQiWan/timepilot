import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const path=name=>new URL('../../../src/timepilot/static/'+name,import.meta.url);
const scripts=['feature-workbench-ui.js','feature-shell.js'].map(name=>readFileSync(path(name),'utf8'));

function harness(){
  let url=new URL('http://localhost/projects/pid/features/analysis?asset=asset1&version=base');
  let stack=[url.href], index=0;
  const root={innerHTML:'',querySelector:()=>null};
  const document={getElementById:id=>id==='tp-feature-lab'?root:null,querySelector:()=>null};
  const location={get href(){return url.href;},get pathname(){return url.pathname;},get search(){return url.search;}};
  const history={state:{},pushState(state,title,path){url=new URL(path,url.href);stack=stack.slice(0,index+1);stack.push(url.href);index++;},
    replaceState(state,title,path){url=new URL(path,url.href);stack[index]=url.href;},back(){if(index>0){index--;url=new URL(stack[index]);}}};
  const FormData=class {constructor(form){this.form=form;}getAll(){return this.form.visibleValues||[];}get(){return null;}};
  const sandbox={window:{},document,location,history,URL,URLSearchParams,AbortController,setTimeout,clearTimeout,console,FormData};
  for(const script of scripts)vm.runInNewContext(script,sandbox);
  const ui=sandbox.window.TimePilotFeatureV2UI;
  const sha='a'.repeat(64);
  const first={candidate_id:'c1',display_name:'均值',output_name:'pressure_mean',status:'recommended',operator_id:'rolling.mean',quality:{null_rate:0,finite_count:3,rows_scanned:3}};
  const second={candidate_id:'c2',display_name:'类别',output_name:'is_A',status:'review',operator_id:'category.is_value',quality:{null_rate:0,finite_count:3,rows_scanned:3}};
  let session={session_id:'sid',source_sha256:sha,asset_id:'asset1',version_id:'base',status:'ready',stage:'review',revision:2,
    selection:['c1'],candidate_session_id:'cs1',report:{quality_rows:[first,second],candidate_count:2,recommended_count:1,review_count:1,excluded_count:0}};
  let saved=[];
  const ctx={
    toast:()=>{},
    navigate(p){history.pushState({},'',p);return ui.render(ctx,'pid','analysis');},
    async req(path,options={}){
      const q=new URL(path,'http://localhost');const asset=q.searchParams.get('asset_id')||url.searchParams.get('asset')||'asset1';
      const version=q.searchParams.get('version_id')||url.searchParams.get('version')||'base';
      const endpoint=q.pathname;
      if(endpoint.endsWith('/native-assets'))return [{native_asset_id:'asset1',asset:{name:'sensors.csv',columns:[{name:'pressure'}]}},{native_asset_id:'asset2',asset:{name:'batch.csv',columns:[{name:'quality'}]}}];
      if(endpoint.endsWith('/versions'))return {versions:[{version_id:'base'},{version_id:'v2',is_current:true}]};
      if(endpoint.endsWith('/contracts'))return {state:'confirmed',revision:2,source_sha256:sha,fields:[{field_id:'fid1',source_name:'pressure',physical_type:'DOUBLE'}]};
      if(endpoint.endsWith('/operators'))return {operators:[]};
      if(endpoint.endsWith('/sets'))return [];
      if(endpoint.endsWith('/workflow/summary'))return {};
      if(endpoint.endsWith('/analysis/latest'))return {session:asset==='asset1'&&version==='base'?session:null};
      if(endpoint.endsWith('/time-basis/infer'))return {fields:[]};
      if(endpoint.endsWith('/candidate-sessions/cs1'))return {stale:false,candidates:[
        {...first,input_field_ids:['fid1'],parameters:{window:2}},{...second,input_field_ids:['fid1'],parameters:{category_value:'A'}}]};
      if(endpoint.endsWith('/supervision-options'))return {eligible:false};
      if(endpoint.endsWith('/delivery'))return {same_artifact_evaluation:false};
      if(endpoint.endsWith('/candidate-sessions/latest'))return {session:null};
      if(endpoint.endsWith('/field-suggestions'))return {fields:[]};
      if(endpoint.endsWith('/profiles'))return {items:[]};
      if(endpoint.endsWith('/profile-jobs'))return {items:[]};
      if(endpoint.endsWith('/selection')&&options.method==='PUT'){
        const payload=JSON.parse(options.body);saved=payload.candidate_ids;
        session={...session,revision:3,selection:saved};return session;
      }
      if(endpoint.endsWith('/chart'))return {status:'ready',mode:q.searchParams.get('mode')||'distribution',
        source_sha256:sha,candidate_id:endpoint.split('/').at(-2),feature_name:'计算特征',version_id:'base',
        candidate_fingerprint:'b'.repeat(64),contract_revision:2,available_modes:['distribution','series','category','group'],
        quality:{rows_scanned:3,finite_count:3},points:[{label:'A',count:2,mean:3},{label:'B',count:1,mean:6}]};
      throw Error('unhandled '+path);
    },
  };
  const event=(action,extra={})=>({preventDefault(){},target:{closest(selector){return selector==='[data-feature-v2]'?{dataset:{featureV2:action,...extra}}:null;}}});
  const change=(type,value)=>({target:{dataset:{featureV2Change:type},value,checked:true}});
  return {ui,ctx,document,history,location,event,change,root,get saved(){return saved;}};
}

test('v0.33.0/0.33.1 real controller restores filter, focus and chart mode after render/back, isolates dataset/version',async()=>{
  const h=harness();
  await h.ui.render(h.ctx,'pid','analysis');
  assert.match(h.root.innerHTML,/候选已计算，等待审核/);
  await h.ui.click(h.event('wb-focus',{cid:'c2'}));
  assert.equal(new URLSearchParams(h.location.search).get('candidate'),'c2');
  await h.ui.click(h.event('wb-filter',{filter:'review'}));
  assert.equal(new URLSearchParams(h.location.search).get('filter'),'review');
  assert.match(h.root.innerHTML,/aria-label="特征全量分布/);
  await h.ui.click(h.event('wb-chart',{chart:'group'}));
  assert.equal(new URLSearchParams(h.location.search).get('chart'),'group');
  await h.ui.render(h.ctx,'pid','analysis'); // simulates reload/mount from URL
  assert.match(h.root.innerHTML,/data-filter="review" class="active"/);
  assert.match(h.root.innerHTML,/data-cid="c2"/);
  assert.match(h.root.innerHTML,/data-chart="group" class="active"/);
  h.history.back();
  await h.ui.render(h.ctx,'pid','analysis');
  assert.equal(new URLSearchParams(h.location.search).get('chart'),null);
  assert.equal(new URLSearchParams(h.location.search).get('candidate'),'c2');
  await h.ui.change(h.change('select-version','v2'));
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(h.location.search,/version=v2/);
  assert.match(h.root.innerHTML,/尚未开始特征分析/);
  assert.doesNotMatch(h.root.innerHTML,/data-cid="c2"/);
  await h.ui.change(h.change('select-asset','asset2'));
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(h.location.search,/asset=asset2/);
  assert.doesNotMatch(h.root.innerHTML,/候选已计算，等待审核/);
  h.ui.dispose();
});

test('v0.33.1 filtering does not discard selected features hidden in other filters',async()=>{
  const h=harness();await h.ui.render(h.ctx,'pid','analysis');
  await h.ui.change({target:{dataset:{featureV2Change:'wb-choice'},value:'c2',checked:true}});
  await h.ui.click(h.event('wb-filter',{filter:'review'}));
  // The currently visible review filter contains only c2. Saving must
  // retain the hidden, previously selected c1 too.
  await h.ui.submit({preventDefault(){},target:{id:'tp-fl2-auto-select',visibleValues:['c2']}});
  assert.deepEqual(h.saved,['c1','c2']);
  assert.match(h.root.innerHTML,/2 项已选/);
  h.ui.dispose();
});
