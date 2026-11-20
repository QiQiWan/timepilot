import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const file=f=>fs.readFileSync(new URL('../../../src/timepilot/static/'+f,import.meta.url),'utf8');
const version=JSON.parse(fs.readFileSync(new URL('../package.json',import.meta.url),'utf8')).version;

test('unified route uses one workspace with legacy feature deep-link compatibility',()=>{
 const project=file('project-ui.js');
 assert.match(project,/TimePilotAnalysisWorkspace\?\.render/);
 assert.match(project,/url\.pathname=projectPath\(project.id\)\+'\/analysis'/);
 assert.match(project,/url\.searchParams\.set\('section','features'\)/);
 assert.doesNotMatch(project,/href="\$\{link\(pid,aid,vid,x.action/);
});

test('data analysis no generic continue links and namespace persisted chart filters',()=>{
 const s=file('data-analysis-ui.js');
 assert.doesNotMatch(s,/>继续<\/a>/);
 assert.match(s,/data-da-action/);
 assert.match(s,/chart-explanation/);
 assert.match(s,/da_field/);
 assert.match(s,/da_entity/);
 assert.match(s,/data-da="asset"/);
 assert.ok(s.includes("prefix+'/start'"));
});

test('feature chart notes and scales protect incompatible dimensions',()=>{
 const feature=file('feature-workbench-ui.js');
 assert.match(feature,/feature-chart-explanation/);
 assert.match(feature,/axis_compatible===false/);
 assert.match(feature,/手动操作与高级特征搜索/);
});

test('workspace keeps source context and one step at a time',()=>{
 const s=file('analysis-workspace.js');
 assert.match(s,/quality','statistics','features'/);
 assert.match(s,/URLSearchParams\(location.search\)/);
 assert.match(s,/TimePilotDataAnalysisUI\?\.render/);
 assert.match(s,/TimePilotFeatureV2UI\?\.render/);
});

test('HTML registers unified workspace stylesheet and controller in source bundle',()=>{
 const html=file('index.html');
 assert.ok(html.includes('/assets/analysis-workspace.js?v='+version));
 assert.ok(html.includes('/assets/analysis-workspace.css?v='+version));
 assert.ok(html.indexOf('data-analysis-ui.js?v='+version)<html.indexOf('analysis-workspace.js?v='+version));
});

test('switching assets removes source-local feature and chart filters',()=>{
 const s=file('data-analysis-ui.js');
 assert.match(s,/\['candidate','advanced_candidate','da_field','da_mode','da_entity'/);
 assert.match(s,/url\.searchParams\.set\('section','overview'\)/);
 assert.match(s,/const ticks=tickIndices/);
});

test('unified view renders an automatic multi-field quality overview from profile evidence',()=>{
 const ui=file('data-analysis-ui.js');
 const css=file('analysis-workspace.css');
 assert.match(ui,/data-testid="data-analysis-workbench"/);
 assert.match(ui,/tp-da-quality-overview/);
 assert.match(ui,/doc\?\.charts\?\.missing/);
 assert.match(css,/\.tp-da-quality-bars/);
});
