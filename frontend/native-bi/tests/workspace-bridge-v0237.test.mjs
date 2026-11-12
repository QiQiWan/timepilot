import {test} from 'node:test';
import assert from 'node:assert/strict';
import {compileAgentViewerState,ensureSameTable} from '../src/workspace-bridge.mjs';
const schema={Time:'integer',A1:'float',A2:'float',ID:'string',__tp_rowid:'integer'};
const base={query:{columns:[],filters:[],group_by:[],metrics:[],order_by:[],limit:300,offset:0},chart:{type:'grid',x:null,y:[],color:null},expressions:[],window:null,bucket:null,join:null,industrial:null,dashboard:[]};
test('full source config preserves table, filters and column names',()=>{
 const state=structuredClone(base);state.query.filters=[{column:'A1',op:'gt',value:0.4}];
 state.query.order_by=[{field:'Time',descending:false}];
 const config=compileAgentViewerState(state,schema,'main.timepilot_active');
 assert.equal(config.table,'main.timepilot_active');
 assert.deepEqual(config.filter,[['A1','>',0.4]]);assert.deepEqual(config.sort,[['Time','asc']]);
 assert.equal(config.plugin,'Datagrid');assert.equal(config.columns.includes('__tp_rowid'),false);
});
test('reject unknown schema, virtual join and unsanctioned table changes',()=>{
 const bad=structuredClone(base);bad.query.filters=[{column:'A99',op:'eq',value:'hello'}];
 assert.throws(()=>compileAgentViewerState(bad,schema,'main.timepilot_active'),/字段/);
 const join=structuredClone(base);join.join={right_asset_id:'stranger'};
 assert.throws(()=>compileAgentViewerState(join,schema,'main.timepilot_active'),/派生表/);
 const before={panels:{main:{table:'main.timepilot_active'}}};
 assert.throws(()=>ensureSameTable(before,{panels:{main:{table:'x.private'}}},'main.timepilot_active'),/数据源/);
});
test('build chart on exact source without replacing data',()=>{
 const state=structuredClone(base);state.chart={type:'line',x:'Time',y:['A1','A2'],color:null};
 const config=compileAgentViewerState(state,schema,'main.timepilot_active');
 assert.equal(config.plugin,'Y Line');assert.deepEqual(config.group_by,['Time']);
 assert.deepEqual(config.columns,['A1','A2']);
});
