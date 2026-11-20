"""Real five-process worker startup and abrupt parent-loss regression.

Uses a fresh disposable TIMEPILOT_HOME; never reads or changes a user's home.
Reports only process liveness and role leases, not business records.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'reports/v0382/worker_subprocess.json')
    parser.add_argument('--baseline-json', type=Path, default=None, help='Optional independently recorded same-host baseline')
    parser.add_argument('--startup-timeout', type=float, default=45.)
    parser.add_argument('--shutdown-timeout', type=float, default=12.)
    args = parser.parse_args()
    # Standalone script works from an uninstalled source tree (CI and source ZIP).
    sys.path.insert(0, str(ROOT/'src'))
    try:
        import psutil
        from timepilot.worker_supervisor import ROLES, WorkerRegistry
    except ImportError as exc:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({'version':'0.38.2', 'status':'blocked',
                                        'missing_dependency':str(exc)})+'\n',encoding='utf-8')
        return 2

    result = {'version':'0.38.2','utc':datetime.now(timezone.utc).isoformat(),
              'test':'five_real_workers_parent_sigkill','status':'failed',
              'roles_required':list(ROLES), 'all_roles_online':False,
              'children_exited_after_parent_kill':False,'leases_released':False,
              'problems':[]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.out.with_suffix('.log')
    with tempfile.TemporaryDirectory(prefix='timepilot-v0382-pool-qa-') as home, log_path.open('w',encoding='utf-8') as log:
        env = dict(os.environ, TIMEPILOT_HOME=home,
                   PYTHONPATH=str(ROOT/'src')+os.pathsep+os.environ.get('PYTHONPATH',''))
        startup_begin=time.monotonic()
        parent = subprocess.Popen([sys.executable,str(ROOT/'scripts/worker_pool.py'),
                                  '--poll-seconds','0.2'],cwd=ROOT,env=env,
                                  stdout=log,stderr=subprocess.STDOUT)
        children = []
        try:
            registry = WorkerRegistry(home)
            deadline = time.monotonic()+args.startup_timeout
            while time.monotonic()<deadline:
                if parent.poll() is not None:
                    result['problems'].append('pool_exited_before_all_roles_started')
                    break
                state=registry.snapshot()
                if state['online_count']==len(ROLES):
                    result['all_roles_online']=True
                    result['startup_seconds']=round(time.monotonic()-startup_begin,2)
                    break
                time.sleep(.15)
            if not result['all_roles_online']:
                result['problems'].append('incomplete_worker_startup')
            else:
                children=psutil.Process(parent.pid).children(recursive=True)
                result['child_count']=len(children)
                # Observed idle worker RSS on the current host; excludes API,
                # does not imply target Python/production workload performance.
                rss = []
                pss = []
                for child in children:
                    try:
                        rss.append(round(child.memory_info().rss / (1024**2), 1))
                        full=child.memory_full_info()
                        if hasattr(full,'pss'):
                            pss.append(round(full.pss/(1024**2),1))
                    except psutil.NoSuchProcess:
                        pass
                result['idle_child_rss_mib'] = rss
                result['idle_child_rss_sum_mib'] = round(sum(rss), 1)
                result['idle_child_pss_mib'] = pss if len(pss)==len(rss) else None
                result['idle_child_pss_sum_mib'] = round(sum(pss),1) if len(pss)==len(rss) else None
                if len(children)<len(ROLES):
                    result['problems'].append('fewer_worker_processes_than_roles')
                parent.kill()
                parent.wait(timeout=5)
                deadline=time.monotonic()+args.shutdown_timeout
                while time.monotonic()<deadline:
                    living=[]
                    for p in children:
                        try:
                            if p.is_running() and p.status()!=psutil.STATUS_ZOMBIE:
                                living.append(p.pid)
                        except psutil.NoSuchProcess:
                            pass
                    if not living:
                        result['children_exited_after_parent_kill']=True
                    if registry.snapshot()['online_count']==0:
                        result['leases_released']=True
                    if result['children_exited_after_parent_kill'] and result['leases_released']:
                        break
                    time.sleep(.15)
                if not result['children_exited_after_parent_kill']:
                    result['problems'].append('orphan_worker_not_exited')
                if not result['leases_released']:
                    result['problems'].append('worker_lease_not_released')
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            result['problems'].append(type(exc).__name__)
        finally:
            if parent.poll() is None:
                parent.terminate()
                try: parent.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    parent.kill(); parent.wait(timeout=5)
            for p in children:
                try:
                    if p.is_running() and p.status()!=psutil.STATUS_ZOMBIE:
                        p.kill()
                except psutil.NoSuchProcess:
                    pass
    if (result['all_roles_online'] and result['children_exited_after_parent_kill']
            and result['leases_released'] and result.get('child_count',0)>=len(ROLES)
            and not result['problems']):
        result['status']='passed'
    if args.baseline_json is not None and args.baseline_json.is_file():
        baseline=json.loads(args.baseline_json.read_text(encoding='utf-8'))
        if baseline.get('status')=='passed' and baseline.get('child_count')==len(ROLES):
            old=baseline.get('idle_child_rss_sum_mib')
            new=result.get('idle_child_rss_sum_mib')
            if old is not None and new is not None and old>0:
                result['comparison']={
                    'baseline_rss_sum_mib':old,'candidate_rss_sum_mib':new,
                    'rss_reduction_percent':round(100*(old-new)/old,1),
                    'note':'Idle RSS only, same-host baseline supplied; shared pages may be double-counted',
                }
    result['log_file'] = log_path.name
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'out':str(args.out)},ensure_ascii=False))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
