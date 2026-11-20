import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,join} from 'node:path';
import vm from 'node:vm';
import {test} from 'node:test';
const root=resolve(import.meta.dirname,'../../..');
const script=name=>readFileSync(join(root,'src/timepilot/static',name),'utf8');
const context={window:{TimePilotFeatureViews:{}},console};
vm.createContext(context);
vm.runInContext(script('feature-auto-ui.js'),context);
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const render=context.window.TimePilotFeatureViews.analysis;
const base={asset:'a',assets:[{native_asset_id:'a',asset:{name:'production.csv'}}],contract:{fields:[]},autoGoal:'分析压力',autoObjective:'explore'};
const state={...base,autoSession:{session_id:'s',status:'ready',stage:'review',revision:2,progress:0.7,selection:['cand1'],report:{row_count:100,field_count:3,candidate_count:1,evaluated_count:1,recommended_count:1,quality_rows:[{candidate_id:'cand1',output_name:'pressure_abs',status:'recommended',quality:{null_rate:0,std:1,rows_scanned:100},reason:'full quality evidence'}],topology:{safe_for_windows:false,reasons:['无法证明设备分组']},intent_advice:{objective:'explore',confidence:0.84},advanced_options:[]}}};
test('default UI has a single explicit confirm-and-run, not hidden auto publication',()=>{
  const html=render(state,{e:escapeHtml,u:encodeURIComponent});
  assert.match(html,/tp-fl2-auto-save/);
  assert.match(html,/name="confirm_materialize"/);
  assert.match(html,/确认采用并计算/);
  assert.match(html,/数据结构识别/);
  assert.match(html,/无法证明设备分组/);
  assert.doesNotMatch(html,/自动发布成功/);
  assert.match(script('feature-shell.js'),/accept-and-run/);
  assert.match(script('feature-shell.js'),/confirm_materialize/);
});
test('the UI labels predictive model feedback as unverified for production',()=>{
  const sig='a'.repeat(64);
  const html=render({...state,autoDelivery:{same_artifact_evaluation:true,evaluation:{mode:'supervised'},artifact:{sha256:sig}},autoSession:{...state.autoSession,stage:'evaluated',feature_set_id:'set1',selection:['cand1'],model_feedback:{artifact_sha256:sig,practical_gain_candidate:true,predictive_gain_verified:false,selection_frequency:[{feature:'pressure_abs',selected_in_folds:2,total_folds:3}],comparison:[]}}},{e:escapeHtml,u:encodeURIComponent});
  assert.match(html,/训练折/);
  assert.match(html,/生产验证/);
});

test('forged or stale model feedback is hidden until artifact SHA is proven',()=>{
  const html=render({...state,autoSession:{...state.autoSession,stage:'evaluated',model_feedback:{artifact_sha256:'f'.repeat(64),practical_gain_candidate:true}}},{e:escapeHtml,u:encodeURIComponent});
  assert.doesNotMatch(html,/模型贡献证据/);
});
