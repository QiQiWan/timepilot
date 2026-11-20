import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const dir=path.resolve(import.meta.dirname,'../../../src/timepilot/static');
const render={e:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),u:encodeURIComponent};
const ctx={window:{},console};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(dir,'feature-evaluation-ui.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(dir,'feature-workbench-ui.js'),'utf8'),ctx);

test('evaluation view renders a sourced unseen-device holdout, and includes protocol form controls',()=>{
  const html=ctx.window.TimePilotFeatureViews.evaluate({
    contract:{entity_field_ids:['fld_device'],fields:[{field_id:'fld_device',source_name:'device',role:'entity_key',confirmation:'confirmed',classification:'internal'}]},
    sets:[],evalResult:{report:{mode:'supervised',task_type:'regression',split_kind:'group',
      model_families:['linear'],folds:[{fold:0}],selection_frequency:[],limitations:[],
      industrial_transfer_holdout:{status:'measured',kind:'unseen_device',holdout_values:['D'],train_rows:270,
         holdout_rows:90,manifest_sha256:'f'.repeat(64),scope:'group_disjoint_only',
         model_metrics:{linear:{raw_only:{mae:3.2},with_features:{mae:0.4},selected_features:{mae:0.5}}},
         interpretation:'已锁定分组并独立测试'}}}},render);
  assert.match(html,/独立留出设备/);
  assert.match(html,/90 条/);
  assert.match(html,/3\.2000/);
  assert.match(html,/0\.4000/);
  assert.match(html,/group_disjoint_only|没有验证未来时间外推/);
  assert.match(html,/name="transfer_enabled"/);
  assert.match(html,/name="transfer_field"/);
  assert.match(html,/name="transfer_cutoff"/);
});

test('feature workbench declares mechanism group and hides unsupported holdout gains',()=>{
  const ui=fs.readFileSync(path.join(dir,'feature-workbench-ui.js'),'utf8');
  assert.match(ui,/机理与维护重置/);
  assert.match(ui,/industrial\.reset_mean/);
  assert.match(ui,/mechanism_reference/);
  assert.match(ui,/same_artifact_evaluation/);
  assert.match(ui,/data-testid="industrial-transfer-evidence"/);
});
