import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import vm from 'node:vm';
import {test} from 'node:test';

const root=resolve(import.meta.dirname,'../../..');
const load=name=>readFileSync(join(root,'src/timepilot/static',name),'utf8');
const sandbox={window:{TimePilotFeatureViews:{}},console};
vm.createContext(sandbox);
vm.runInContext(load('feature-auto-ui.js'),sandbox,{filename:'feature-auto-ui.js'});
const render=sandbox.window.TimePilotFeatureViews.analysis;
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const artifact='a'.repeat(64);
const base={pid:'p',asset:'a1',assets:[{native_asset_id:'a1',asset:{name:'process.csv'}}],
  contract:{fields:[],source_sha256:'s'.repeat(64)},autoObjective:'classification',
  autoGoal:'提前识别质量不良',timeBasis:{fields:[]}};
const report={row_count:120,field_count:8,candidate_count:2,evaluated_count:2,
  recommended_count:1,profile_scope:'full',high_missing_fields:[],limitations:[],
  quality_rows:[{candidate_id:'c1',output_name:'pressure_std',display_name:'压力波动',
    status:'recommended',reason:'覆盖率合格',operator_id:'rolling.std',
    quality:{null_rate:0,rows_scanned:120,std:4}}]};

function state(artifactReady=false){
 return {...base,autoSession:{session_id:'sid',status:'ready',stage:artifactReady?'evaluated':'review',
    progress:1,revision:4,objective:'classification',selection:['c1'],report,feature_set_id:artifactReady?'set-1':null,
    artifact:artifactReady?{artifact_id:'real-id',sha256:artifact}:null,
    evaluation:artifactReady?{evaluation_id:'ev1'}:null},
   autoSupervision:{eligible:true,issues:[],label_fields:[{field_id:'label',name:'NG_FLAG',role:'label'}],
      prediction_time_fields:[{field_id:'dec',name:'decision_at',role:'prediction_time'}],
      label_available_fields:[{field_id:'lab_at',name:'NG_known_at',role:'available_at'}]}};
}

test('supervised task configuration is offered only in review and requires human checkbox',()=>{
  const html=render(state(),{e:escapeHtml,u:encodeURIComponent});
  assert.match(html,/启用模型对照评价（可选）/);
  assert.match(html,/name="label_field_id"/);
  assert.match(html,/name="label_available_at_field_id"/);
  assert.match(html,/name="prediction_time_field_id"/);
  assert.match(html,/name="enable_supervised"/);
  assert.match(html,/我已核实/);
  assert.doesNotMatch(html,/name="enable_supervised"[^>]*checked/);
});

test('same artifact evaluation and BI evidence are shown without promotion to production QA',()=>{
  const s=state(true);
  s.autoDelivery={status:'app_evidence_linked',artifact:{artifact_id:'real-id',sha256:artifact},
      evaluation:{mode:'supervised',artifact_sha256:artifact,summary:[]},
      same_artifact_evaluation:true,same_artifact_bi_receipt:true,production_verified:false,limitations:[]};
  const html=render(s,{e:escapeHtml,u:encodeURIComponent});
  assert.match(html,/已完成绑定同一 Parquet 产物的监督模型评价/);
  assert.match(html,/同源校验通过/);
  assert.match(html,/data-feature-v2="auto-export-evidence"/);
  assert.match(html,/客户端双面板回执已记录/);
  assert.match(html,/不能替代独立浏览器验收/);
  assert.doesNotMatch(html,/name="enable_supervised"/);
  assert.doesNotMatch(html,/自动发布成功/);
});

test('BI entry must validate source and artifact with server and post panel receipt',()=>{
 const js=load('feature-shell.js');
 assert.match(js,/auto_sid/);
 assert.match(js,/analysis\/sessions\/.*delivery/);
 assert.match(js,/proof\.artifact\.sha256/);
 assert.match(js,/captureReceipt\(\)/);
 assert.match(js,/viewer-receipt/);
 assert.match(js,/auto-export-evidence/);
 assert.match(js,/report_sha256/);
 assert.match(js,/s\.autoSupervision\?\.eligible/);
});
