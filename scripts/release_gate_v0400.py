"""v0.40.0 source, model-UX regression and strict deployment blocker receipts.

Uses the existing Python interpreter and installed Node; never fetches or
installs dependencies. Old physical/browser/offline gates are delegated with
all environment blocks intact, AFTER passing the new source acceptance.
"""
from __future__ import annotations

import argparse
import os
import signal
import tempfile
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['source','physical','browser','offline'],default='source')
    p.add_argument('--archive',type=Path)
    p.add_argument('--wheelhouse',type=Path)
    p.add_argument('--out',type=Path,default=Path('reports/v0400/release_gate.json'))
    a=p.parse_args()
    destination=a.out if a.out.is_absolute() else ROOT/a.out
    destination.parent.mkdir(parents=True,exist_ok=True)
    report={'version':'0.40.0','mode':a.mode,'utc':datetime.now(timezone.utc).isoformat(),
            'python':sys.version.split()[0],'stages':[],'status':'in_progress'}

    def save():
        tmp=destination.with_suffix(destination.suffix+'.pending')
        tmp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        tmp.replace(destination)

    def stage(name,command,seconds=120):
        # A regular file prevents inherited stdout pipes from keeping
        # communicate() alive after the direct pytest process has exited.
        began=time.monotonic()
        entry={'name':name,'status':'in_progress'}
        report['stages'].append(entry)
        save()
        try:
            with tempfile.TemporaryFile(mode='w+b') as log:
                proc=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                    start_new_session=(os.name=='posix'),
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0))
                timed_out=False
                try:
                    proc.wait(timeout=seconds)
                except subprocess.TimeoutExpired:
                    timed_out=True
                    if os.name=='posix':
                        try:os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                    elif os.name=='nt':
                        try:subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                                           capture_output=True,timeout=6)
                        except (OSError,subprocess.TimeoutExpired):proc.kill()
                    else:proc.kill()
                    try:proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:pass
                log.flush();log.seek(0,os.SEEK_END)
                log.seek(max(0,log.tell()-2500))
                trace=log.read().decode('utf-8',errors='replace')
                entry.update({'status':'failed' if timed_out else
                              'passed' if proc.returncode==0 else
                              'blocked' if proc.returncode==2 else 'failed',
                              'exit_code':proc.returncode,
                              'output_tail':trace})
                if timed_out:entry['reason']='hard_timeout'
        except OSError as exc:
            entry.update({'status':'blocked','reason':type(exc).__name__+': '+str(exc)})
        entry['elapsed_seconds']=round(time.monotonic()-began,2)
        save()
        print(f'[v0.40.0] {name} {entry["status"]} ({entry["elapsed_seconds"]}s)',flush=True)
        return entry['status']=='passed'

    pytests=[
        'tests/test_model_task_designer_v0389.py',
        'tests/test_model_task_contract_v0390.py',
        'tests/test_model_task_api_integration_v0390.py',
        'tests/test_model_task_execution_v0391.py',
        'tests/test_model_task_pit_v0392.py',
        'tests/test_model_task_temporal_bridge_v0392.py',
        'tests/test_model_task_unified_runs_v0393.py',
        'tests/test_model_task_time_and_results_v0395.py',
        'tests/test_model_worker_predictions_v0396.py',
        'tests/test_model_experiment_control_v0400.py',
    ]
    base_ok=stage('signed_source_integrity',
                  [sys.executable,'scripts/verify_installation.py','--source-only'],35)
    if base_ok:
        py_ok=stage('real_csv_sqlite_sklearn_model_regressions',
                    [sys.executable,'-m','pytest','-q',*pytests],480)
        # Invoke the glob using cwd expansion-independent, deterministic paths.
        if py_ok:
            jsfiles=sorted(str(p.relative_to(ROOT)) for p in (ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
            js_ok=stage('entire_frontend_native_bi_node_suite',['node','--test',*jsfiles],90)
        else: js_ok=False
        base_ok=base_ok and py_ok and js_ok
    if base_ok and a.mode!='source':
        # Historical distribution gates remain authoritative for physical,
        # Chrome/Perspective and target platform offline dependencies.
        cmd=[sys.executable,'scripts/release_gate_v0393.py','--mode',a.mode,
             '--out',str(destination.parent/('legacy_'+a.mode+'.json'))]
        if a.archive:cmd+=['--archive',str(a.archive.expanduser().resolve())]
        if a.wheelhouse:cmd+=['--wheelhouse',str(a.wheelhouse.expanduser().resolve())]
        base_ok=stage('physical_browser_offline_gate',cmd,750)
    report['status']='passed' if base_ok else (
        'blocked' if any(x['status']=='blocked' for x in report['stages']) and
            not any(x['status']=='failed' for x in report['stages']) else 'failed')
    save()
    return 0 if report['status']=='passed' else 2 if report['status']=='blocked' else 1


if __name__=='__main__':
    raise SystemExit(main())
