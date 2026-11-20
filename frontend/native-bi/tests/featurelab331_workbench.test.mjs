import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../../../src/timepilot/static/feature-workbench-ui.js',import.meta.url),'utf8');
const shell=readFileSync(new URL('../../../src/timepilot/static/feature-shell.js',import.meta.url),'utf8');
const bundle=readFileSync(new URL('../../../src/timepilot/static/index.html',import.meta.url),'utf8');
const sandbox={window:{}};
vm.runInNewContext(source,sandbox,{filename:'feature-workbench-ui.js'});
const view=sandbox.window.TimePilotFeatureViews.analysis;
const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const helper={e,u:encodeURIComponent};
const sourceData={assets:[{native_asset_id:'asset1',asset:{name:'machine.csv'}}],pid:'testproj',asset:'asset1',version:'base',
 contract:{state:'confirmed',revision:2,fields:[{field_id:'f1'},{field_id:'f2'}]},autoObjective:'explore',autoSession:null,
 uiFocused:'featureA',uiChartMode:'distribution',uiFilter:'all',uiDraftSelection:['featureA'],uiChartStatus:null};
const candidate={candidate_id:'featureA',display_name:'设备压力均值',output_name:'avg_pressure',operator_id:'rolling.mean',
 status:'recommended',quality:{null_rate:0,mean:10.5,std:2.5,finite_count:4,rows_scanned:4}};
const state={session_id:'session1',source_sha256:'a'.repeat(64),status:'ready',stage:'review',selection:['featureA'],
 report:{quality_rows:[candidate],candidate_count:1,recommended_count:1,review_count:0,excluded_count:0}};
function render(overrides={}, session=state){return view({...sourceData,autoSession:session,...overrides},helper);}
test('v0.33.0 empty, loading, failed and confirmation states are visible',()=>{
 assert.match(view({...sourceData},helper),/尚未开始特征分析/);
 assert.match(view({...sourceData,assets:[]},helper),/尚未开始特征分析/);
 assert.match(render({}, {...state,status:'profiling',progress:0.38,report:null}),/正在执行/);
 assert.match(render({}, {...state,status:'needs_input',warnings:['需要设备字段'],report:null}),/需要确认数据/);
 assert.match(render({}, {...state,status:'failed',error:'文件读取失败',report:null}),/文件读取失败/);
 assert.match(render({}, {...state,stale:true}),/来源已变化/);
 assert.match(render(),/等待审核/);
 assert.match(render({}, {...state,stage:'evaluated',artifact:{artifact_id:'ar1'},feature_set_id:'s1'}),/特征已保存并完成计算/);
});
test('v0.33.0 goal selection, review controls, evidence and source-bound actions',()=>{
 const html=render();
 for(const val of ['explore','anomaly','quality','classification','regression','forecast'])assert.match(html,new RegExp('value="'+val+'"'));
 for(const filter of ['all','recommended','review','exclude','selected'])assert.match(html,new RegExp('data-filter="'+filter+'"'));
 assert.match(html,/tp-fl2-auto-save/);
 assert.match(html,/confirm_materialize/);
 assert.match(html,/tp-fl2-auto-select/);
 assert.match(html,/尚未验证/);
 assert.match(html,/通过质量检查不代表能提高预测效果/);
 assert.doesNotMatch(html,/虚构模型提升/);
});
test('v0.33.1 chart reads calculated series and preserves selected feature and filter',()=>{
 const points=[{label:'0–10',count:1},{label:'10–20',count:3}];
 const payload={status:'ready',mode:'distribution',points,quality:{rows_scanned:4,finite_count:4},source_sha256:'a'.repeat(64),
   candidate_fingerprint:'b'.repeat(64),contract_revision:2,version_id:'base',candidate_id:'featureA',feature_name:'设备压力均值',available_modes:['distribution','series','group']};
 const html=render({uiChart:payload,uiFilter:'recommended'});
 assert.match(html,/aria-label="特征全量分布/);
 assert.match(html,/源数据 SHA256/);
 assert.match(html,/data-cid="featureA"/);
 assert.match(html,/class="active"/);
 assert.match(html,/实测有效记录 4 \/ 4/);
 assert.match(html,/svg role="img"/);
 assert.doesNotMatch(render({uiChart:{...payload,source_sha256:'different'}}),/aria-label="特征全量分布/);
 const group=render({uiChartMode:'group',uiChart:{...payload,mode:'group',points:[{label:'Machine-A',count:3,mean:12.5}]}});
 assert.match(group,/Machine-A/);
 const category=render({uiChartMode:'category',uiChart:{...payload,mode:'category',points:[{label:'A班',count:3,mean:1}]}});
 assert.match(category,/A班/);
 const temporal=render({uiChartMode:'series',uiChart:{...payload,mode:'series',points:[{x:'1',value:10,original:8},{x:'2',value:12,original:14}]}});
 assert.match(temporal,/2 个时间桶/);
 assert.match(temporal,/polyline/);
});
test('v0.33.1 navigation state, version selection and mounted frontend assets are wired',()=>{
 assert.match(shell,/uri\.searchParams\.set\('candidate'/);
 assert.match(shell,/uri\.searchParams\.set\('filter'/);
 assert.match(shell,/uri\.searchParams\.set\('chart'/);
 assert.match(shell,/query\.get\('candidate'\)/);
 assert.match(shell,/data-feature-v2-change="select-version"/);
 assert.match(shell,/function resetAssetState/);
 assert.match(shell,/chartGeneration\+\+/);
 assert.match(shell,/chart\?mode=/);
 assert.match(bundle,/feature-workbench-ui\.js/);
 const version=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8')).version;
 assert.ok(bundle.includes(`TimePilot v${version}`));
});
