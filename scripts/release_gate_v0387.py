"""Independent fail-closed v0.38.7 source/offline/physical/browser release gates.

No installation or runtime-data modification. Each stage records exact
subprocess return codes. Tests requiring hardware/browser cannot be skipped
and still produce a PASS for the corresponding gate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import time
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StageLog(list):
    """Persist every stage outcome before starting another long-lived process."""
    def __init__(self, out: Path, report: dict):
        super().__init__()
        self.out = out
        self.report = report

    def append(self, item):
        super().append(item)
        self.report['status'] = 'running'
        self.out.parent.mkdir(parents=True, exist_ok=True)
        staging = self.out.with_name(self.out.name + '.staging')
        staging.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(staging, self.out)


def run(command: list[str], label: str, steps: list, *, timeout: int = 150) -> bool:
    """Isolate test process trees and capture output in files, never in pipes.

    Some historical integration fixtures start subprocesses that inherit open
    stdout/stderr handles. Even after pytest exits, communicate() can block
    waiting for descendants to close their pipes. A regular file and wait()
    make the actual stage exit code the sole PASS criterion and ensure timeout
    is bounded independently of inherited handles.
    """
    print('[v0.38.7] START', label, flush=True)
    start = time.monotonic()
    try:
        with tempfile.TemporaryFile(mode='w+b') as output:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            proc = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=(os.name == 'posix'),
                                    creationflags=flags)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == 'posix':
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                elif os.name == 'nt':
                    try:
                        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                                       capture_output=True,timeout=6)
                    except (OSError, subprocess.TimeoutExpired): proc.kill()
                else: proc.kill()
                try: proc.wait(timeout=6)
                except subprocess.TimeoutExpired: pass
            output.flush()
            output.seek(0, os.SEEK_END)
            size = output.tell()
            output.seek(max(0, size-5000))
            tail = output.read().decode('utf-8',errors='replace')[-4000:]
            elapsed = round(time.monotonic()-start,2)
            if timed_out:
                steps.append({'gate':label,'status':'failed','reason':'TimeoutExpired',
                              'duration_seconds':elapsed,'output_tail':tail})
                print('[v0.38.7] END',label,'failed TimeoutExpired',flush=True)
                return False
            status = 'passed' if proc.returncode == 0 else 'blocked' if proc.returncode == 2 else 'failed'
            steps.append({'gate':label,'status':status,'exit_code':proc.returncode,
                          'duration_seconds':elapsed,'output_tail':tail})
            print('[v0.38.7] END',label,status,'exit=',proc.returncode,flush=True)
            return status == 'passed'
    except OSError as exc:
        steps.append({'gate':label,'status':'failed','reason':type(exc).__name__})
        print('[v0.38.7] END',label,'failed',type(exc).__name__,flush=True)
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['source', 'offline', 'physical', 'browser'], default='source')
    parser.add_argument('--wheelhouse', default=os.environ.get('TIMEPILOT_WHEELHOUSE', 'dist/wheelhouse'))
    parser.add_argument('--out', default='reports/v0387/release_gate.json')
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {'version': '0.38.7', 'mode': args.mode,
              'utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version.split()[0], 'steps': []}
    manifest = ROOT / 'docs/RELEASE_MANIFEST.json'
    if manifest.is_file():
        report['source_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    steps = StageLog(out, report)
    report['steps'] = steps
    # Check target-only prerequisites BEFORE expensive source regressions. In
    # particular, a wrong Python interpreter must not force four identical
    # source suites to run, then finally report the known environmental block.
    prereq_missing = []
    if args.mode in ('offline', 'physical', 'browser') and sys.version_info[:3] != (3, 12, 12):
        prereq_missing.append('exact_CPython_3.12.12')
    if args.mode in ('physical', 'browser', 'offline'):
        for module in ('duckdb', 'pyarrow'):
            if importlib.util.find_spec(module) is None:
                prereq_missing.append('runtime_module_' + module)
    if args.mode in ('offline', 'browser'):
        for module in ('perspective',):
            if importlib.util.find_spec(module) is None:
                prereq_missing.append('runtime_module_' + module)
        if not (ROOT / 'frontend/native-bi/package-lock.json').is_file():
            prereq_missing.append('reviewed_npm_lock')
        if not (ROOT / 'src/timepilot/static/native-bi/perspective-build.json').is_file():
            prereq_missing.append('compiled_native_bi')
    if args.mode == 'offline':
        wheelhouse = Path(args.wheelhouse).expanduser()
        if not wheelhouse.is_absolute():
            wheelhouse = ROOT / wheelhouse
        if not (wheelhouse / 'OFFLINE_WHEELS.json').is_file():
            prereq_missing.append('target_platform_wheelhouse')
    if prereq_missing:
        steps.append({'gate': 'runtime_prerequisites', 'status': 'blocked', 'missing': prereq_missing})
        ok = False
    else:
        ok = run([sys.executable, 'scripts/verify_installation.py', '--source-only'], 'source_integrity', steps)
    # Separate process lifetimes: a historical integration test can leave a
    # non-daemon thread alive after pytest prints PASS. Never infer a gate
    # result from printed dots without an actual child-process exit code.
    groups = (
        ('source_python_data', [
            'tests/test_v0379_runtime_recovery.py', 'tests/test_v0379_offline_release.py',
            'tests/test_featurelab378_contract_and_cache.py',
            'tests/test_featurelab377_dashboard_regression.py',
            'tests/test_featurelab380_field_scope.py']),
        ('source_python_workers', [
            'tests/test_v0381_worker_supervisor.py',
            'tests/test_v0382_light_runtime_and_fencing.py']),
        # A subset of historical publication fixtures may keep non-daemon
        # resources alive when files share a pytest interpreter. Every file
        # gets its own real process exit code; the gate does not infer PASS
        # from pytest progress dots or silently omit a historical test.
        ('source_python_publication_jobs', [
            'tests/test_project_publication_jobs_v020.py']),
        ('source_python_publication_reconciliation', [
            'tests/test_v0383_publication_reconciliation.py']),
        ('source_python_publication_identity', [
            'tests/test_v0384_publication_identity.py']),
        ('source_python_publication_journal', [
            'tests/test_v0385_durable_publication.py']),
        ('source_python_native_recovery', [
            'tests/test_v0386_native_recovery.py']),
        ('source_python_registered_integrity', [
            'tests/test_v0387_integrity_audit.py']),
    )
    for label, files in groups:
        if ok:
            ok = run([sys.executable, '-m', 'pytest', '-q', *files],
                     label, steps, timeout=95)
    if ok:
        ok = run(['node', '--test',
                  'frontend/native-bi/tests/featurelab377_chart_race.test.mjs',
                  'frontend/native-bi/tests/featurelab378_metric_contract.test.mjs',
                  'frontend/native-bi/tests/featurelab380_task_scope.test.mjs',
                  'frontend/native-bi/tests/featurelab381_worker_diagnostics.test.mjs',
                  'frontend/native-bi/tests/featurelab383_reconciliation_ui.test.mjs',
                  'frontend/native-bi/tests/featurelab385_publication_journal.test.mjs'], 'source_node', steps)
    if ok:
        ok = run([sys.executable, 'scripts/featurelab382_worker_acceptance.py',
                  '--out', 'reports/v0387/worker_subprocess.json'],
                 'five_role_scoped_workers_and_parent_loss', steps, timeout=110)
    if ok and args.mode == 'offline':
        wheelhouse = Path(args.wheelhouse).expanduser()
        if not wheelhouse.is_absolute():
            wheelhouse = ROOT / wheelhouse
        missing = []
        if sys.version_info[:3] != (3,12,12):
            missing.append('exact_CPython_3.12.12')
        if not (ROOT/'frontend/native-bi/package-lock.json').is_file():
            missing.append('reviewed_npm_lock')
        if not (ROOT/'src/timepilot/static/native-bi/perspective-build.json').is_file():
            missing.append('compiled_native_bi')
        if not (wheelhouse/'OFFLINE_WHEELS.json').is_file():
            missing.append('target_platform_wheelhouse')
        for module in ('duckdb','pyarrow','perspective'):
            if importlib.util.find_spec(module) is None:
                missing.append('runtime_module_'+module)
        if missing:
            steps.append({'gate':'offline_prerequisites','status':'blocked','missing':missing})
            ok = False
        if ok:
            ok = run([sys.executable, 'scripts/verify_native_bi_release.py',
                  '--require-python31212', '--require-duckdb', '--require-build',
                  '--require-source-current', '--require-virtual-server'], 'native_runtime', steps)
        if ok:
            ok = run([sys.executable, 'scripts/offline_wheels.py', 'verify',
                      '--wheelhouse', str(wheelhouse)], 'python_wheelhouse', steps, timeout=900)
    if ok and args.mode in ('physical', 'browser'):
        if sys.version_info[:3] != (3, 12, 12):
            steps.append({'gate': 'target_interpreter', 'status': 'blocked',
                          'required': 'CPython 3.12.12', 'actual': sys.version.split()[0]})
            ok = False
        else:
            ok = run([sys.executable, 'scripts/native_bi_physical_crash_acceptance.py',
                      '--require-target', '--out', 'reports/v0387/native_bi_crash.json'],
                     'physical_native_parquet_crash_windows', steps, timeout=190)
            if ok:
                ok = run([sys.executable, 'scripts/v0387_physical_integrity_acceptance.py',
                          '--require-target', '--out', 'reports/v0387/registered_integrity.json'],
                         'physical_registered_asset_integrity', steps, timeout=120)
            if ok:
                ok = run([sys.executable, 'scripts/featurelab377_dashboard_acceptance.py',
                          '--out', 'reports/v0387/duckdb_oracle.json'],
                         'physical_duckdb', steps, timeout=300)
            if ok:
                ok = run([sys.executable, 'scripts/featurelab381_performance.py',
                          '--require-target','--out','reports/v0387/physical_performance.json'],
                         'physical_performance_10k_100k_1m', steps, timeout=500)
    if ok and args.mode == 'browser':
        ok = run([sys.executable, 'scripts/verify_native_bi_release.py',
                  '--require-build', '--require-source-current',
                  '--require-duckdb', '--require-virtual-server'], 'browser_bundle', steps)
        if ok:
            ok = run([sys.executable, 'scripts/system_chrome.py', '--check'], 'installed_chrome', steps)
        if ok:
            ok = run([sys.executable, 'scripts/featurelab371_chrome_cycles.py',
                      '--out', 'reports/v0387/chrome_cycles.json', '--timeout', '240'],
                     'browser_30_cycles', steps, timeout=360)
    report['status'] = ('failed' if any(s['status']=='failed' for s in steps) else
                        'blocked' if any(s['status']=='blocked' for s in steps) else
                        'passed' if ok else 'failed')
    report['complete'] = ok
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'mode': args.mode, 'report': str(out)}, ensure_ascii=False))
    return 0 if ok else 2 if report['status'] == 'blocked' else 1


if __name__ == '__main__':
    raise SystemExit(main())
