import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const src=f=>fs.readFileSync(new URL('../../../src/timepilot/static/'+f,import.meta.url),'utf8');
const version=JSON.parse(fs.readFileSync(new URL('../package.json',import.meta.url),'utf8')).version;
function ui(){
  const w={}; vm.runInNewContext(src('analysis-insights-ui.js'),{window:w,document:{},setTimeout,clearTimeout,location:{search:'?asset=a&version=base'}});return w.TimePilotAnalysisInsightsUI;
}
test('no label means no fabricated scores or comparison table',()=>{
 const html=ui().renderContent({status:'ready',source_sha256:'a'.repeat(64),layers:{},model:{status:'no_confirmed_label',message:'当前无监督标签',comparisons:[]}});
 assert.match(html,/当前无监督标签/);
 assert.doesNotMatch(html,/insights-model-comparison/);
 assert.match(html,/data-model-state="no_confirmed_label"/);
});
test('verified model metrics appear in three-way comparison with explicit provenance',()=>{
 const html=ui().renderContent({status:'ready',source_sha256:'a'.repeat(64),contract_revision:3,layers:{},
  model:{status:'promising_in_validation',metric_label:'MAE',message:'同折改善',
   comparisons:[{label:'原始字段',mean:1.5,valid_folds:3},{label:'全部候选',mean:1.2,valid_folds:3},{label:'筛选后特征',mean:1.0,valid_folds:3}],
   evidence:[{kind:'model_evaluation',path:'report.paired_comparison.metrics.mae',source_sha256:'a'.repeat(64),artifact_sha256:'b'.repeat(64)}]}});
 assert.match(html,/insights-model-comparison/);
 assert.match(html,/原始字段/);assert.match(html,/筛选后特征/);
 assert.match(html,/1\.5/);assert.match(html,/report\.paired_comparison\.metrics\.mae/);
 assert.match(html,/b{64}/);
});
test('untrusted feature names are escaped and no-script content is rendered',()=>{
 const html=ui().renderContent({status:'ready',source_sha256:'a',layers:{data_quality:{status:'measured',findings:[{
 title:'<img src=x onerror=alert(1)>',observation:'abc',evidence:[],action:'quality'}]}},model:{status:'no_confirmed_label',comparisons:[]}});
 assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
});
test('stale state cannot show old insights',()=>{
 const html=ui().renderContent({status:'stale',model:{status:'promising_in_validation',comparisons:[{mean:99}]}});
 assert.match(html,/insights-stale/);assert.doesNotMatch(html,/99/);
});
test('unified page integrates insights before candidate results and preserves chart/workflow routes',()=>{
 const work=src('analysis-workspace.js'),html=src('index.html');
 assert.ok(work.includes('tp-unified-insights') && work.includes('tp-unified-features'));
 assert.ok(work.indexOf('tp-analysis-data-surface')<work.indexOf('tp-analysis-feature-surface'));
 assert.match(work,/dataset\.section/);
 assert.match(work,/TimePilotAnalysisInsightsUI\?\.mount/);
 assert.ok(html.includes('analysis-insights-ui.js?v='+version));
 assert.ok(html.indexOf('analysis-insights-ui.js')<html.indexOf('analysis-workspace.js'));
 const api=fs.readFileSync(new URL('../../../src/timepilot/feature_lab_v2_api.py',import.meta.url),'utf8');
 assert.match(api,/workspace\/insights/);
});
