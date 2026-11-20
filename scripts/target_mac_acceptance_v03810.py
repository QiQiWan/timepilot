"""Explicit, non-destructive Mac P0 acceptance recorder.

No installs, no project-data edits. Each gate writes its own immutable-in-run
receipt, so a later gate cannot overwrite evidence from an earlier gate.
Source gates certify code only; physical/browser/offline require the real
active target interpreter and the exact installed native BI bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('source', 'physical', 'browser', 'offline')


def _version(name: str):
    exe = shutil.which(name)
    if not exe:
        return None
    try:
        run = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=8)
        return run.stdout.strip() or run.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def inventory() -> dict:
    files = {
        'npm_lock': ROOT / 'frontend/native-bi/package-lock.json',
        'perspective_build': ROOT / 'src/timepilot/static/native-bi/perspective-build.json',
        'release_manifest': ROOT / 'docs/RELEASE_MANIFEST.json',
        'mac_launcher': ROOT / 'TIMEPILOT.sh',
    }
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ('duckdb', 'pyarrow', 'perspective', 'fastapi', 'pydantic')}
    return {
        'utc': datetime.now(timezone.utc).isoformat(),
        'system': platform.platform(),
        'machine': platform.machine(),
        'python': sys.version.split()[0],
        'python_executable': sys.executable,
        'target_python': sys.version_info[:3] == (3, 12, 12),
        'target_macos': sys.platform == 'darwin',
        'node': _version('node'), 'npm': _version('npm'),
        'node24': str(_version('node') or '').startswith('v24.'),
        'files': {k: v.is_file() for k, v in files.items()},
        'modules': modules,
        'wheelhouse_exists': (ROOT / 'dist/wheelhouse/OFFLINE_WHEELS.json').is_file(),
        'data_directory': os.environ.get('TIMEPILOT_HOME', ''),
    }


def save(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', help='Execute actual gates without installations')
    parser.add_argument('--mode', choices=('all',) + STAGES, default='all')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--out', type=Path, default=Path('reports/v0396/mac_acceptance.json'))
    parser.add_argument('--timeout', type=int, default=2400)
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    report = {'version': '0.39.6', 'environment': inventory(), 'gates': {}, 'status': 'inventory_only'}
    save(out, report)
    if not args.run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    stages = STAGES if args.mode == 'all' else (args.mode,)
    for stage in stages:
        receipt = out.parent / ('mac_' + stage + '.json')
        if stage != 'source' and sys.platform != 'darwin':
            report['gates'][stage] = {'status': 'blocked', 'reason': 'target_macOS_required'}
            report['status'] = 'blocked'
            save(out, report)
            continue
        command = [sys.executable, str(ROOT / 'scripts/release_gate_v0396.py'),
                   '--mode', stage, '--out', str(receipt)]
        if stage == 'offline':
            if args.archive:
                command += ['--archive', str(args.archive.expanduser().resolve())]
            if args.wheelhouse:
                command += ['--wheelhouse', str(args.wheelhouse.expanduser().resolve())]
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=ROOT, timeout=args.timeout, check=False)
            status = {0: 'passed', 2: 'blocked'}.get(result.returncode, 'failed')
            entry = {'status': status, 'exit_code': result.returncode,
                     'receipt': str(receipt), 'seconds': round(time.monotonic()-started, 2)}
        except subprocess.TimeoutExpired:
            entry = {'status': 'failed', 'reason': 'timeout', 'seconds': round(time.monotonic()-started, 2)}
        report['gates'][stage] = entry
        report['status'] = ('failed' if any(x['status'] == 'failed' for x in report['gates'].values()) else
                            'blocked' if any(x['status'] == 'blocked' for x in report['gates'].values()) else 'passed')
        save(out, report)
    print(json.dumps({'status': report['status'], 'receipt': str(out), 'gates': report['gates']}, ensure_ascii=False))
    return {'passed': 0, 'blocked': 2, 'failed': 1}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
