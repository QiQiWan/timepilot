"""Exercise verified complete-offline ZIP in a clean temporary data directory.

Uses the *existing* target CPython 3.12.12 environment and installed Chrome;
no virtualenv, npm download, or changes to TIMEPILOT_HOME. The wheelhouse is
checked using the bundled offline resolver, but is not installed into the
user environment. Tests run against files extracted from the *actual ZIP*.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from verify_offline_archive_v0388 import ArchiveProblem, verify_archive



def run_stage(command: list[str], label: str, *, cwd: Path, env: dict,
              timeout: int) -> dict:
    """Bound a process tree and its output; children may inherit stdout handles."""
    start=time.monotonic()
    with tempfile.TemporaryFile(mode='w+b') as output:
        try:
            process=subprocess.Popen(command,cwd=cwd,env=env,stdout=output,
                                     stderr=subprocess.STDOUT,
                                     start_new_session=(os.name=='posix'),
                                     creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0))
            try:
                process.wait(timeout=timeout)
                timed_out=False
            except subprocess.TimeoutExpired:
                timed_out=True
                if os.name=='posix':
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                elif os.name=='nt':
                    try:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                                       capture_output=True,timeout=6)
                    except (OSError,subprocess.TimeoutExpired):process.kill()
                else:process.kill()
                try:process.wait(timeout=6)
                except subprocess.TimeoutExpired:pass
            output.seek(0,os.SEEK_END)
            size=output.tell()
            output.seek(max(0,size-2000))
            tail=output.read().decode('utf-8',errors='replace')[-1600:]
            status=('failed' if timed_out else 'passed' if process.returncode==0
                    else 'blocked' if process.returncode==2 else 'failed')
            return {'stage':label,'status':status,'exit_code':process.returncode,
                    'reason':'TimeoutExpired' if timed_out else None,
                    'duration_seconds':round(time.monotonic()-start,2),
                    'output_tail':tail}
        except OSError as exc:
            return {'stage':label,'status':'failed','reason':type(exc).__name__,
                    'duration_seconds':round(time.monotonic()-start,2)}


def accept(archive: Path, out: Path, *, require_target=True, timeout=450) -> int:
    report={'mode':'extracted_complete_offline_runtime',
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'python':sys.version.split()[0], 'archive':str(archive), 'steps':[]}
    try:
        identity=verify_archive(archive,require_complete=True)
    except (ArchiveProblem,OSError,zipfile.BadZipFile,KeyError,TypeError) as exc:
        report.update(status='failed',reason=f'ZIP integrity: {type(exc).__name__}: {exc}')
        code=1
    else:
        if identity['status']!='passed':
            report.update(status='blocked',reason='No complete offline distribution',archive_check=identity)
            code=2
        else:
            missing=[]
            if require_target and sys.version_info[:3]!=(3,12,12):
                missing.append('exact_CPython_3.12.12')
            for module in ('duckdb','pyarrow','perspective'):
                if importlib.util.find_spec(module) is None:
                    missing.append('runtime_module_'+module)
            if missing:
                report.update(status='blocked',missing=missing,archive_check=identity)
                code=2
            else:
                with tempfile.TemporaryDirectory(prefix='tp-offline-zip-e2e-') as temp:
                    base=Path(temp)
                    with zipfile.ZipFile(archive) as handle:
                        handle.extractall(base)
                    root=base/f"TimePilot_v{identity['version']}"
                    home=base/'fresh-home'
                    home.mkdir()
                    safe_env={**os.environ, 'TIMEPILOT_HOME':str(home),
                              'PYTHONPATH':str(root/'src'),
                              'PIP_NO_INDEX':'1', 'TIMEPILOT_OFFLINE':'1',
                              'PYTHONDONTWRITEBYTECODE':'1', 'npm_config_offline':'true'}
                    # Never inherit the caller's production database pointer.
                    for name in ('TIMEPILOT_DB_URL','DATABASE_URL','TIMEPILOT_DATA_DIR'):
                        safe_env.pop(name,None)
                    stages=[
                        ('extracted_source_integrity',['scripts/verify_installation.py','--source-only'],80),
                        ('extracted_native_bundle',['scripts/verify_native_bi_release.py','--require-python31212',
                           '--require-duckdb','--require-build','--require-source-current',
                           '--require-virtual-server'],60),
                        ('extracted_offline_wheel_resolver',['scripts/offline_wheels.py','verify',
                           '--wheelhouse',str(root/'offline-wheelhouse')],360),
                        ('extracted_parquet_crash',['scripts/native_bi_physical_crash_acceptance.py',
                           '--require-target','--out',str(base/'parquet_crash.json')],200),
                        ('extracted_parquet_integrity',['scripts/v0387_physical_integrity_acceptance.py',
                           '--require-target','--out',str(base/'registered_integrity.json')],120),
                        ('extracted_dashboard_sql',['scripts/featurelab377_dashboard_acceptance.py',
                           '--out',str(base/'dashboard_sql.json')],300),
                        ('extracted_chrome_30_switches',['scripts/featurelab371_chrome_cycles.py',
                           '--out',str(base/'chrome_cycles.json'),'--timeout','240'],360),
                    ]
                    code=0
                    for label,args,limit in stages:
                        result=run_stage([sys.executable,*args],label,cwd=root,
                                         env=safe_env,timeout=min(timeout,limit))
                        report['steps'].append(result)
                        if result['status']!='passed':
                            code=2 if result['status']=='blocked' else 1
                            break
                    report['status']='passed' if code==0 else 'blocked' if code==2 else 'failed'
                    report['complete']=code==0
    out.parent.mkdir(parents=True,exist_ok=True)
    part=out.with_suffix(out.suffix+'.tmp')
    part.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(part,out)
    print(json.dumps({'status':report['status'],'receipt':str(out),'steps':len(report['steps'])},ensure_ascii=False))
    return code


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('reports/v0388/extracted_offline_runtime.json'))
    p.add_argument('--require-target',action='store_true')
    args=p.parse_args(argv)
    return accept(args.archive,args.out,require_target=args.require_target)


if __name__=='__main__':
    raise SystemExit(main())
