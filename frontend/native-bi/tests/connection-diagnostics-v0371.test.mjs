import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
import {withTrace,explainConnectionFailure} from '../src/connection-diagnostics.mjs';
const folder=dirname(fileURLToPath(import.meta.url));

test('BI WebSocket trace keeps project scope and same-origin route',()=>{
 const result=withTrace('ws://127.0.0.1:8000/api/v1/native-bi/assets/a/versions/base/ws?project_id=p','tpabcdef0123');
 const parsed=new URL(result);
 assert.equal(parsed.pathname,'/api/v1/native-bi/assets/a/versions/base/ws');
 assert.equal(parsed.searchParams.get('project_id'),'p');
 assert.equal(parsed.searchParams.get('tp_trace'),'tpabcdef0123');
 assert.throws(()=>withTrace(result,'a b'),/trace/);
});

test('server verified error reveals refusal code without guessing protocol cause',async()=>{
 const diagnostics={active_connections:2,max_connections:2,
   last_failure:{stage:'capacity',code:'connections_busy',close_code:1013}};
 const f=async()=>({ok:true,json:async()=>diagnostics});
 const error=await explainConnectionFailure('tpabcdef0123','WebSocket',new Error('Generic Websocket Error'),f);
 assert.match(error.message,/连接数已满/);
 assert.match(error.message,/1013/);
 assert.match(error.message,/tpabcdef0123/);
});

test('no diagnostic information keeps the actual original error',async()=>{
 const e=await explainConnectionFailure('tpabcdef0123','远程表',new Error('Catalog empty'),async()=>{throw new Error('offline')});
 assert.match(e.message,/Catalog empty/);
 assert.doesNotMatch(e.message,/已满/);
});

test('single browser module registration and stale digest full-refresh guard',async()=>{
 const src=readFileSync(resolve(folder,'../../../src/timepilot/static/perspective-loader.js'),'utf8');
 const js=src.replace('import(ENTRY)','mockImport(ENTRY)');
 const files=[];let imports=0;let digest='a'.repeat(64);
 const existing=()=>files.find(x=>x.dataset?.perspectiveCss==='1')||null;
 const fakeModule={mountPerspectiveRemote(){},mountFeatureComparison(){},disposePerspective(){}};
 const scope={window:{},document:{querySelector:existing,createElement:()=>({dataset:{}}),head:{appendChild:x=>files.push(x)}},
   fetch:async()=>({ok:true,json:async()=>({files:{'perspective-entry.js':{sha256:digest}}})}),
   mockImport:async url=>{imports++;assert.equal(url,'/assets/native-bi/perspective-entry.js');return fakeModule;}};
 vm.runInNewContext(js,scope);
 const loader=scope.window.TimePilotPerspectiveLoader;
 const [a,b,c]=await Promise.all([loader.load(),loader.load(),loader.load()]);
 assert.equal(a,b);assert.equal(b,c);assert.equal(imports,1);
 assert.equal(files.length,1);assert.match(files[0].href,/perspective-entry\.css\?v=aaaa/);
 await loader.load();assert.equal(imports,1);
 digest='b'.repeat(64);
 await assert.rejects(loader.load(),/刷新整个页面/);
 assert.equal(imports,1);
});
