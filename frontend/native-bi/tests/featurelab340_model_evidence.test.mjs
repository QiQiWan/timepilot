import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const src=fs.readFileSync(new URL('../../../src/timepilot/static/feature-workbench-ui.js',import.meta.url),'utf8');
const root={window:{}};vm.runInNewContext(src,root);
const view=root.window.TimePilotFeatureViews.analysis;
const h={e:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),u:encodeURIComponent};
const source_sha='a'.repeat(64),artifact_sha='b'.repeat(64);
const candidate={candidate_id:'c1',output_name:'pressure_mean_10',display_name:'最近10次压力均值',
 status:'recommended',operator_id:'rolling.mean',quality:{null_rate:0,mean:12,std:2,finite_count:20,rows_scanned:20}};
function state(over={}){
  return {pid:'p',asset:'asset',version:'base',assets:[{native_asset_id:'asset',asset:{name:'data.csv'}}],
    contract:{state:'confirmed',revision:1,fields:[]},uiFocused:'c1',uiFilter:'all',
    autoDelivery:{same_artifact_evaluation:true,artifact:{sha256:artifact_sha},
        evaluation:{mode:'supervised',fold_count:3,paired_comparison:{metrics:{}}}},
    autoSession:{source_sha256:source_sha,revision:1,status:'ready',stage:'evaluated',
      artifact:{sha256:artifact_sha},report:{quality_rows:[candidate],candidate_count:1,recommended_count:1},
      model_feedback:{artifact_sha256:artifact_sha,selection_frequency:[{feature:'pressure_mean_10',
        selected_in_folds:2,total_folds:3}]}},...over};
}
test('only pinned same-artifact fold frequency is shown for one candidate',()=>{
  const text=view(state(),h);
  assert.match(text,/训练折选择证据/);
  assert.match(text,/2 \/ 3 折入选/);
  assert.match(text,/独立验证生产收益/);
});
test('source/artifact mismatch hides candidate predictive evidence',()=>{
 const s=state({autoDelivery:{same_artifact_evaluation:false,artifact:{sha256:'wrong'}}});
 assert.match(view(s,h),/尚未验证/);
 assert.doesNotMatch(view(s,h),/2 \/ 3 折入选/);
});
