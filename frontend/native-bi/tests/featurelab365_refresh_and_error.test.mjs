import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const read = (name) => fs.readFileSync(new URL('../../../src/timepilot/static/'+name,import.meta.url),'utf8');

test('publishing resumes persisted batch on refresh without resubmission',()=>{
  const src=read('project-ui.js');
  assert.match(src,/const unfinished=entries\.find\(/);
  assert.match(src,/\['queued','running','prepared','partial_failed'\]/);
  assert.match(src,/showPublishBatch\(latest\.batch_id,true\)/);
  assert.match(src,/setInterval\(\(\)=>/);
});

test('failed upload retains actionable form-level alert',()=>{
  const src=read('project-ui.js');
  assert.match(src,/setAttribute\('role','alert'\)/);
  assert.match(src,/slot\.firstElementChild\.textContent=msg/);
  assert.match(src,/submitter\.disabled=false/);
});

test('auto data profiling is independently queued and available on refresh',()=>{
  const src=read('data-analysis-ui.js');
  assert.match(src,/z\.doc\.status==='not_started'&&autoStart/);
  assert.doesNotMatch(src,/not_started'&&autoStart&&z\.journey/);
  assert.match(src,/da_field|data-da="field"/);
});

test('workspace and report errors have recoverable UI and data provenance',()=>{
  const workspace=read('analysis-workspace.js');
  const delivery=read('analysis-delivery-ui.js');
  assert.match(workspace,/setAttribute\('aria-label','数据分析任务/);
  assert.match(delivery,/role="alert"/);
  assert.match(delivery,/返回数据管理重新选择/);
  assert.match(delivery,/href="\/logs"/);
  assert.match(delivery,/report_sha256/);
});
