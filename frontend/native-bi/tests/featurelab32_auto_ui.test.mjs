import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { test } from 'node:test';

const root = path.resolve(import.meta.dirname, '../../..');
const load = (name) => fs.readFileSync(path.join(root, 'src/timepilot/static', name), 'utf8');
const context = {window: {TimePilotFeatureViews: {}}, console};
vm.createContext(context);
vm.runInContext(load('feature-auto-ui.js'), context, {filename:'feature-auto-ui.js'});
const render = context.window.TimePilotFeatureViews.analysis;
const esc = x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c]));
const fixture = {
  pid:'project-1',assets: [{native_asset_id:'source-1',asset: {name:'housing.csv'}}], asset:'source-1',
  contract: {fields: [{source_name:'Time'}, {source_name:'A1'}],source_sha256:'a'.repeat(64)},
  timeBasis: {fields:[{name:'Time',field_id:'fld-time',kind:'sequence_order_candidate',confidence:.90,
    evidence:['Integer sequence, not calendar time'],confirmed:false}]},
  autoGoal:'Find feature statistics',autoObjective:'explore',autoSession:null,
};

test('default FeatureLab screen has goal, auto start, and conservative time suggestion', () => {
 const html=render(fixture,{e:esc,u:encodeURIComponent});
 assert.match(html,/housing\.csv/);
 assert.match(html,/开始自动分析/);
 assert.match(html,/data-feature-v2="accept-time-order"/);
 assert.match(html,/记录顺序候选/);
 assert.match(html,/不能据此推断真实时间间隔/);
 assert.doesNotMatch(html,/预测性能提升 \d+%/);
});

test('results expose full-scope quality, draft and materialize without fake model gains', () => {
 const session={session_id:'session-1',status:'ready',stage:'review',progress:1,
   source_sha256:'a'.repeat(64),revision:5,selection:['a1'],
   report:{row_count:506,field_count:15,profile_scope:'full',candidate_count:2,
     evaluated_count:2,recommended_count:1,high_missing_fields:[],
     quality_rows:[{candidate_id:'a1',output_name:'A1_mean5',display_name:'A1 rolling mean',
       status:'recommended',reason:'passed coverage',operator_id:'rolling.mean',
       quality:{null_rate:0,rows_scanned:506,std:0.4}}],limitations:[]}};
 const html=render({...fixture,autoSession:session},{e:esc,u:encodeURIComponent});
 assert.match(html,/A1 rolling mean/);
 assert.match(html,/506/);
 assert.match(html,/确认采用并计算/);
 assert.match(html,/尚未评价预测收益/);
 assert.match(html,/data-feature-v2="auto-report"/);
});

test('production entry points load auto workflow before feature-shell', () => {
 const html=load('index.html');
 const version=JSON.parse(fs.readFileSync(new URL('../package.json',import.meta.url),'utf8')).version;
 assert.ok(html.indexOf(`feature-auto-ui.js?v=${version}`) < html.indexOf(`feature-shell.js?v=${version}`));
 const shell=load('feature-shell.js');
 assert.match(shell,/\['analysis','analysis'\]/);
 assert.match(shell,/analysis\/sessions/);
 assert.match(shell,/time-basis\/infer/);
 const project=load('project-ui.js');
 assert.match(project,/\['analysis','分析与特征'\]/);
});
