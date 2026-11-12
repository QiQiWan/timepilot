"""v0.25.3-v0.25.6 staged, evidence-producing FeatureLab checks.

Offline results cover real source/API tests, an installed Chromium *component*
with a mock Perspective host, and Node tests. Only configured mode may claim
real numerical/Perspective evidence, and it must fail closed on missing deps.
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
STAGES = {
    'feature-safety-v0253': [
        'tests/test_featurelab3_safety_v0253.py',
        'tests/test_featurelab2_temporal_and_evaluation_v0250.py',
        'tests/test_featurelab2_contract_registry_v0247.py',
    ],
    'feature-preview-v0254': [
        'tests/test_featurelab3_preview_v0254.py',
        'tests/test_featurelab3_safety_v0253.py',
        'tests/test_featurelab2_monitor_and_pit_v0251.py',
    ],
    'feature-perspective-v0255': [
        'tests/test_featurelab3_versions_v0255.py',
        'tests/test_featurelab3_safety_v0253.py',
        'tests/test_featurelab2_contract_registry_v0247.py',
    ],
    'feature-agent-v0256': [
        'tests/test_featurelab3_agent_v0256.py',
        'tests/test_featurelab3_safety_v0253.py',
        'tests/test_featurelab3_preview_v0254.py',
        'tests/test_featurelab3_versions_v0255.py',
        'tests/test_featurelab2_agent_workflows_v0252.py',
        'tests/test_featurelab2_temporal_and_evaluation_v0250.py',
    ],
}


def check(command: list[str], env: dict, *, seconds: int = 120) -> dict:
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, text=True,
                              capture_output=True, timeout=seconds, check=False)
        return {'command': command, 'exit_code': done.returncode,
                'tail': (done.stdout + '\n' + done.stderr)[-5000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'command': command, 'exit_code': 2,
                'tail': f'{type(exc).__name__}: {exc}'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', required=True, choices=STAGES)
    parser.add_argument('--mode', choices=('offline', 'configured'), default='offline')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    report = {
        'stage': args.stage, 'mode': args.mode,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'environment': {
            'python': platform.python_version(),
            'dependencies': {name: bool(importlib.util.find_spec(name))
                             for name in ('duckdb', 'pyarrow', 'playwright', 'pytest')},
            'chrome': shutil.which('google-chrome') or shutil.which('chromium')
                      or shutil.which('chromium-browser'),
            'native_wasm_present': any((ROOT / 'src/timepilot/static/native-bi').rglob('*.wasm')),
        },
        'checks': [], 'real_duckdb_numeric': False,
        'real_feature_viewer_e2e': False, 'status': 'running',
    }
    tests = check([sys.executable, '-m', 'pytest', '-q', '-rs', *STAGES[args.stage]], env)
    report['checks'].append({'kind': 'python_source_http_unit', **tests})
    exit_code = tests['exit_code']
    if not exit_code and args.stage in ('feature-perspective-v0255', 'feature-agent-v0256'):
        browser = check([sys.executable, 'scripts/accept_featurelab3_ui_v0255.py'], env, seconds=85)
        report['checks'].append({'kind': 'chromium_mock_viewer_component', **browser})
        exit_code = browser['exit_code']
    if not exit_code and args.stage in ('feature-perspective-v0255', 'feature-agent-v0256'):
        node_files = [str(file.relative_to(ROOT)) for file in sorted(
            (ROOT / 'frontend/native-bi/tests').glob('*.test.mjs'))]
        if node_files and shutil.which('node'):
            node = check(['node', '--test', *node_files], env, seconds=110)
            report['checks'].append({'kind': 'frontend_node_component', **node})
            exit_code = node['exit_code']
        else:
            report['checks'].append({'kind': 'frontend_node_component',
                                     'exit_code': 2, 'tail': 'Node or tests not available'})
            exit_code = 2
    if not exit_code and args.mode == 'configured':
        checks = report['environment']
        blockers = []
        if checks['python'] != '3.12.12':
            blockers.append('Target Python 3.12.12 is required')
        for dep in ('duckdb', 'pyarrow'):
            if not checks['dependencies'][dep]:
                blockers.append(f'Missing {dep}')
        if not checks['chrome']:
            blockers.append('System Chrome / Chromium not available')
        if not checks['native_wasm_present']:
            blockers.append('Perspective 5.6.1 compiled JS/WASM not present')
        if blockers:
            report['status'] = 'blocked_environment'
            report['checks'].append({'kind': 'configured_dependencies',
                                     'exit_code': 2, 'blockers': blockers})
            exit_code = 2
        else:
            real = check([sys.executable, '-m', 'pytest', '-q', '-rs',
                          'tests/test_featurelab3_real_duckdb_v0254.py',
                          'tests/test_featurelab2_real_duckdb_v0249.py'], env, seconds=200)
            report['checks'].append({'kind': 'real_duckdb_numeric', **real})
            exit_code = real['exit_code']
            report['real_duckdb_numeric'] = exit_code == 0
            if not exit_code:
                native = check([sys.executable, 'scripts/verify_native_bi_release.py',
                                '--require-python31212', '--require-duckdb',
                                '--require-build', '--require-source-current',
                                '--require-virtual-server'], env, seconds=100)
                report['checks'].append({'kind': 'native_build_gate', **native})
                exit_code = native['exit_code']
            if not exit_code:
                native_ui = check([sys.executable, 'scripts/accept_native_bi_live.py',
                                   '--max-seconds', '180'], env, seconds=205)
                report['checks'].append({'kind': 'native_browser_live_general', **native_ui})
                exit_code = native_ui['exit_code']
            # The general native BI acceptance does NOT prove FeatureLab comparison
            # viewer persistence. That needs an independent real task + screenshot run.
            if not exit_code:
                report['status'] = 'blocked_feature_viewer_e2e'
                report['checks'].append({'kind': 'feature_comparison_real_e2e', 'exit_code': 2,
                                         'tail': 'Real FeatureLab compare/save/refresh/undo Chrome E2E is not yet implemented.'})
                exit_code = 2
    if report['status'] == 'running':
        report['status'] = ('passed_source_component_only' if exit_code == 0
                            else 'failed')
    destination = args.output or (ROOT / 'reports/featurelab/v0256' / args.stage /
                                  ('acceptance_configured.json' if args.mode == 'configured'
                                   else 'acceptance.json'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'stage': args.stage, 'mode': args.mode,
                      'status': report['status'], 'evidence': str(destination),
                      'checks': len(report['checks']),
                      'real_feature_viewer_e2e': report['real_feature_viewer_e2e']},
                     ensure_ascii=False), flush=True)
    if exit_code:
        for item in report['checks']:
            if item.get('exit_code', 0):
                print(item.get('blockers', item.get('tail', 'Failure')), flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
