import test from 'node:test';
import assert from 'node:assert/strict';
import {analyticalChartConfig,gridConfig} from '../src/viewer-contract.mjs';
const schema={Time:'integer',A1:'float',A5:'float',Batch:'string',maintenance_marker:'float'};
test('v0.23 native line and scatter charts bind real columns and table',()=>{
  const line=analyticalChartConfig({type:'line',x:'Time',y:['A1','A5']},schema,'tp-asset');
  assert.equal(line.plugin,'Y Line');assert.equal(line.table,'tp-asset');
  assert.deepEqual(line.group_by,['Time']);assert.deepEqual(line.columns,['A1','A5']);
  const scatter=analyticalChartConfig({type:'scatter',x:'A1',y:['A5'],color:'Batch'},schema,'tp-asset');
  assert.equal(scatter.plugin,'X/Y Scatter');assert.deepEqual(scatter.columns,['A1','A5']);
  assert.deepEqual(scatter.split_by,['Batch']);
});
test('v0.23 view config rejects column inventions, histogram masquerade, and empty axes',()=>{
  assert.throws(()=>analyticalChartConfig({type:'line',x:'Time',y:['nonexistent']},schema,'t'),/字段/);
  assert.throws(()=>analyticalChartConfig({type:'histogram',x:'Time',y:['A1']},schema,'t'),/分箱/);
  assert.throws(()=>analyticalChartConfig({type:'line',x:'Time',y:[]},schema,'t'),/Y 轴/);
  assert.deepEqual(gridConfig(schema,'t').columns,Object.keys(schema));
});
test('v0.23 industrial maintenance overlay uses actual derived column',()=>{
  const conf=analyticalChartConfig({type:'line',x:'Time',y:['A1','maintenance_marker']},schema,'t');
  assert(conf.columns.includes('maintenance_marker'));
});
