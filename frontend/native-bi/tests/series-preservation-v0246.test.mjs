import test from 'node:test';
import assert from 'node:assert/strict';
import {compatibleSeries,createSeriesPreserver} from '../src/series-preservation.mjs';

function mockViewer(){
  const listeners=new Map();const work={panels:{main:{plugin:'Y Line',table:'source',columns:['A1','A3','A14'],group_by:['Time'],filter:[['A1','>',2]],split_by:[]},other:{plugin:'Datagrid',table:'source',columns:['Time','A2']}}};
  return {
    work,restores:[],addEventListener(name,cb){listeners.set(name,cb)},removeEventListener(name){listeners.delete(name)},
    async saveWorkspace(){return structuredClone(work)},async save({panel}){return structuredClone(work.panels[panel])},
    async restore(patch,{panel}){this.restores.push({patch,panel});Object.assign(work.panels[panel],patch);this.emit('perspective-config-update');},
    emit(name){listeners.get(name)?.()}
  };
}
test('switching chart plugin restores selected series, keeps filters, layout and other panels',async()=>{
  const viewer=mockViewer();const notices=[];
  const keeper=createSeriesPreserver(viewer,{Time:'integer',A1:'float',A2:'float',A3:'float',A14:'float'},{onNotice:x=>notices.push(x)});
  await keeper.initialize();viewer.work.panels.main.plugin='Y Bar';viewer.work.panels.main.columns=['A1'];
  await keeper.flush();
  assert.deepEqual(viewer.work.panels.main.columns,['A1','A3','A14']);
  assert.deepEqual(viewer.work.panels.main.filter,[['A1','>',2]]);
  assert.deepEqual(viewer.work.panels.other.columns,['Time','A2']);
  assert.equal(viewer.restores[0].panel,'main');keeper.dispose();
});
test('user edits without plugin change are authoritative, and unsupported series are ignored',async()=>{
  const viewer=mockViewer();const keeper=createSeriesPreserver(viewer,{Time:'datetime',A1:'float',A2:'float',A3:'float',A14:'float'});
  await keeper.initialize();viewer.work.panels.main.columns=['A2','A3'];await keeper.flush();
  viewer.work.panels.main.plugin='X/Y Scatter';viewer.work.panels.main.columns=['A1','A14'];await keeper.flush();
  assert.deepEqual(viewer.work.panels.main.columns,['A2','A3']);
  viewer.work.panels.main.plugin='Datagrid';viewer.work.panels.main.columns=['A1'];await keeper.flush();
  assert.deepEqual(viewer.work.panels.main.columns,['A2','A3']);
  keeper.dispose();
});
test('filter invalid and reserved columns, avoid invalid scatter configuration',()=>{
  assert.deepEqual(compatibleSeries(['Time','A2','A2','__tp_rowid'],'Y Line',{Time:'datetime',A2:'float',__tp_rowid:'integer'}),['A2']);
  assert.deepEqual(compatibleSeries(['A2'],'X/Y Scatter',{A2:'float'}),[]);
});

test('separate plugin and default-series events preserve user choice',async()=>{
  const viewer=mockViewer();const keeper=createSeriesPreserver(viewer,{Time:'integer',A1:'float',A3:'float',A14:'float'});
  await keeper.initialize();
  viewer.work.panels.main.plugin='Y Area';await keeper.flush();
  viewer.work.panels.main.columns=['A1'];await keeper.flush();
  assert.deepEqual(viewer.work.panels.main.columns,['A1','A3','A14']);
  keeper.dispose();
});
