import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read=n=>readFileSync(new URL('../../../src/timepilot/static/'+n,import.meta.url),'utf8');
const sandbox={window:{}};
vm.runInNewContext(read('feature-workbench-ui.js'),sandbox);
const view=sandbox.window.TimePilotFeatureViews.analysis;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const h={e:esc,u:encodeURIComponent};
const candidate={candidate_id:'c1',display_name:'组合特征',output_name:'score',operator_id:'arithmetic.binary',status:'recommended',quality:{null_rate:0,finite_count:100,rows_scanned:100}};
const session={session_id:'sid',source_sha256:'a'.repeat(64),status:'ready',stage:'review',revision:1,selection:['c1'],report:{quality_rows:[candidate],candidate_count:1,recommended_count:1,review_count:0,excluded_count:0}};
const state={assets:[{native_asset_id:'a',asset:{name:'fixed.csv'}}],pid:'pid',asset:'a',version:'base',contract:{state:'confirmed',revision:2,fields:[{field_id:'f1'}]},autoSession:session,uiFilter:'all',uiDraftSelection:['c1']};
const label={field_id:'fld_target',name:'target',role:'label'};
const at={field_id:'fld_prediction',name:'decision_at',role:'prediction_time'};
const av={field_id:'fld_label_at',name:'label_at',role:'available_at'};
function draw(supervision,extra={}){return view({...state,autoSupervision:supervision,...extra},h);}

test('v0.33.2 static regression appears in real workbench form and requires explicit attestation',()=>{
 const html=draw({static_eligible:true,temporal_eligible:false,task_type:'regression',label_fields:[label],static_default_split:'kfold',entity_field_ids:[]});
 assert.match(html,/data-testid="supervised-evaluation"/);
 assert.match(html,/name="enable_supervised"/);
 assert.match(html,/name="static_independence_confirmed"/);
 assert.match(html,/name="label_field_id"/);
 assert.match(html,/value="kfold" selected/);
 assert.doesNotMatch(html,/value="stratified"/);
 assert.doesNotMatch(html,/name="prediction_time_field_id"/);
 assert.match(html,/未勾选时只生成特征数据/);
});
test('v0.33.2 binary supervised uses stratification and grouped entities cannot be randomized',()=>{
 const html=draw({static_eligible:true,temporal_eligible:false,task_type:'binary_classification',label_fields:[label],static_default_split:'stratified',entity_field_ids:[]});
 assert.match(html,/value="stratified" selected/);
 assert.match(html,/value="kfold"/);
 const group=draw({static_eligible:true,temporal_eligible:false,task_type:'binary_classification',label_fields:[label],static_default_split:'group',entity_field_ids:['fld_device']});
 assert.match(group,/value="group" selected/);
 assert.doesNotMatch(group,/value="kfold"/);
 assert.doesNotMatch(group,/value="stratified"/);
});
test('v0.33.2 timed supervision binds distinct predictive and label availability fields',()=>{
 const html=draw({static_eligible:false,temporal_eligible:true,task_type:'regression',label_fields:[label],prediction_time_fields:[at],label_available_fields:[av],entity_field_ids:['fld_device']});
 assert.match(html,/value="time" selected/);
 assert.match(html,/name="prediction_time_field_id"/);
 assert.match(html,/name="label_available_at_field_id"/);
 assert.match(html,/name="label_field_id"/);
 assert.doesNotMatch(html,/name="static_independence_confirmed"/);
});
test('v0.33.2 unavailable supervision does not promise predictive evidence',()=>{
 const html=draw({static_eligible:false,temporal_eligible:false,label_fields:[]});
 assert.match(html,/当前尚无可执行的监督评价契约/);
 assert.doesNotMatch(html,/name="enable_supervised"/);
});
test('v0.33.2 paired effect and stability are visible only with verified same-artifact receipt',()=>{
 const ev={mode:'supervised',split_kind:'kfold',fold_count:3,selection_stability:{mean_jaccard:0.7},
  paired_comparison:{metrics:{mae:{n_paired_folds:3,mean_improvement:1.02}}}};
 const artifact={artifact_id:'z1',sha256:'4'.repeat(64)};
 const complete={...state.autoSession,feature_set_id:'s1',artifact};
 const proof={same_artifact_evaluation:true,evaluation:ev,artifact};
 const html=draw(null,{autoSession:complete,autoDelivery:proof});
 assert.match(html,/data-testid="verified-supervised-feedback"/);
 assert.match(html,/同折平均改善 1.02/);
 assert.match(html,/训练折特征选择一致性 Jaccard：0.7/);
 const bad=draw(null,{autoSession:complete,autoDelivery:{...proof,artifact:{sha256:'5'.repeat(64)}}});
 assert.doesNotMatch(bad,/data-testid="verified-supervised-feedback"/);
 assert.doesNotMatch(draw(null,{autoSession:complete,autoDelivery:{...proof,same_artifact_evaluation:false}}),/data-testid="verified-supervised-feedback"/);
});
