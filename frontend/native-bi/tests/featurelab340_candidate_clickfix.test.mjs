import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const src = (name) => readFileSync(new URL('../../../src/timepilot/static/' + name, import.meta.url), 'utf8');
const sandbox = {window: {}};
vm.runInNewContext(src('feature-workbench-ui.js'), sandbox, {filename: 'feature-workbench-ui.js'});
const render = sandbox.window.TimePilotFeatureViews.analysis;
const esc = (x) => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const helper = {e: esc, u: encodeURIComponent};
const candidate = (id, name, status='recommended') => ({
  candidate_id:id, display_name:name, output_name:id, status, operator_id:'rolling.mean',
  quality:{null_rate:0, mean:3, std:1, finite_count:4, rows_scanned:4}
});
const session = {session_id:'sid', source_sha256:'f'.repeat(64), status:'ready', stage:'review',
  selection:['a'], report:{quality_rows:[candidate('a','A1 · 移动平均'), candidate('b','A2 · 历史差值')],
  candidate_count:2, recommended_count:2, review_count:0, excluded_count:0}};
function output(state={}) {
  return render({pid:'pid', asset:'asset', version:'base',
    assets:[{native_asset_id:'asset',asset:{name:'demo.csv'}}],
    contract:{state:'confirmed', revision:1, fields:[]},
    autoSession:session, uiFocused:'a', uiChartMode:'distribution', uiFilter:'all',
    uiDraftSelection:['a'], ...state}, helper);
}

test('checkbox is separate from the full-row detail button; the old view button and wrapping label are gone', () => {
  const html=output();
  const row=html.match(/<div class="tp-wb-item focused">([\s\S]*?)<\/div>/)?.[1];
  assert.ok(row, 'focused row exists');
  assert.match(row, /<input class="tp-wb-item-checkbox" type="checkbox"/);
  assert.match(row, /data-feature-v2-change="wb-choice"/);
  assert.match(row, /aria-label="选择A1 · 移动平均"/);
  assert.match(row, /<button class="tp-wb-item-open" type="button" data-feature-v2="wb-focus"/);
  assert.match(row, /data-cid="a" aria-controls="tp-wb-feature-detail" aria-current="true"/);
  assert.doesNotMatch(row, /<label|>查看<\/button>/);
  assert.match(html, /id="tp-wb-feature-detail"/);
  assert.match(output({uiFocused:'b'}), /data-cid="b" aria-controls="tp-wb-feature-detail" aria-current="true"/);
});

test('a visible row stays interactive when checkbox is disabled, without making its label toggle the checkbox', () => {
  const html = output({autoSession:{...session,stage:'evaluated',selection:[]},uiDraftSelection:[]});
  assert.match(html, /class="tp-wb-item-checkbox"[^>]*disabled/);
  assert.match(html, /class="tp-wb-item-open"[^>]*data-cid="b"/);
  assert.doesNotMatch(html, /<label><input type="checkbox" name="candidate_id"/);
});

test('clicking the focused row is idempotent and URL/chart handlers remain independent of checkbox changes',()=>{
  const js=src('feature-shell.js');
  assert.match(js, /if\(act==='wb-focus'\)\{if\(s\.uiFocused===el\.dataset\.cid&&\!s\.uiAdvancedFocus\)return true;/);
  assert.match(js, /if\(kind==='wb-choice'\)/);
  assert.match(js, /if\(event\.target\.checked\)ids\.add\(event\.target\.value\);else ids\.delete\(event\.target\.value\)/);
  assert.match(js, /writeViewURL\(true\);await loadAutoChart\(\)/);
  const css=src('feature-lab2.css');
  assert.match(css,/\.tp-wb-item-open\{[^}]*flex:1/);
  assert.match(css,/\.tp-wb-item-open:focus-visible/);
  const index=src('index.html');
  const version = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version;
  for (const name of ['feature-lab2.css','feature-workbench-ui.js','feature-shell.js'])
    assert.ok(index.includes(`${name}?v=${version}`));
});
