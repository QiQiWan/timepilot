import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const shell=readFileSync(new URL('../../../src/timepilot/static/feature-shell.js',import.meta.url),'utf8');
const catalog=readFileSync(new URL('../../../src/timepilot/static/feature-catalog-ui.js',import.meta.url),'utf8');
const automatic=readFileSync(new URL('../../../src/timepilot/static/feature-auto-ui.js',import.meta.url),'utf8');

test('manual window editor requires confirmed time/sequence and entity grouping',()=>{
 assert.match(shell,/role==='order_index'&&f\.confirmation==='confirmed'/);
 assert.match(shell,/parameters\.partition_field_ids=\(c\.entity_field_ids\|\|\[\]\)\.slice\(\)/);
 assert.match(shell,/没有可靠顺序时不能手工添加窗口特征/);
 assert.match(shell,/设备\/批次分组字段尚未确认/);
});

test('catalog renders signed read-only legacy audit and risk warning',()=>{
 assert.match(shell,/\/sets\/temporal-audits\?asset_id=/);
 assert.match(shell,/\/temporal-audit'/);
 assert.match(catalog,/risk\(r\)\?\.risk==='high'/);
 assert.match(catalog,/发布将阻断/);
});

test('automatic task gives a concrete evidence-derived next action',()=>{
 assert.match(automatic,/const guidedAction=action\?/);
 assert.match(automatic,/下一步：/);
 assert.match(automatic,/系统不会自动审批或发布/);
});
