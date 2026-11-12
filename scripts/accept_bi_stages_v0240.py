"""One command per v0.23.6-v0.24.0 stage; never treat fixtures as real E2E.

All acceptance tests are run with the active Python/installed Chrome, no new
virtual environment, no Playwright download, and no external model required.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FRONT=ROOT/'frontend/native-bi'
STAGES={
  'assets-v0236': [('python', ['tests/test_asset_lifecycle_v0236.py','tests/test_bi_simple_intents_v0236.py'])],
  'bi-fullscreen-v0237': [('python',['tests/test_perspective_workspace_v0237_v0239.py']),('node', ['tests/workspace-bridge-v0237.test.mjs']),('fixture_chrome',[])],
  'perspective-parity-v0238': [('python',['tests/test_perspective_workspace_v0237_v0239.py','tests/test_bi_tool_manifest_v0239.py']),('node',['tests/workspace-bridge-v0237.test.mjs'])],
  'bi-tools-v0239': [('python',['tests/test_bi_tool_manifest_v0239.py','tests/test_conversational_bi_v0231.py']),('node',['tests/workspace-bridge-v0237.test.mjs'])],
  'bi-agent-v0240': [('python',['tests/test_bi_simple_intents_v0236.py','tests/test_bi_reasoning_v0240.py','tests/test_conversational_bi_v0231.py','tests/test_bi_tool_manifest_v0239.py']),('node',['tests/workspace-bridge-v0237.test.mjs'])],
}

def main():
    ap=argparse.ArgumentParser(description='TimePilot staged acceptance, explicit real-engine gate')
    ap.add_argument('--stage',choices=sorted(STAGES),required=True)
    ap.add_argument('--mode',choices=('offline','configured'),default='offline')
    ap.add_argument('--output',type=Path)
    a=ap.parse_args()
    env=os.environ.copy()
    env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
    env.setdefault('TIMEPILOT_HOME',str(ROOT/'runtime'))
    record={'stage':a.stage,'mode':a.mode,'started_at':datetime.now(timezone.utc).isoformat(),
      'python':platform.python_version(),'node':shutil.which('node'),
      'duckdb':bool(importlib.util.find_spec('duckdb')),
      'pyarrow':bool(importlib.util.find_spec('pyarrow')),
      'chrome':bool(shutil.which('chromium') or shutil.which('google-chrome')),
      'perspective_built':(ROOT/'src/timepilot/static/native-bi/perspective-entry.js').is_file(),
      'checks':[], 'real_native_e2e':False}
    status='source_tests_passed';returncode=0
    def execute(name,cmd,cwd=ROOT,timeout=240):
        nonlocal returncode,status
        print('> '+' '.join(cmd),flush=True)
        try:
            proc=subprocess.run(cmd,cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
            output=(proc.stdout+'\n'+proc.stderr)[-5000:]
            code=proc.returncode
        except (FileNotFoundError,subprocess.TimeoutExpired) as error:
            code=2;output=repr(error)
        record['checks'].append({'name':name,'return_code':code,'output_tail':output})
        print(output[-1600:],flush=True)
        if code:
            status='blocked' if code==2 else 'failed';returncode=code
            return False
        return True
    for kind,files in STAGES[a.stage]:
        if kind=='python':
            ok=execute('python_unit', [sys.executable,'-m','pytest','-q',*files])
        elif kind=='node':
            ok=execute('node_contract',[shutil.which('node') or 'node','--test',*files],FRONT)
        else:
            ok=execute('fixture_chrome',[sys.executable,'scripts/accept_bi_workspace_ui_v0233.py'],timeout=180)
        if not ok:break
    if returncode==0 and a.mode=='configured':
        # Missing dependencies are a BLOCKED ENVIRONMENT result, never a
        # failed functional acceptance and never a passing real-E2E claim.
        blockers=[]
        if record['python']!='3.12.12':blockers.append('需要目标 Python 3.12.12，当前 '+record['python'])
        if not record['duckdb']:blockers.append('DuckDB 未安装')
        if not record['pyarrow']:blockers.append('PyArrow 未安装')
        if not record['perspective_built']:blockers.append('缺少已编译且校验的 Perspective JS/WASM')
        if not record['chrome']:blockers.append('未发现系统 Chrome/Chromium')
        if blockers:
            record['checks'].append({'name':'configured_preflight','return_code':2,
                                     'output_tail':'；'.join(blockers)})
            status='blocked_environment';returncode=2
            print('CONFIGURED ENVIRONMENT BLOCKED: '+'; '.join(blockers),flush=True)
        # Production evidence: JS/WASM source fingerprint, DuckDB/Arrow, system
        # browser and a real remote Perspective session. Dependency absence
        # MUST block rather than being counted as an acceptance pass.
        real=[
          ('native_dependencies',[sys.executable,'scripts/verify_native_bi_release.py','--require-python31212','--require-duckdb','--require-build','--require-source-current','--require-virtual-server']),
          ('installed_chrome',[sys.executable,'scripts/system_chrome.py','--check']),
          ('perspective_live',[sys.executable,'scripts/accept_native_bi_live.py','--max-seconds',env.get('TIMEPILOT_ACCEPTANCE_MAX_SECONDS','300')]),
          ('conversational_real',[sys.executable,'scripts/accept_conversational_bi_v0231.py']),
        ]
        if returncode==0:
            for name,cmd in real:
                if not execute(name,cmd,timeout=390):break
        if returncode==0:record['real_native_e2e']=True;status='real_native_gates_passed'
    record['status']=status
    record['completed_at']=datetime.now(timezone.utc).isoformat()
    target=a.output or ROOT/'reports'/'v0240'/f'{a.stage}_{a.mode}.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(target),'stage':a.stage,'status':status,'real_native_e2e':record['real_native_e2e']},ensure_ascii=False),flush=True)
    return returncode

if __name__=='__main__':raise SystemExit(main())
