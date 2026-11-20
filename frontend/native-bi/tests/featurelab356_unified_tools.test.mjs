import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const read=f=>fs.readFileSync(new URL('../../../src/timepilot/static/'+f,import.meta.url),'utf8');
const version=JSON.parse(fs.readFileSync(new URL('../package.json',import.meta.url),'utf8')).version;
function delivery(){
  const w={};
  vm.runInNewContext(read('analysis-delivery-ui.js'),{
    window:w,globalThis:{crypto:webcrypto},crypto:webcrypto,
    TextEncoder,Uint8Array,Blob,URL,setTimeout,clearTimeout,document:{}
  });
  return w.TimePilotAnalysisDeliveryUI;
}
const same='a'.repeat(64);
const session={session_id:'session-1',revision:4,stage:'evaluated',source_sha256:same,contract_revision:2,stale:false};
const basic={pid:'p',aid:'asset',vid:'base',assetName:'设备数据',sets:[{set_id:'set1',asset_id:'asset',version_id:'base',name:'特征集一',revision:1,state:'draft'}],
  session,delivery:{status:'materialized',source:{asset_id:'asset',version_id:'base',sha256:same,contract_revision:2},
    feature_set:{set_id:'set1'},artifact:{artifact_id:'artifact-1',sha256:'b'.repeat(64),row_count:200}},reports:[],preview:''};

test('one workspace exposes source-aligned manual, advanced, native BI and report tools',()=>{
  const p=read('analysis-workspace.js');
  for(const word of ['手工构造','高级算法','BI 数据','报告与成果']) assert.ok(p.includes(word));
  assert.match(p,/window\.TimePilotFeatureV2UI\?\.render\(ctx,pid,tools\[tool\]\[0\]\)/);
  assert.match(p,/window\.TimePilotAnalysisDeliveryUI\?\.render/);
  assert.match(p,/ctx|__tpCtx/);
  const html=read('index.html');
  for(const file of ['analysis-delivery-ui.js','analysis-delivery-ui.css','feature-integrated-bi-ui.js']){
    assert.ok(html.includes(file+'?v='+version),`${file} must load with current cache key`);
  }
  assert.ok(html.indexOf('feature-integrated-bi-ui.js')<html.indexOf('feature-shell.js'));
  assert.ok(html.indexOf('analysis-delivery-ui.js')<html.indexOf('analysis-workspace.js'));
});

test('in-workspace switching keeps exact asset version and browser history',async()=>{
  const loc={href:'http://127.0.0.1:8000/projects/p/analysis?asset=A%20one&version=v1&section=features',pathname:'/projects/p/analysis',search:'?asset=A%20one&version=v1&section=features'};
  const moves=[];
  const host={isConnected:true,dataset:{},innerHTML:'',addEventListener(){},querySelectorAll(){return[]},querySelector(){return null},scrollIntoView(){}};
  const w={TimePilotDataAnalysisUI:{getContext:()=>({aid:'A one',vid:'v1'}),render:async()=>{},dispose(){}},
    TimePilotFeatureV2UI:{render:async()=>{},dispose(){}},TimePilotAnalysisInsightsUI:{mount(){}}};
  const d={getElementById:()=>host,addEventListener(){},removeEventListener(){}};
  const ctx={navigate(url){moves.push(url)}};
  const sandbox={window:{...w,location:loc},location:loc,document:d,history:{replaceState(){}},URL,URLSearchParams};
  vm.runInNewContext(read('analysis-workspace.js'),sandbox);
  await sandbox.window.TimePilotAnalysisWorkspace.render(ctx,'p');
  sandbox.window.TimePilotAnalysisWorkspace.openTool('manual');
  assert.equal(moves.length,1);
  assert.match(moves[0],/tool=manual/);
  assert.match(moves[0],/asset=A(?:%20|\+)one/);
  assert.match(moves[0],/version=v1/);
});

test('delivery display only permits verified source-matching current artifacts',()=>{
  const ui=delivery();
  const good=ui.renderPage(basic);
  assert.match(good,/正式物化/);assert.match(good,/在 BI 查看正式成果/);
  assert.match(good,/手工编辑或新建特征集/);
  const old=ui.renderPage({...basic,delivery:{...basic.delivery,source:{...basic.delivery.source,sha256:'f'.repeat(64)}}});
  assert.doesNotMatch(old,/在 BI 查看正式成果/);
  assert.match(old,/待计算/);
  const stale=ui.renderPage({...basic,session:{...session,stale:true}});
  assert.doesNotMatch(stale,/data-delivery="archive"(?![^>]*disabled)/);
  assert.match(stale,/过期|变化|来源/);
});

test('delivery sanitizes untrusted source names and report previews',()=>{
  const html=delivery().renderPage({...basic,assetName:'<script>alert(1)</script>',preview:'<svg onload=alert(1)>'});
  assert.doesNotMatch(html,/<script>alert/);assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<svg onload/);assert.match(html,/&lt;svg/);
});

test('archived Markdown bytes require a strict SHA256 checksum',async()=>{
  const ui=delivery(),body='# a report\n',data=new TextEncoder().encode(body);
  const digest=await webcrypto.subtle.digest('SHA-256',data);
  const hex=Buffer.from(digest).toString('hex');
  await ui.checksum(body,hex);
  await assert.rejects(ui.checksum(body,'1'.repeat(64)),/SHA256/);
  await assert.rejects(ui.checksum(body,'invalid'),/SHA256/);
});

test('BI view allows verified feature comparison only when source matches',()=>{
  const w={TimePilotFeatureViews:{}};
  vm.runInNewContext(read('feature-integrated-bi-ui.js'),{window:w});
  const ui=w.TimePilotFeatureViews.bi;
  const h={e:x=>String(x??'')};
  const base={pid:'p',asset:'asset',version:'base',contract:{source_sha256:same},
    assets:[{native_asset_id:'asset',asset:{name:'production.csv'}}]};
  const proof=basic.delivery;
  let html=ui({...base,biProof:proof},h);
  assert.match(html,/data-feature-v2="bi-verified-compare"/);
  assert.doesNotMatch(html,/data-feature-v2="bi-verified-compare" disabled/);
  html=ui({...base,biProof:{...proof,source:{...proof.source,sha256:'f'.repeat(64)}}},h);
  assert.match(html,/data-feature-v2="bi-verified-compare" disabled/);
});

test('legacy secured controls stay wired to in-place tool modes',()=>{
  const shell=read('feature-shell.js');
  for(const key of ['tp-fl2-add-step','tp-fl2-add-formula','tp-fl2-save-set','tp-fl2-advanced-form','tp-adv-join-form','tp-adv-eval-form'])
    assert.ok(shell.includes(key)||read('feature-builder-ui.js').includes(key)||read('feature-advanced-ui.js').includes(key));
  assert.match(shell,/async function openIntegratedBi/);
  assert.match(shell,/bi-verified-compare/);
  assert.match(shell,/bi-receipt/);
  assert.match(shell,/source_sha256/);
  assert.match(shell,/s\.viewerOpen=true;show\(\)/);
});

test('re-rendering workspace replaces the click listener and avoids duplicate navigation',async()=>{
  const listeners=new Map();const moves=[];
  const loc={href:'http://localhost/projects/p/analysis?asset=A&version=base',pathname:'/projects/p/analysis',search:'?asset=A&version=base'};
  const host={isConnected:true,dataset:{},innerHTML:'',__tpCtx:null,
    addEventListener(type,fn){listeners.set(type,[...(listeners.get(type)||[]),fn])},
    removeEventListener(type,fn){listeners.set(type,(listeners.get(type)||[]).filter(x=>x!==fn))},
    querySelectorAll(){return[]},querySelector(){return null},contains(){return true}};
  const w={TimePilotDataAnalysisUI:{getContext:()=>({aid:'A',vid:'base'}),render:async()=>{},dispose(){}},
    TimePilotFeatureV2UI:{render:async()=>{},dispose(){}},TimePilotAnalysisInsightsUI:{mount(){}}};
  const d={getElementById:()=>host,addEventListener(){},removeEventListener(){}};
  const ctx={navigate(url){moves.push(url)}};
  const sb={window:{...w,location:loc},location:loc,document:d,history:{replaceState(){}},URL,URLSearchParams};
  vm.runInNewContext(read('analysis-workspace.js'),sb);
  await sb.window.TimePilotAnalysisWorkspace.render(ctx,'p');
  await sb.window.TimePilotAnalysisWorkspace.render(ctx,'p');
  assert.equal((listeners.get('click')||[]).length,1);
  const button={hasAttribute:key=>key==='data-analysis-tool',dataset:{analysisTool:'manual'}};
  const event={target:{closest:()=>button},preventDefault(){}};
  listeners.get('click')[0](event);
  assert.equal(moves.length,1);
  assert.match(moves[0],/tool=manual/);
});

test('saved set management opens the exact set without losing asset and version',async()=>{
  const loc={href:'http://localhost/projects/p/analysis?tool=reports&asset=asset1&version=v7',pathname:'/projects/p/analysis',search:'?tool=reports&asset=asset1&version=v7'};
  const moves=[],host={isConnected:true,dataset:{},innerHTML:'',addEventListener(){},removeEventListener(){},querySelectorAll(){return[]},querySelector(){return null}};
  const ctx={navigate:url=>moves.push(url)};
  const sb={window:{location:loc,TimePilotFeatureV2UI:{dispose(){}}},location:loc,document:{getElementById:()=>host,addEventListener(){},removeEventListener(){}},URL,URLSearchParams};
  vm.runInNewContext(read('analysis-workspace.js'),sb);
  await sb.window.TimePilotAnalysisWorkspace.render(ctx,'p');
  sb.window.TimePilotAnalysisWorkspace.openTool('manual',{setId:'set-42'});
  assert.match(moves[0],/asset=asset1/);
  assert.match(moves[0],/version=v7/);
  assert.match(moves[0],/tool=manual/);
  assert.match(moves[0],/set=set-42/);
  assert.match(read('feature-shell.js'),/query\.get\('set'\)/);
});

test('report workspace never substitutes another asset for a requested missing asset',async()=>{
  const w={};vm.runInNewContext(read('analysis-delivery-ui.js'),{
    window:w,globalThis:{crypto:webcrypto},crypto:webcrypto,TextEncoder,Uint8Array,Blob,URL,
    setTimeout,clearTimeout,document:{}});
  const host={isConnected:true,innerHTML:'',addEventListener(){}};
  await w.TimePilotAnalysisDeliveryUI.render({req:async()=>[{
    native_asset_id:'asset-one',asset:{name:'one.csv',columns:['A']}
  }]},'p',host,'deleted-asset','base');
  assert.match(host.innerHTML,/已不存在|无权限/);
  assert.doesNotMatch(host.innerHTML,/one\.csv/);
});
