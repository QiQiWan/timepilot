import test from 'node:test';
import assert from 'node:assert/strict';
import {gridConfig, presetConfig, loadNamedClient} from '../src/viewer-contract.mjs';

const schema = {__tp_rowid: 'integer', region: 'string', event_time: 'date', sales: 'float'};

test('Perspective 5 grid explicitly binds immutable named table', () => {
  const config = gridConfig(schema, 'timepilot_active');
  assert.equal(config.table, 'timepilot_active');
  assert.equal(config.plugin, 'Datagrid');
  assert(config.columns.includes('__tp_rowid'));
});

test('Perspective 5 pivot binds named table and numeric aggregate field', () => {
  const config = presetConfig('pivot', schema, 'timepilot_active');
  assert.equal(config.table, 'timepilot_active');
  assert.deepEqual(config.group_by, ['region']);
  assert.deepEqual(config.columns, ['sales']);
});

test('Perspective 5 chart and timeseries bind named table', () => {
  const bar = presetConfig('bar', schema, 'timepilot_active');
  const line = presetConfig('line', schema, 'timepilot_active');
  assert.equal(bar.plugin, 'Y Bar');
  assert.equal(line.plugin, 'Y Line');
  assert.deepEqual(line.group_by, ['event_time']);
  assert.deepEqual(line.columns, ['sales']);
});

test('refuses charts without numeric fields and incomplete table contract', () => {
  assert.throws(() => presetConfig('line', {region: 'string'}, 'tbl'), /数值字段/);
  assert.throws(() => gridConfig({}, 'tbl'), /为空/);
  assert.throws(() => gridConfig(schema, ''), /为空/);
});

test('v5 actually loads Client before restoring named table, not a Table proxy', async () => {
  const client = {id: 'perspective-client'};
  const table = {get_name: async () => 'timepilot_active', schema: async () => schema};
  const calls = [];
  const viewer = {
    load: async value => {calls.push(['load', value]);},
    restore: async value => {calls.push(['restore', value]);},
  };
  const result = await loadNamedClient(viewer, client, table);
  assert.equal(result.tableName, 'timepilot_active');
  assert.deepEqual(calls.map(x => x[0]), ['load', 'restore']);
  assert.strictEqual(calls[0][1], client);
  assert.equal(calls[1][1].table, 'timepilot_active');
  assert.equal(calls[1][1].plugin, 'Datagrid');
});

test('v5 Client binding surfaces WebSocket errors rather than silently reporting ready', async () => {
  const viewer = {load: async () => {throw new Error('Remote WebSocket open_table failed');}};
  const table = {get_name: async () => 'timepilot_active', schema: async () => schema};
  await assert.rejects(loadNamedClient(viewer, {id: 'client'}, table), /WebSocket/);
});
