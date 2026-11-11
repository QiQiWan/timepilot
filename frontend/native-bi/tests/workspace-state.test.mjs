import test from 'node:test';
import assert from 'node:assert/strict';
import {rebindWorkspaceBase} from '../src/workspace-state.mjs';

test('only root bindings change; derived source and panel survive refresh', () => {
  const input = {
    version: '5',
    panels: {raw: {table: 'old_root', plugin: 'Datagrid'},
      chart: {table: 'Region Totals', plugin: 'Y Bar'}},
    derived_tables: {'Region Totals': {table: 'old_root', group_by: ['region']},
      'Subregion': {table: 'Region Totals', group_by: ['subregion']}},
    layout: {type: 'split-layout', children: ['raw', 'chart']},
    palette: {'--psp-user--color-1': '#345678'},
  };
  const result = rebindWorkspaceBase(input, 'old_root', 'timepilot_active');
  assert.equal(result.panels.raw.table, 'timepilot_active');
  assert.equal(result.panels.chart.table, 'Region Totals');
  assert.equal(result.derived_tables['Region Totals'].table, 'timepilot_active');
  assert.equal(result.derived_tables.Subregion.table, 'Region Totals');
  assert.deepEqual(result.layout, input.layout);
  assert.deepEqual(input.panels.raw.table, 'old_root');
});

test('rejects orphaned dependencies rather than loading empty chart', () => {
  assert.throws(() => rebindWorkspaceBase({panels: {p: {table: 'other_asset'}}},
    'timepilot_active', 'new_root'), /未知数据源/);
});

test('rebind is idempotent when new root equals old root', () => {
  const input = {panels: {grid: {table: 'timepilot_active', plugin: 'Datagrid'}},
    derived_tables: {}, layout: {type: 'tab-layout', tabs: ['grid']}};
  const out = rebindWorkspaceBase(input, 'timepilot_active', 'timepilot_active');
  assert.deepEqual(out, input);
  assert.notStrictEqual(out, input);
});

test('rebind safely rejects absent panels and unknown derived graph sources', () => {
  assert.throws(() => rebindWorkspaceBase({panels: {}}, 'old', 'new'), /缺少面板配置/);
  const invalid = {panels: {p: {table:'derived'}},
    derived_tables: {derived: {table:'foreign_asset'}}};
  assert.throws(() => rebindWorkspaceBase(invalid, 'timepilot_active', 'next_version'), /未知数据源/);
});
