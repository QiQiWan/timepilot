"""Version-specific evidence with explicit offline versus real-runtime gates.

The offline path exercises actual project catalog/HTTP contracts and frontend
state adapters. It must never be presented as DuckDB/Arrow/Perspective E2E.
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / 'frontend/native-bi'
STAGES = {
 'bi-recovery-v0241': [('python', ['tests/test_asset_lifecycle_v0236.py','tests/test_import_contract_and_jobs_v0242.py','tests/test_perspective_workspace_v0237_v0239.py']),('node',['tests/workspace-bridge-v0237.test.mjs','tests/agent-manual-merge-v0243.test.mjs'])],
 'import-contract-v0242': [('python', ['tests/test_import_contract_and_jobs_v0242.py','tests/test_import_summary_v0234.py','tests/test_import_summary_llm_fallback_v0234.py','tests/test_universal_import_v0221.py'])],
 'bi-workspace-v0243': [('python',['tests/test_perspective_workspace_v0237_v0239.py','tests/test_bi_reasoning_v0240.py']),('node',['tests/workspace-bridge-v0237.test.mjs','tests/agent-manual-merge-v0243.test.mjs'])],
 'feature-lab-v0244': [('python',['tests/test_feature_lab_v0244.py','tests/test_bi_math_and_context_v0245.py']),('node',['tests/agent-manual-merge-v0243.test.mjs'])],
 'bi-reasoning-v0245': [('python',['tests/test_bi_math_and_context_v0245.py','tests/test_bi_reasoning_v0240.py','tests/test_bi_reasoning_effort_v0240.py','tests/test_bi_simple_intents_v0236.py','tests/test_conversational_bi_v0231.py']),('node',['tests/agent-manual-merge-v0243.test.mjs'])],
}


def utc():return datetime.now(timezone.utc).isoformat()


def main():
    ap=argparse.ArgumentParser(description='TimePilot v0.24.1-0.24.5 stage gates')
    ap.add_argument('--stage',choices=sorted(STAGES),required=True)
    ap.add_argument('--mode',choices=('offline','configured'),default='offline')
    ap.add_argument('--output',type=Path)
    opt=ap.parse_args()
    env=os.environ.copy()
    env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
    env['PYTHONUNBUFFERED']='1'
    python=platform.python_version()
    deps={name:bool(importlib.util.find_spec(name)) for name in ('duckdb','pyarrow','pytest')}
    chrome=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    bundle=ROOT/'src/timepilot/static/native-bi'
    runtime_assets=any(bundle.glob('*')) if bundle.is_dir() else False
    evidence={'stage':opt.stage,'mode':opt.mode,'started_at':utc(),
              'environment':{'python':python,'node':shutil.which('node'),'chrome':chrome,'dependencies':deps,'native_bundle_exists':runtime_assets},
              'steps':[],'real_e2e':False,'status':'running'}
    rc=0
    def run_test(kind,files,cwd=ROOT,timeout=100):
        if kind=='python':cmd=[sys.executable,'-m','pytest','-q',*files]
        else:cmd=[shutil.which('node') or 'node','--test',*files]
        print('> '+' '.join(cmd),flush=True)
        try:
            completed=subprocess.run(cmd,cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
            code=completed.returncode
            output=(completed.stdout+'\n'+completed.stderr)[-3500:]
        except (OSError,subprocess.TimeoutExpired) as exc:
            code=2;output=repr(exc)
        evidence['steps'].append({'type':kind,'files':files,'return_code':code,'tail':output})
        print(output[-1000:],flush=True)
        return code
    for kind,files in STAGES[opt.stage]:
        rc=run_test(kind,files,FRONT if kind=='node' else ROOT)
        if rc:break
    if rc==0 and opt.mode=='configured':
        blockers=[]
        if python!='3.12.12':blockers.append(f'Python {python}, expected 3.12.12')
        for name in ('duckdb','pyarrow'):
            if not deps[name]:blockers.append(f'{name} missing')
        if not chrome:blockers.append('installed Chrome/Chromium missing')
        if not runtime_assets:blockers.append('compiled Perspective JS/WASM missing')
        if blockers:
            rc=2
            evidence['steps'].append({'type':'real_dependency_gate','return_code':rc,'blockers':blockers})
        else:
            # Old real E2E still needs to pass; feature-specific real numerics
            # and multi-panel restore have separate release gates in v0.24.6.
            rc=run_test('python',['tests/test_bi_correlation_duckdb_v0235.py'],timeout=100)
            if rc==0:
                cmd=[sys.executable,'scripts/verify_native_bi_release.py','--require-python31212','--require-duckdb','--require-build','--require-source-current','--require-virtual-server']
                try:
                    completed=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=150)
                    rc=completed.returncode
                    evidence['steps'].append({'type':'native_build_gate','return_code':rc,'tail':(completed.stdout+completed.stderr)[-3000:]})
                except (OSError,subprocess.TimeoutExpired) as exc:
                    rc=2;evidence['steps'].append({'type':'native_build_gate','return_code':rc,'tail':repr(exc)})
            if rc==0:
                cmd=[sys.executable,'scripts/accept_native_bi_live.py','--max-seconds','300']
                try:
                    completed=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=330)
                    rc=completed.returncode
                    evidence['steps'].append({'type':'native_perspective_e2e','return_code':rc,'tail':(completed.stdout+completed.stderr)[-3500:]})
                except (OSError,subprocess.TimeoutExpired) as exc:
                    rc=2;evidence['steps'].append({'type':'native_perspective_e2e','return_code':rc,'tail':repr(exc)})
            if rc==0:
                evidence['real_e2e']=True
    evidence['status']='source_tests_passed' if rc==0 and opt.mode=='offline' else ('real_native_gates_passed' if rc==0 else ('blocked_environment' if opt.mode=='configured' and any(x.get('type')=='real_dependency_gate' for x in evidence['steps']) else 'failed'))
    evidence['finished_at']=utc()
    path=opt.output or ROOT/'reports/v0245'/f'{opt.stage}_{opt.mode}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'stage':opt.stage,'status':evidence['status'],'real_e2e':evidence['real_e2e'],'report':str(path)},ensure_ascii=False),flush=True)
    return rc

if __name__=='__main__':raise SystemExit(main())
