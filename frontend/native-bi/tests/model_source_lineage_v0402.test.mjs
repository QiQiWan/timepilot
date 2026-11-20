import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../../../src/timepilot/static/modeling-ui.js', import.meta.url), 'utf8');

test('temporal budget is explicitly selectable and passed in the approved execution request', () => {
  assert.match(src, /data-testid="temporal-budget-profile"/);
  assert.match(src, /name="temporal_budget_profile"/);
  assert.match(src, /budget_profile:temporalBudgetProfile/);
  assert.match(src, /\['5m','15m','30m'\]\.includes\(temporalBudgetProfile\)/);
});

test('device OOD preflight is displayed with validity and per-fold class counts', () => {
  assert.match(src, /data-testid="device-ood-fold-preflight"/);
  assert.match(src, /positive_train/);
  assert.match(src, /positive_validation/);
});

test('prediction lineage fetch uses project and task bound execution endpoint and escapes output', () => {
  assert.match(src, /data-testid="model-prediction-lineage"/);
  assert.match(src, /executionEndpoint\(\)\+'\/predictions\/lineage\?row_id='\+U\(value\)/);
  assert.match(src, /E\(x\.selected_row_id\)/);
  assert.match(src, /savedLineage\.status/);
});

test('bounded worker logs are shown only from run-step evidence and escaped', () => {
  assert.match(src, /data-testid="temporal-worker-logs"/);
  assert.match(src, /E\(x\.log_sha256\)/);
  assert.match(src, /E\(x\.tail\)/);
});
