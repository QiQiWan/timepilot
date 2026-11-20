"""First-run Perspective build with optional user-local Windows Node 24 bootstrap.

The command preserves verified JS/WASM, the Python/Conda environment and data;
network access is required only for a missing Node/npm build or unsigned npm lock.
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
    # A top-level package.json comparison misses missing Rollup native
    # optionalDependencies (the exact macOS/npm EUSAGE reported by the user).
    from npm_lock_health import lock_issues
    return not lock_issues(FRONTEND)


def main() -> int:
    p = argparse.ArgumentParser(description='Build the verified offline Perspective JS/WASM on a connected build machine')
    p.add_argument('--bootstrap-lock', action='store_true', help='Repair only an unsigned missing/incomplete npm lock on first build')
    p.add_argument('--auto-node', action='store_true', help='Use or download official Node 24 into a user-local cache on Windows')
    args = p.parse_args()
    sys.path.insert(0, str(ROOT / 'src'))
    from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
    bundle = ROOT / 'src/timepilot/static/native-bi'
    status = bundle_status(bundle)
    if status['ready'] and frontend_matches_bundle(bundle, FRONTEND):
        print('[BI SETUP] Existing validated Perspective JS/WASM bundle is current.')
        return run([sys.executable, 'scripts/ensure_native_bi.py', '--status-only'], cwd=ROOT, timeout=50)

    if args.auto_node:
        from node_toolchain import NodeToolchainError, ensure_node24
        try:
            runtime_env, runtime_version, origin = ensure_node24(
                os.environ, allow_download=True)
        except NodeToolchainError as exc:
            print('[BI SETUP BLOCKED] ' + str(exc), file=sys.stderr)
            print('Connect to nodejs.org for first build or set TIMEPILOT_NODE_HOME '
                  'to an existing Node 24 directory. No Python/data changes were made.', file=sys.stderr)
            return 2
        # Only this Python process and descendants receive the adjusted PATH.
        os.environ.update(runtime_env)
        print('[BI SETUP] Node ' + runtime_version + ' selected (' + origin + ')', flush=True)
    if not shutil.which('node') or not shutil.which('npm'):
        print('[BI SETUP BLOCKED] Node.js 24/npm missing. '
              'Use `TIMEPILOT.bat start` for automatic Windows setup.', file=sys.stderr)
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

    from npm_lock_health import lock_issues, repair_lock, LockRepairError
    initial_issues = lock_issues(FRONTEND)
    if initial_issues:
        print('[BI LOCK CHECK] ' + '; '.join(initial_issues[:12]), flush=True)
        if not args.bootstrap_lock:
            print('[BI SETUP BLOCKED] npm lock is missing/incomplete. Run `TIMEPILOT.bat start` '
                  'for automatic unsigned-lock bootstrap.', file=sys.stderr)
            return 2
        try:
            repaired = repair_lock(FRONTEND, root=ROOT)
            print('[BI LOCK REPAIRED] ' + json.dumps(repaired, ensure_ascii=False), flush=True)
        except (LockRepairError, OSError, ValueError) as exc:
            print('[BI SETUP BLOCKED] Safe npm lock repair failed: ' + str(exc), file=sys.stderr)
            print('Original lock and any existing verified JS/WASM were retained.', file=sys.stderr)
            return 2
    if not lock_matches_package():
        print('[BI SETUP BLOCKED] npm lock remains inconsistent after repair.', file=sys.stderr)
        return 2

    code = run([sys.executable, 'scripts/build_native_bi.py'], cwd=ROOT, timeout=900)
    if code == 3 and args.bootstrap_lock and not initial_issues:
        # npm ci can find omissions not detectable from the known Rollup/esbuild
        # platform metadata. Repair at most once, never looping on EUSAGE.
        try:
            repaired = repair_lock(FRONTEND, root=ROOT)
            print('[BI LOCK REPAIRED AFTER npm ci] ' + json.dumps(repaired, ensure_ascii=False), flush=True)
        except (LockRepairError, OSError, ValueError) as exc:
            print('[BI SETUP BLOCKED] Safe retry failed: ' + str(exc), file=sys.stderr)
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
