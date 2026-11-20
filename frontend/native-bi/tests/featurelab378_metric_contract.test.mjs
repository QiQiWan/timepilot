import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const text = readFileSync(new URL('../../../src/timepilot/static/data-dashboard-v0372.js', import.meta.url), 'utf8');
test('Dashboard uses mutually exclusive invalid counters and a scoped denominator', () => {
  for (const keyword of ['metric.non_finite_numeric_cells', 'metric.conversion_failed_cells',
    'metric.invalid_numeric_cells', 'metric.metric_scope?.denominator_cells',
    'metric.metric_scope?.field_ids', 'metric.missing_percent']) {
    assert.ok(text.includes(keyword), keyword);
  }
});
test('Correlations keep a symmetric field matrix and failed source versions do not update UI', () => {
  assert.ok(text.includes('const pairMap=new Map'));
  assert.ok(text.includes('if(state!==z||epoch!==z.queryEpoch)return'));
  assert.ok(text.includes('if(response.scope_sha256!==z.report?.scope_sha256)'));
});
