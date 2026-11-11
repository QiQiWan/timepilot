import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveHostedTableName, openHostedTable} from '../src/hosted-table.mjs';

test('v5.6.1 DuckDB virtual server advertises catalog-qualified names', () => {
  assert.equal(resolveHostedTableName(['memory.timepilot_active'], 'timepilot_active'),
    'memory.timepilot_active');
});

test('streaming Perspective Server preserves its exact live table ID', () => {
  assert.equal(resolveHostedTableName(['tp_live_123'], 'tp_live_123'), 'tp_live_123');
});

test('exact names take precedence, but qualified missing names never cross catalogs', () => {
  assert.equal(resolveHostedTableName(['memory.x','x'], 'x'), 'x');
  assert.throws(() => resolveHostedTableName(['other.x'], 'memory.x'), /没有公布/);
});

test('rejects empty and ambiguous remote registries with actionable error', () => {
  assert.throws(() => resolveHostedTableName([], 'timepilot_active'), /数据表：\（空\）/);
  assert.throws(() => resolveHostedTableName(['a.x','b.x'], 'x'), /多个数据库中重名/);
  assert.throws(() => resolveHostedTableName('memory.x', 'x'), /列表格式错误/);
});

test('openHostedTable queries registry and opens only the registered ID', async () => {
  const calls = [];
  const client = {
    get_hosted_table_names: async () => {calls.push('names'); return ['memory.timepilot_active'];},
    open_table: async name => {calls.push('open:' + name); return {get_name: async () => name};},
  };
  const steps = [];
  const step = async (value, label, ms) => {steps.push([label, ms]); return value;};
  const result = await openHostedTable(client, 'timepilot_active', step);
  assert.deepEqual(calls, ['names', 'open:memory.timepilot_active']);
  assert.equal(await result.table.get_name(), 'memory.timepilot_active');
  assert.deepEqual(steps.map(x => x[1]), [10000, 12000]);
});

test('unknown table does not perform a speculative open_table call', async () => {
  let opened = false;
  const client = {
    get_hosted_table_names: async () => ['memory.other'],
    open_table: async () => {opened = true;},
  };
  await assert.rejects(openHostedTable(client, 'timepilot_active'), /没有公布/);
  assert.equal(opened, false);
});
