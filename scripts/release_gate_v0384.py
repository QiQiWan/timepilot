"""Independent fail-closed v0.38.4 source/offline/physical/browser release gates.

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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], label: str, steps: list, *, timeout: int = 150) -> bool:
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        status = 'passed' if result.returncode == 0 else 'blocked' if result.returncode == 2 else 'failed'
        steps.append({'gate': label, 'status': status, 'exit_code': result.returncode,
                      'output_tail': (result.stdout + '\n' + result.stderr)[-3000:]})
        return status == 'passed'
    except (OSError, subprocess.TimeoutExpired) as exc:
        steps.append({'gate': label, 'status': 'blocked', 'reason': type(exc).__name__})
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['source', 'offline', 'physical', 'browser'], default='source')
    parser.add_argument('--wheelhouse', default=os.environ.get('TIMEPILOT_WHEELHOUSE', 'dist/wheelhouse'))
    parser.add_argument('--out', default='reports/v0384/release_gate.json')
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {'version': '0.38.4', 'mode': args.mode,
              'utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version.split()[0], 'steps': []}
    manifest = ROOT / 'docs/RELEASE_MANIFEST.json'
    if manifest.is_file():
        report['source_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    steps = report['steps']
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
    if ok:
        ok = run([sys.executable, '-m', 'pytest', '-q',
                  'tests/test_v0379_runtime_recovery.py', 'tests/test_v0379_offline_release.py',
                  'tests/test_featurelab378_contract_and_cache.py',
                  'tests/test_featurelab377_dashboard_regression.py',
                  'tests/test_featurelab380_field_scope.py',
                  'tests/test_v0381_worker_supervisor.py',
                  'tests/test_v0382_light_runtime_and_fencing.py',
                  'tests/test_project_publication_jobs_v020.py',
                  'tests/test_v0383_publication_reconciliation.py',
                  'tests/test_v0384_publication_identity.py'], 'source_python', steps, timeout=190)
    if ok:
        ok = run(['node', '--test',
                  'frontend/native-bi/tests/featurelab377_chart_race.test.mjs',
                  'frontend/native-bi/tests/featurelab378_metric_contract.test.mjs',
                  'frontend/native-bi/tests/featurelab380_task_scope.test.mjs',
                  'frontend/native-bi/tests/featurelab381_worker_diagnostics.test.mjs',
                  'frontend/native-bi/tests/featurelab383_reconciliation_ui.test.mjs'], 'source_node', steps)
    if ok:
        ok = run([sys.executable, 'scripts/featurelab382_worker_acceptance.py',
                  '--out', 'reports/v0384/worker_subprocess.json'],
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
            ok = run([sys.executable, 'scripts/featurelab377_dashboard_acceptance.py',
                      '--out', 'reports/v0384/duckdb_oracle.json'], 'physical_duckdb', steps, timeout=300)
            if ok:
                ok = run([sys.executable, 'scripts/featurelab381_performance.py',
                          '--require-target','--out','reports/v0384/physical_performance.json'],
                         'physical_performance_10k_100k_1m', steps, timeout=500)
    if ok and args.mode == 'browser':
        ok = run([sys.executable, 'scripts/verify_native_bi_release.py',
                  '--require-build', '--require-source-current',
                  '--require-duckdb', '--require-virtual-server'], 'browser_bundle', steps)
        if ok:
            ok = run([sys.executable, 'scripts/system_chrome.py', '--check'], 'installed_chrome', steps)
        if ok:
            ok = run([sys.executable, 'scripts/featurelab371_chrome_cycles.py',
                      '--out', 'reports/v0384/chrome_cycles.json', '--timeout', '240'],
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
