"""Versioned FeatureLab 2.0 acceptance with explicit source vs real-E2E status."""
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
P='tests/'
CORE=P+'test_featurelab2_contract_registry_v0247.py'
TEMP=P+'test_featurelab2_temporal_and_evaluation_v0250.py'
MON=P+'test_featurelab2_monitor_and_pit_v0251.py'
AGENT=P+'test_featurelab2_agent_workflows_v0252.py'
LEGACY=P+'test_feature_lab_v0244.py'
STAGES={
 'feature-contract-v0247':[CORE,LEGACY],
 'feature-profile-v0248':[CORE,MON,P+'test_import_contract_and_jobs_v0242.py'],
 'feature-execution-v0249':[CORE,MON,AGENT],
 'feature-evaluation-v0250':[TEMP,MON],
 'feature-registry-v0251':[CORE,MON,LEGACY,P+'test_asset_lifecycle_v0236.py'],
 'feature-agent-v0252':[CORE,TEMP,MON,AGENT,LEGACY],
}


def run(cmd,env,timeout=130):
    try:
        completed=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=timeout)
        return {'command':cmd,'exit_code':completed.returncode,
                'tail':(completed.stdout+'\n'+completed.stderr)[-2400:]}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'command':cmd,'exit_code':2,'tail':f'{type(exc).__name__}: {exc}'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--stage',required=True,choices=sorted(STAGES))
    ap.add_argument('--mode',default='offline',choices=('offline','configured'))
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    env=os.environ.copy()
    env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
    source_deps={x:bool(importlib.util.find_spec(x)) for x in ('duckdb','pyarrow','playwright','pytest')}
    chrome=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    bundle=ROOT/'src/timepilot/static/native-bi'
    native_ready=bundle.is_dir() and any(bundle.rglob('*.wasm'))
    report={'stage':args.stage,'mode':args.mode,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'environment':{'python':platform.python_version(),'dependencies':source_deps,
                           'chrome':chrome,'native_wasm_present':native_ready},
            'checks':[],'status':'running','real_e2e':False}
    py=run([sys.executable,'-m','pytest','-q','-rs',*STAGES[args.stage]],env)
    report['checks'].append({'kind':'python_unit_http_contract',**py})
    result=py['exit_code']
    if result==0 and args.stage in ('feature-profile-v0248','feature-execution-v0249','feature-agent-v0252'):
        ui=run([sys.executable,'scripts/accept_featurelab2_ui_v0248.py'],env,timeout=80)
        report['checks'].append({'kind':'installed_chromium_component',**ui});result=ui['exit_code']
    if result==0 and args.stage in ('feature-execution-v0249','feature-agent-v0252'):
        if shutil.which('node'):
            node=run(['node','--test',*(str(x.relative_to(ROOT)) for x in sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs')))],env,timeout=90)
            report['checks'].append({'kind':'frontend_node',**node});result=node['exit_code']
        else:
            report['checks'].append({'kind':'frontend_node','exit_code':2,'tail':'Node unavailable'})
            result=2
    if result==0 and args.mode=='configured':
        blockers=[]
        if platform.python_version()!='3.12.12':blockers.append('Python 3.12.12 is required')
        for dep in ('duckdb','pyarrow'):
            if not source_deps[dep]:blockers.append(dep+' missing')
        if not chrome:blockers.append('installed Chrome/Chromium missing')
        if not native_ready:blockers.append('native Perspective JS/WASM missing')
        if blockers:
            report['checks'].append({'kind':'real_environment_gate','exit_code':2,'blockers':blockers})
            report['status']='blocked_environment';result=2
        else:
            real=run([sys.executable,'-m','pytest','-q',P+'test_featurelab2_real_duckdb_v0249.py'],env,timeout=180)
            report['checks'].append({'kind':'real_duckdb_numeric','required':True,**real})
            result=real['exit_code']
            if result==0:
                native=run([sys.executable,'scripts/verify_native_bi_release.py','--require-python31212',
                            '--require-duckdb','--require-build','--require-source-current','--require-virtual-server'],env,timeout=130)
                report['checks'].append({'kind':'native_release_gate',**native})
                result=native['exit_code']
            if result==0:
                live=run([sys.executable,'scripts/accept_native_bi_live.py','--max-seconds','220'],env,timeout=235)
                report['checks'].append({'kind':'native_browser_live',**live})
                result=live['exit_code']
            report['real_e2e']=result==0
    if report['status']=='running':
        report['status']='passed_source_and_component' if result==0 and args.mode=='offline' else (
            'passed_configured_e2e' if result==0 else 'failed')
    out=args.output or ROOT/'reports'/'featurelab'/'v0252'/args.stage/'acceptance.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'stage':args.stage,'mode':args.mode,'status':report['status'],
          'evidence':str(out),'steps':len(report['checks']),'real_e2e':report['real_e2e']},ensure_ascii=False),flush=True)
    for step in report['checks']:
        if step.get('exit_code'):
            print(step.get('tail',step.get('blockers')),flush=True)
    return result

if __name__=='__main__':raise SystemExit(main())
