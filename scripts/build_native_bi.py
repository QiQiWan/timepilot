"""Build the offline Perspective browser component without touching the Python env.

The build is staged and checked before replacing the previously released assets.
The old working bundle remains available if npm or Vite fails.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'native-bi'
OUTPUT = ROOT / 'src' / 'timepilot' / 'static' / 'native-bi'


def main() -> int:
    if not shutil.which('npm'):
        print('ERROR: npm unavailable; install Node.js 24 on the build machine.', file=sys.stderr)
        return 2
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    lock = FRONTEND / 'package-lock.json'
    package = json.loads((FRONTEND / 'package.json').read_text(encoding='utf-8'))
    wanted = {**package.get('dependencies', {}), **package.get('devDependencies', {})}

    def node_modules_current() -> bool:
        if not (FRONTEND / 'node_modules').is_dir():
            return False
        try:
            check = subprocess.run(['npm', 'ls', '--depth=0', '--json'], cwd=FRONTEND,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=35)
            if check.returncode != 0:
                return False
            actual = json.loads(check.stdout).get('dependencies', {})
            return all(actual.get(name, {}).get('version') == version
                       for name, version in wanted.items())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    if not node_modules_current():
        if os.environ.get('TIMEPILOT_SKIP_NPM_INSTALL') == '1':
            print('ERROR: Frontend node_modules missing or mismatched, npm install disabled.',
                  file=sys.stderr)
            return 2
        command = (['npm', 'ci', '--no-audit', '--no-fund'] if lock.is_file()
                   else ['npm', 'install', '--no-audit', '--no-fund'])
        if not lock.is_file():
            print('[BI BUILD] First-run bootstrap will generate package-lock.json from pinned package.json; subsequent builds use npm ci.', flush=True)
        print('Installing/verifying pinned Perspective frontend packages...', flush=True)
        try:
            subprocess.run(command, cwd=FRONTEND, check=True, timeout=600)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f'ERROR: npm dependency install failed: {exc}.', file=sys.stderr)
            return 2
        if not node_modules_current():
            print('ERROR: npm completed but top-level installed versions still mismatch package.json.',
                  file=sys.stderr)
            return 2

    staging = Path(tempfile.mkdtemp(prefix='.native-bi-staging-', dir=ROOT))
    backup = OUTPUT.parent / '.native-bi-previous'
    swapped = False
    try:
        env = os.environ.copy()
        env['TIMEPILOT_NATIVE_BI_OUTDIR'] = str(staging)
        subprocess.run(['npm', 'run', 'build'], cwd=FRONTEND, env=env, check=True)
        sys.path.insert(0, str(ROOT / 'src'))
        from timepilot.native_bi_bundle import generate_manifest, bundle_status, frontend_source_hash
        result = generate_manifest(staging, frontend_sha256=frontend_source_hash(FRONTEND))
        status = bundle_status(staging)
        if not status['ready']:
            raise RuntimeError('Native BI bundle validation failed: ' + '; '.join(status['problems']))
        if not (staging / 'perspective-contract.json').is_file():
            raise RuntimeError('Compiled entry module has no verified public export contract')
        if not (staging / 'README.txt').is_file():
            raise RuntimeError('Vite public assets missing: README.txt')

        if backup.exists():
            shutil.rmtree(backup)
        if OUTPUT.exists():
            os.replace(OUTPUT, backup)
        try:
            os.replace(staging, OUTPUT)
            swapped = True
        except Exception:
            if backup.exists():
                os.replace(backup, OUTPUT)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        print(json.dumps({'bundle_ready': True,
                          'file_count': len(result['files']),
                          'output': str(OUTPUT),
                          'status': bundle_status(OUTPUT)}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: Native BI build not published: {exc}', file=sys.stderr)
        print('The previous bundle was retained. Check Vite output and WASM assets.', file=sys.stderr)
        return 1
    finally:
        if not swapped and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
