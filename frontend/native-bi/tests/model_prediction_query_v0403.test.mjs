import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const ui=fs.readFileSync(new URL('../../../src/timepilot/static/modeling-ui.js', import.meta.url),'utf8');
const api=fs.readFileSync(new URL('../../../src/timepilot/model_task_api.py', import.meta.url),'utf8');

test('query and prediction trace share the approved project/task execution scope',()=>{
  assert.match(ui, /executionEndpoint\(\)\+'\/predictions\/query\?'/);
  assert.match(ui, /executionEndpoint\(\)\+'\/predictions\/lineage\?row_id='/);
  assert.match(ui, /data-testid="model-prediction-query-controls"/);
  assert.match(ui, /data-testid="model-prediction-query-result"/);
  assert.match(api, /execution\/predictions\/query/);
});
test('selected prediction row highlights and preserves the native Perspective mount',()=>{
  assert.match(ui, /btn\('select-prediction-row'/);
  assert.match(ui, /data-prediction-row-id/);
  assert.match(ui, /tracePredictionRow\(value\)/);
  assert.match(ui, /Preserve the mounted Perspective component/);
  assert.match(ui, /mountPerspectiveRemote/);
});
test('historical window edge query provides clear scope of evidence',()=>{
  assert.match(ui, /trace-history-window/);
  assert.match(ui, /historical_observation_membership_certified|历史观测/);
  assert.match(api, /execution\/predictions\/window-lineage/);
});
test('query result metrics are distinguished from model-selection scores',()=>{
  assert.match(ui, /metric_scope/);
  assert.match(ui, /逐行汇总，不直接作为模型选择分数/);
});
