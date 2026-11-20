import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {test} from 'node:test';

const root=path.resolve(import.meta.dirname,'../../..');
const source=fs.readFileSync(path.join(root,'src/timepilot/static/feature-auto-ui.js'),'utf8');
const context={window:{TimePilotFeatureViews:{}},console};
vm.createContext(context);
vm.runInContext(source,context,{filename:'feature-auto-ui.js'});
const render=context.window.TimePilotFeatureViews.analysis;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const base={pid:'p1',asset:'a1',assets:[{native_asset_id:'a1',asset:{name:'sensor.csv'}}],
 contract:{fields:[{source_name:'Time'},{source_name:'temp'}],source_sha256:'a'.repeat(64)},
 timeBasis:{fields:[]},autoObjective:'explore',autoGoal:'find anomalies'};
const report={row_count:100,field_count:2,candidate_count:2,evaluated_count:2,
 recommended_count:1,profile_scope:'full',high_missing_fields:[],limitations:[],
 quality_rows:[{candidate_id:'c1',output_name:'temp_mean5',display_name:'rolling mean',
 status:'recommended',reason:'full scan',operator_id:'rolling.mean',
 quality:{null_rate:0.02,rows_scanned:100,std:2}}]};

test('v0.32.1 shows automatic grounded explanation, agent provenance and trace',()=>{
 const state={...base,autoSession:{session_id:'s1',status:'ready',stage:'review',revision:5,
 progress:.7,selection:['c1'],report,
 agent:{mode:'llm',rejected_proposals:0,
   candidate_adjustment:{provider:'llm',reason:'coverage evidence'},trace:[
     {step:'feature.profile.compute',status:'succeeded'},
     {step:'feature.quality.screen',status:'succeeded'}]},
 explanation:{provider:'llm_curated',headline:'特征分析结果',
  summary:'2 项经过检查；没有预测收益验证。',
  claims:[{id:'overview',text:'已计算 2 项候选'}]}}};
 const html=render(state,{e:esc,u:encodeURIComponent});
 assert.match(html,/模型辅助 · 受控执行/);
 assert.match(html,/候选调整/);
 assert.match(html,/智能分析执行记录/);
 assert.match(html,/数据画像/);
 assert.match(html,/特征质量筛选/);
 assert.match(html,/2 项经过检查；没有预测收益验证/);
 assert.match(html,/逐条查看依据/);
 assert.match(html,/确认采用并计算/);
 assert.doesNotMatch(html,/自动发布特征/);
 assert.doesNotMatch(html, /''\}/);
});

test('v0.32.1 escapes untrusted text and clearly indicates rule fallback',()=>{
 const state={...base,autoSession:{session_id:'s2',status:'ready',stage:'review',revision:2,
 progress:.7,selection:['c1'],report,
 agent:{mode:'rules',rejected_proposals:1,
   candidate_adjustment:{provider:'rules',reason:'<script>evil()</script>'},
   trace:[{step:'feature.profile.compute',status:'succeeded'}]},
 explanation:{provider:'rules',headline:'规则结果',summary:'真实数据统计',claims:[]}}};
 const html=render(state,{e:esc,u:encodeURIComponent});
 assert.match(html,/规则模式 · 可无模型使用/);
 assert.match(html,/1 次模型建议未通过白名单校验/);
 assert.doesNotMatch(html,/<script>evil\(\)<\/script>/);
 assert.match(html,/&lt;script&gt;/);
});
