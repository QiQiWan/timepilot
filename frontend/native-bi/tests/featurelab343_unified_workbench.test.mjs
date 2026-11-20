import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read=name=>readFileSync(new URL('../../../src/timepilot/static/'+name,import.meta.url),'utf8');
const root={window:{TimePilotFeatureAdvanced:{render(){return '<section data-testid="inline-advanced">PIT form</section>';}}}};
vm.runInNewContext(read('feature-workbench-ui.js'),root,{filename:'feature-workbench-ui.js'});
const view=root.window.TimePilotFeatureViews.analysis;
const escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const h={e:escape,u:encodeURIComponent};
const sha='a'.repeat(64),feat='b'.repeat(64);
function state(over={}){
 const base={candidate_id:'c1',operator_id:'rolling.std',display_name:'A2 · 波动',output_name:'a2_std',
   status:'recommended',quality:{null_rate:0,mean:3,std:1,finite_count:30,rows_scanned:30}};
 const changed={candidate_id:'c2',operator_id:'difference.rows',display_name:'A5 · 差值',output_name:'a5_diff',
   status:'recommended',quality:{null_rate:0,mean:0,std:2,finite_count:30,rows_scanned:30}};
 return {pid:'p',asset:'a',version:'base',assets:[{native_asset_id:'a',asset:{name:'sensors.csv'}}],
  contract:{revision:3,state:'confirmed',fields:[{field_id:'a',source_name:'A2'}]},
  uiFocused:'c1',uiFilter:'all',uiFamily:'all',uiAdvancedOpen:false,
  candidateSession:{candidates:[{...base,family:'anomaly',input_field_ids:['a'],reason:'滚动波动'},
     {...changed,family:'history',input_field_ids:['b'],reason:'历史差值'}],
    strategy_plan:{budget:16, family_counts:{anomaly:1,history:1},
       blocked:[{family:'duration',reason:'真实时间尚未确认'}]}},
  advancedCapabilities:{engines:{tsfresh:true,featuretools:false}},
  advancedBatches:{candidate_count:1,batches:[{batch_id:'adv_job',job_id:'job',engine:'tsfresh',
     rows:22,candidate_count:1,status:'review_required',native_asset_id:'p1',
     source_fingerprint:sha,artifact_sha256:feat,recipe_sha256:feat,
     candidates:[{candidate_id:'adv_1',batch_id:'adv_job',display_name:'fft_1',engine:'tsfresh'}]}]},
  autoSession:{session_id:'sid',revision:3,status:'ready',stage:'review',source_sha256:sha,
    selection:['c1'],report:{quality_rows:[base,changed],candidate_count:2,recommended_count:2,review_count:0,excluded_count:0}},
  uiDraftSelection:['c1'],...over};
}
test('automatic strategy and multiple operator families are visible, without claim of predictive gain',()=>{
 const html=view(state(),h);
 assert.match(html,/本次自动特征策略/);
 assert.match(html,/异常与波动/);
 assert.match(html,/历史变化/);
 assert.match(html,/真实时间尚未确认/);
 assert.match(html,/模型贡献|训练折选择证据/);
 assert.match(html,/预测收益|预测效果/);
 assert.match(html,/高级候选/);
 assert.match(html,/data-family="anomaly"/);
 assert.match(html,/data-feature-v2="wb-advanced-focus"/);
 assert.match(html,/data-feature-v2="wb-advanced-panel"/);
 assert.match(html,/tsfresh|时序统计/);
});
test('family filter shows only selected family without toggling hidden checkbox values',()=>{
 const html=view(state({uiFamily:'history',uiFocused:'c2'}),h);
 assert.match(html,/A5 · 差值/);
 assert.doesNotMatch(html,/data-cid="c1"/);
 assert.match(html,/1 项已选/);
 assert.match(html,/data-family="history" class="active"/);
});
test('advanced metadata uses separate review state and never becomes an automatic checkbox',()=>{
 const html=view(state({uiAdvancedFocus:'adv_1',uiFocused:null}),h);
 assert.match(html,/data-testid="advanced-feature-detail"/);
 assert.match(html,/需确认预测实例主键|下一步需确认预测实例主键/);
 assert.match(html,/data-feature-v2="wb-advanced-focus"/);
 assert.match(html,/进入模型评价/);
 assert.doesNotMatch(html,/name="candidate_id"[^>]*value="adv_1"/);
});
test('inline advanced control displays gated execution interface only when requested',()=>{
 const closed=view(state(),h);
 const opened=view(state({uiAdvancedOpen:true}),h);
 assert.doesNotMatch(closed,/inline-advanced/);
 assert.match(opened,/inline-advanced/);
 assert.match(opened,/aria-expanded="true"/);
});
test('all user supplied feature and advanced batch values are HTML escaped',()=>{
 const s=state();s.advancedBatches.batches[0].candidates[0].display_name='<img src=x onerror=alert(1)>';
 const html=view(s,h);
 assert.doesNotMatch(html,/<img src=x/);
 assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
});
test('controller routes advanced focus and family filters through URL, without changing checkbox action',()=>{
 const code=read('feature-shell.js');
 assert.match(code,/wb-advanced-focus/);
 assert.match(code,/wb-family/);
 assert.match(code,/advanced_candidate/);
 assert.match(code,/family/);
 assert.match(code,/wb-choice/);
 assert.match(code,/wb-advanced-panel/);
 assert.match(code,/advanced-batches/);
});
