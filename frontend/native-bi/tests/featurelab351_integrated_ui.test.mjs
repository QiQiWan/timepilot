import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const read=(f)=>fs.readFileSync(new URL('../../../src/timepilot/static/'+f,import.meta.url),'utf8');
const src=read('feature-workbench-ui.js');
const shell=read('feature-shell.js');
const data=read('data-analysis-ui.js');
const context={window:{}};vm.runInNewContext(src,context);
const render=context.window.TimePilotFeatureViews.analysis;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const h={e:esc,u:encodeURIComponent};
const sha='a'.repeat(64),artifact='b'.repeat(64);
function mock(over={}){
  return {pid:'proj',asset:'a',version:'base',assets:[{native_asset_id:'a',asset:{name:'line.csv'}}],
    contract:{revision:2,state:'confirmed',fields:[{field_id:'x',source_name:'x'}]},
    autoSession:{status:'ready',stage:'evaluated',source_sha256:sha,
      artifact:{sha256:artifact},report:{quality_rows:[],candidate_count:0,recommended_count:0}},
    autoDelivery:{same_artifact_evaluation:true,artifact:{sha256:artifact},
      evaluation:{mode:'supervised',fold_count:3,paired_comparison:{metrics:{}},feature_group_ablation:{groups:[
        {group:'window_stats',mean_loss_when_removed:0.15,evaluated_folds:3,positive_loss_folds:2}]}}},
    dataAnalysis:{status:'ready',row_count:1600,column_count:3,source_sha256:sha,contract_revision:2,
      findings:[{summary:'压力数据缺失 8.3%'}]},...over};
}

test('source-matched EDA diagnostics handoff into the visible feature workbench',()=>{
  const html=render(mock(),h);
  assert.match(html,/data-testid="source-diagnosis"/);
  assert.match(html,/压力数据缺失 8.3%/);
  assert.match(html,/\/projects\/proj\/analysis\?asset=a&version=base/);
});

test('changed source or contract revision never displays stale diagnostic summary',()=>{
  let s=mock({dataAnalysis:{...mock().dataAnalysis,source_sha256:'tampered'}});
  assert.doesNotMatch(render(s,h),/压力数据缺失 8.3%/);
  s=mock({dataAnalysis:{...mock().dataAnalysis,contract_revision:999}});
  assert.doesNotMatch(render(s,h),/压力数据缺失 8.3%/);
});

test('verified same-artifact group ablation is rendered with cautious wording',()=>{
  const html=render(mock(),h);
  assert.match(html,/特征组消融（同折验证）/);
  assert.match(html,/window_stats/);
  assert.match(html,/2 \/ 3 折模型表现变差/);
  assert.match(html,/不能证明因果贡献或上线收益/);
  const altered=mock({autoDelivery:{...mock().autoDelivery,same_artifact_evaluation:false}});
  assert.doesNotMatch(render(altered,h),/特征组消融（同折验证）/);
});

test('chart time semantics expose full span and never render missing extrema as zero',()=>{
  assert.match(data,/population_count/);
  assert.match(data,/safe\(p\.min\)/);
  assert.match(data,/data-da="entity"/);
  assert.match(data,/data-da="start"/);
  assert.match(data,/data-da="end"/);
  assert.match(data,/data-da="resolution"/);
  assert.match(shell,/data-analysis\?asset_id=/);
  assert.match(shell,/uiChartEntity/);
  assert.match(shell,/uiChartResolution/);
});
