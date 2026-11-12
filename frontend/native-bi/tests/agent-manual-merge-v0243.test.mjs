import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mergeAgentPanelConfig} from '../src/workspace-bridge.mjs';
const live={table:'main.timepilot_active',plugin:'Datagrid',filter:[['A1','>',3]],
  sort:[['A2','desc']],columns:['A1','A2'],group_by:[],settings:false,title:'Manual'};
const desired={table:'main.timepilot_active',filter:[],sort:[],columns:['A2'],plugin:'Y Line',group_by:['Time'],settings:true};
test('chart changes preserve manual filters/sorts/settings',()=>{
 const actual=mergeAgentPanelConfig(live,desired,['bi.chart.create']);
 assert.deepEqual(actual.filter,live.filter);assert.deepEqual(actual.sort,live.sort);
 assert.equal(actual.settings,false);assert.equal(actual.plugin,'Y Line');assert.equal(actual.title,'Manual');
});
test('filters are additive unless explicitly cleared',()=>{
 const d={...desired,filter:[['A1','>',3],['A2','<',5]]};
 assert.equal(mergeAgentPanelConfig(live,d,['bi.filter.set']).filter.length,2);
 assert.deepEqual(mergeAgentPanelConfig(live,desired,['bi.filter.clear']).filter,[]);
});
test('source switch is blocked',()=>assert.throws(()=>mergeAgentPanelConfig(live,{...desired,table:'other'},['bi.chart.create']),/来源/));
