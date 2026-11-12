"""Explicit one-time macOS/Linux Perspective frontend setup (build host only).

Requires an already installed Node 24 and internet for npm ci.  Never installs
Python, Conda, a browser or a database.  Preserves a previously good bundle.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend/native-bi'
LOCK = FRONTEND / 'package-lock.json'


def run(args: list[str], *, cwd: Path, timeout: int) -> int:
    print('[BI SETUP] ' + ' '.join(args), flush=True)
    try:
        return subprocess.run(args, cwd=cwd, check=False, timeout=timeout).returncode
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print('[BI SETUP BLOCKED] ' + str(exc), file=sys.stderr)
        return 2


def lock_matches_package() -> bool:
    if not LOCK.is_file():
        return False
    try:
        package = json.loads((FRONTEND / 'package.json').read_text(encoding='utf-8'))
        lock = json.loads(LOCK.read_text(encoding='utf-8'))
        pinned = lock['packages']['']
        return (lock.get('lockfileVersion') in {2, 3} and
                lock.get('name') == package.get('name') and
                pinned.get('version') == package.get('version') and
                pinned.get('dependencies') == package.get('dependencies') and
                pinned.get('devDependencies') == package.get('devDependencies'))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main() -> int:
    p = argparse.ArgumentParser(description='Build the verified offline Perspective JS/WASM on a connected build machine')
    p.add_argument('--bootstrap-lock', action='store_true', help='Generate npm lock only if missing; requires network access')
    args = p.parse_args()
    sys.path.insert(0, str(ROOT / 'src'))
    from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
    bundle = ROOT / 'src/timepilot/static/native-bi'
    status = bundle_status(bundle)
    if status['ready'] and frontend_matches_bundle(bundle, FRONTEND):
        print('[BI SETUP] Existing validated Perspective JS/WASM bundle is current.')
        return run([sys.executable, 'scripts/ensure_native_bi.py', '--status-only'], cwd=ROOT, timeout=50)

    if not shutil.which('node') or not shutil.which('npm'):
        print('[BI SETUP BLOCKED] Node.js 24/npm are required on the build machine. '
              'Use an existing Node 24 installation or install it separately.', file=sys.stderr)
        return 2
    try:
        node_version = subprocess.check_output(['node', '--version'], text=True, timeout=10).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print('[BI SETUP BLOCKED] Cannot inspect Node version: ' + str(exc), file=sys.stderr)
        return 2
    if not re.fullmatch(r'v24\.\d+\.\d+', node_version):
        print('[BI SETUP BLOCKED] This frontend build requires Node 24; found ' + node_version +
              '. Select an existing Node 24 installation (e.g. nvm use 24).', file=sys.stderr)
        return 2

    if not LOCK.is_file():
        if not args.bootstrap_lock:
            print('[BI SETUP BLOCKED] package-lock.json missing. On an online build host run '
                  '`bash TIMEPILOT.sh bi-setup --bootstrap-lock`.', file=sys.stderr)
            return 2
        if os.environ.get('TIMEPILOT_OFFLINE', '').lower() in {'1', 'true', 'yes'}:
            print('[BI SETUP BLOCKED] Offline mode cannot generate a new npm lockfile.', file=sys.stderr)
            return 2
        code = run(['npm', 'install', '--package-lock-only', '--ignore-scripts',
                    '--no-audit', '--no-fund', '--save-exact'], cwd=FRONTEND, timeout=300)
        if code:
            print('[BI SETUP BLOCKED] Registry download failed; no synthetic lockfile or WASM was written. '
                  'Verify network/DNS and npm registry access.', file=sys.stderr)
            return code
    if not lock_matches_package():
        print('[BI SETUP BLOCKED] package-lock.json does not match package.json. '
              'Review/restore the lockfile before building.', file=sys.stderr)
        return 2

    code = run([sys.executable, 'scripts/build_native_bi.py'], cwd=ROOT, timeout=900)
    if code:
        return code
    status = bundle_status(bundle)
    if not status['ready'] or not frontend_matches_bundle(bundle, FRONTEND):
        print('[BI SETUP BLOCKED] JS/WASM was built but did not pass source and checksum validation.',
              file=sys.stderr)
        return 2
    print('[BI SETUP] Verified Perspective resources present. Checking current Python imports...', flush=True)
    return run([sys.executable, 'scripts/ensure_native_bi.py', '--status-only'], cwd=ROOT, timeout=60)


if __name__ == '__main__':
    raise SystemExit(main())
