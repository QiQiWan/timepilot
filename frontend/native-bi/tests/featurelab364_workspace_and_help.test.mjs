import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const read=p=>fs.readFileSync(new URL('../../../src/timepilot/static/'+p,import.meta.url),'utf8');

test('project has one five-item project nav and one analysis step nav',()=>{
 const p=read('project-ui.js'),w=read('analysis-workspace.js');
 assert.match(p,/分析与特征/);
 assert.equal((p.match(/<nav class="project-tabs"/g)||[]).length,1);
 assert.match(w,/tp-analysis-steps/);
 assert.match(w,/tp-analysis-more/);
 assert.match(w,/data-section="features"|dataset\.section/);
 assert.match(read('analysis-workspace.css'),/data-section="outputs"/);
});

test('operator explanations include plain language, valid math, local MathJax and no remote requests',()=>{
 const code=read('feature-operator-help.js');
 assert.match(code,/mathjax-tex-svg\.js/);
 assert.doesNotMatch(code,/https?:\/\//);
 let listener;
 const document={addEventListener:(type,fn)=>{listener=fn;}};
 const window={};
 vm.runInNewContext(code,{window,document});
 const api=window.TimePilotOperatorHelp;
 for(const id of ['rolling.mean','rolling.std','lag.rows','industrial.reset_sum','tsfresh']){
  const html=api.render(id);
  assert.match(html,/怎么计算/);
  assert.match(html,/使用条件/);
  assert.match(html,/data-tex/);
 }
 assert.match(api.render('rolling.mean'),/最近 n 条/);
 assert.match(api.render('unknown-op'),/暂无内置/);
 assert.equal(typeof listener,'function');
});

test('chart narratives and materialization are readable and evidence-preserving',()=>{
 const feat=read('feature-workbench-ui.js'),data=read('data-analysis-ui.js');
 assert.match(feat,/怎么看这张图/);
 assert.match(feat,/保存与使用特征/);
 assert.match(feat,/来源 SHA256|源数据 SHA256/);
 assert.match(data,/怎么看这张图/);
 assert.match(data,/统计定义、限制和原始依据/);
});

test('admin log screen supports safe filtering and two exports',()=>{
 const html=read('index.html'),ui=read('system-logs-ui.js');
 assert.match(html,/href="\/logs"/);
 for(const key of ['level','source','since','q','download-jsonl','download-txt']){
  assert.ok(ui.includes(key));
 }
 assert.match(ui,/encodeURIComponent|URLSearchParams/);
});
