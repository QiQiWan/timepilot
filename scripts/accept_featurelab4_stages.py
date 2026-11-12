"""FeatureLab v0.25.7-v0.26.0 auditable acceptance, explicit real-engine gates.

'configured' never reports an unexecuted numerical or browser test as passing.
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
TESTS={
    'feature-audit-v0257':[
      'tests/test_featurelab4_security_v0257.py',
      'tests/test_featurelab3_safety_v0253.py',
      'tests/test_featurelab3_versions_v0255.py'],
    'feature-engine-v0258':[
      'tests/test_featurelab4_security_v0257.py',
      'tests/test_featurelab3_preview_v0254.py',
      'tests/test_featurelab3_safety_v0253.py',
      'tests/test_featurelab2_contract_registry_v0247.py'],
    'feature-industrial-v0259':[
      'tests/test_featurelab4_industrial_v0259.py',
      'tests/test_featurelab2_temporal_and_evaluation_v0250.py',
      'tests/test_featurelab3_safety_v0253.py'],
    'feature-agent-e2e-v0260':[
      'tests/test_featurelab4_security_v0257.py',
      'tests/test_featurelab4_industrial_v0259.py',
      'tests/test_featurelab4_agent_receipts_v0260.py',
      'tests/test_featurelab3_agent_v0256.py',
      'tests/test_featurelab2_agent_workflows_v0252.py',
      'tests/test_featurelab3_versions_v0255.py'],
}


def check(args,env,seconds=120):
    try:
        done=subprocess.run(args,cwd=ROOT,env=env,text=True,capture_output=True,
           check=False,timeout=seconds)
        return {'command':args,'exit_code':done.returncode,'output_tail':
           (done.stdout+'\n'+done.stderr)[-10000:]}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'command':args,'exit_code':2,'output_tail':f'{type(exc).__name__}: {exc}'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',required=True,choices=TESTS)
    parser.add_argument('--mode',choices=('offline','configured'),default='offline')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    env=os.environ.copy();env['PYTHONPATH']=(str(ROOT/'src')+os.pathsep+str(ROOT/'tests')+
      os.pathsep+env.get('PYTHONPATH',''))
    chrome=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    native_built=(ROOT/'src/timepilot/static/native-bi')
    report={'stage':args.stage,'mode':args.mode,'timestamp':datetime.now(timezone.utc).isoformat(),
      'python':platform.python_version(),
      'environment':{'duckdb':bool(importlib.util.find_spec('duckdb')),
           'pyarrow':bool(importlib.util.find_spec('pyarrow')),
           'chromium':chrome,'perspective_wasm':any(native_built.rglob('*.wasm'))},
      'checks':[],'full_numeric_verified':False,'feature_perspective_e2e_verified':False}
    result=check([sys.executable,'-m','pytest','-q','-rs',*TESTS[args.stage]],env,seconds=180)
    report['checks'].append({'kind':'python_contract_http_sql_static',**result});rc=result['exit_code']
    if not rc and args.stage in ('feature-industrial-v0259','feature-agent-e2e-v0260'):
        result=check(['node','--test',*[str(p.relative_to(ROOT)) for p in sorted(
           (ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))]],env,seconds=90)
        report['checks'].append({'kind':'frontend_node_static',**result});rc=result['exit_code']
    if not rc and args.stage=='feature-agent-e2e-v0260':
        result=check([sys.executable,'scripts/accept_featurelab4_ui_v0260.py'],env,seconds=100)
        report['checks'].append({'kind':'chromium_http_mock_viewer_component',**result});rc=result['exit_code']
    if not rc and args.mode=='configured':
        blockers=[]
        if report['python']!='3.12.12':blockers.append('Python 3.12.12 mismatch')
        if not report['environment']['duckdb']:blockers.append('DuckDB not installed')
        if not report['environment']['pyarrow']:blockers.append('PyArrow not installed')
        if not chrome:blockers.append('System Chrome / Chromium missing')
        if not report['environment']['perspective_wasm']:blockers.append('Perspective JS/WASM not compiled')
        if blockers:
            report['checks'].append({'kind':'configured_environment','exit_code':2,'blockers':blockers})
            report['status']='blocked_environment';rc=2
        else:
            real=check([sys.executable,'-m','pytest','-q','-rs',
                 'tests/test_featurelab2_real_duckdb_v0249.py',
                 'tests/test_featurelab3_real_duckdb_v0254.py',
                 'tests/test_featurelab4_real_duckdb_v0258.py'],env,seconds=180)
            report['checks'].append({'kind':'real_duckdb_numerical',**real});rc=real['exit_code']
            report['full_numeric_verified']=not rc
            if not rc:
                native=check([sys.executable,'scripts/accept_native_bi_live.py',
                  '--max-seconds','180'],env,seconds=205)
                report['checks'].append({'kind':'native_perspective_general_browser',**native})
                rc=native['exit_code']
            # General native acceptance does not cover FeatureLab multi-panel
            # materialization, save, refresh, undo on real JS/WASM data.
            if not rc:
                report['checks'].append({'kind':'featurelab_real_perspective_end_to_end',
                     'exit_code':2,'status':'not_implemented',
                     'reason':'Requires dedicated real feature asset + Perspective browser replay'})
                report['status']='blocked_featurelab_e2e';rc=2
    if 'status' not in report:
        report['status']='passed_source_component_only' if rc==0 else 'failed'
    target=args.output or ROOT/'reports/featurelab/v0260'/args.stage/(
         'configured.json' if args.mode=='configured' else 'offline.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'stage':args.stage,'mode':args.mode,
          'status':report['status'],'report':str(target.relative_to(ROOT))},ensure_ascii=False))
    return rc

if __name__=='__main__':raise SystemExit(main())
