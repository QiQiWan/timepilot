import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const filename=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../../src/timepilot/static/agent-ui.js');
const code=fs.readFileSync(filename,'utf-8');

test('resumed Agent jobs keep polling after page reload until terminal state',async()=>{
  let state='running',ticks=0,cleared=0, timer;
  const detail={innerHTML:''};
  const recent={innerHTML:''};
  const doc={ getElementById(id){return ({'agent-job-detail':detail,'agent-recent':recent})[id]??null} };
  const window={};
  const context={window,document:doc,location:{pathname:'/agent/job-123'},
    setInterval(callback){timer=callback;ticks++;return 7},
    clearInterval(){cleared++}};
  vm.runInNewContext(code,context,{filename});
  const root={innerHTML:''};
  const req=async uri=>{
    if(uri==='/agent/skills')return {skills:[]};
    if(uri==='/data-assets'||uri==='/smart-import/imports')return [];
    if(uri==='/agent/jobs?limit=20')return [];
    if(uri==='/agent/jobs/job-123')return {job_id:'job-123',status:state,goal:'quality',
      budget:{used_units:1,max_units:30,used_seconds:0,max_seconds:300},
      model_status:'disabled',steps:[]};
    throw new Error('unexpected route: '+uri)
  };
  await window.TimePilotAgentCenter.render({root,req,toast(){},navigate(){}});
  assert.match(detail.innerHTML,/执行中/);
  assert.equal(ticks,1,'resume polling an already-running job');
  state='succeeded';
  await timer();
  assert.match(detail.innerHTML,/已完成/);
  assert.equal(cleared,1,'stop polling when job is terminal');
});
