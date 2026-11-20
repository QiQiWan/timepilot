"""Fail-closed release stages: source -> real DuckDB/Parquet -> actual installed Chrome.

Runs only in the active Python, using existing Node and the installed Google Chrome.
No new Python environment, npm bootstrap, hidden downloads or project data changes.
A source-only PASS is never promoted to physical/browser acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(args: list[str], *, label: str, timeout: int, results: list[dict]) -> bool:
    try:
        done=subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        success=done.returncode==0
        results.append({'step':label,'status':'passed' if success else ('blocked' if done.returncode==2 else 'failed'),
            'exit_code':done.returncode, 'output':(done.stdout+'\n'+done.stderr)[-6500:]})
        return success
    except (OSError, subprocess.TimeoutExpired) as exc:
        results.append({'step':label,'status':'blocked','error':type(exc).__name__})
        return False


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('source','physical','browser'),default='source')
    parser.add_argument('--out',default='reports/v0378/release_gate.json')
    args=parser.parse_args()
    output=Path(args.out)
    if not output.is_absolute():output=ROOT/output
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'version':'0.37.8','mode':args.mode,'at_utc':datetime.now(timezone.utc).isoformat(),
        'source':'release_manifest_sha256','python':sys.version.split()[0],
        'physical':'not_run', 'browser':'not_run', 'status':'blocked','steps':[]}
    manifest=ROOT/'docs/RELEASE_MANIFEST.json'
    if manifest.is_file():report['source_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    success=run([sys.executable,'scripts/verify_installation.py','--source-only'],label='source_integrity',timeout=35,results=report['steps'])
    if success:
        success=run([sys.executable,'-m','pytest','-q',
            'tests/test_featurelab378_contract_and_cache.py',
            'tests/test_featurelab377_dashboard_regression.py'],label='contracts_and_regression',timeout=90,results=report['steps'])
    if success:
        node=shutil.which('node')
        if node:
            success=run([node,'--test','frontend/native-bi/tests/featurelab378_metric_contract.test.mjs',
                'frontend/native-bi/tests/featurelab377_chart_race.test.mjs'],label='ui_contract_and_races',timeout=90,results=report['steps'])
        else:
            report['steps'].append({'step':'ui_contract_and_races','status':'blocked','error':'node_not_installed'})
            success=False
    if success and args.mode!='source':
        missing=[name for name in ('duckdb','pyarrow') if importlib.util.find_spec(name) is None]
        py_ok=sys.version_info[:3]==(3,12,12)
        if missing or not py_ok:
            report['steps'].append({'step':'physical_runtime','status':'blocked',
                'missing':missing,'target_python':'3.12.12','actual_python':sys.version.split()[0]})
            success=False
        else:
            success=run([sys.executable,'scripts/featurelab377_dashboard_acceptance.py',
               '--out','reports/v0378/physical_dashboard.json'],label='real_duckdb_parquet',
               timeout=240,results=report['steps'])
            report['physical']='passed' if success else 'failed'
    if success and args.mode=='browser':
        report['browser']='blocked'
        success=run([sys.executable,'scripts/verify_native_bi_release.py',
             '--require-python31212','--require-duckdb','--require-build',
             '--require-source-current','--require-virtual-server'],
             label='native_bundle_and_websocket',timeout=45,results=report['steps'])
        if success:
            success=run([sys.executable,'scripts/system_chrome.py','--check'],
                label='installed_chrome',timeout=30,results=report['steps'])
        if success:
            success=run([sys.executable,'scripts/featurelab371_chrome_cycles.py',
                '--out','reports/v0378/chrome_30_cycles.json','--timeout','240'],
                label='real_chrome_30_cycles',timeout=360,results=report['steps'])
        report['browser']='passed' if success else 'failed_or_blocked'
    report['status']='passed' if success else ('blocked' if any(x['status']=='blocked' for x in report['steps']) else 'failed')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'mode':args.mode,'report':str(output)},ensure_ascii=False))
    return 0 if success else (2 if report['status']=='blocked' else 1)

if __name__=='__main__':raise SystemExit(main())
