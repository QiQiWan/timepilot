"""Fail-closed native BI readiness check.

Normal launch calls --status-only and never downloads/install/builds anything.
Explicit --auto-build flags remain for legacy build-host scripts only.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import hashlib
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle

BUNDLE=ROOT/'src/timepilot/static/native-bi'
FRONTEND=ROOT/'frontend/native-bi'


def readiness():
    result=bundle_status(BUNDLE)
    source=frontend_matches_bundle(BUNDLE,FRONTEND)
    missing=[]
    for name in ('duckdb','pyarrow','perspective'):
        if importlib.util.find_spec(name) is None: missing.append(name)
    virtual=False
    if not missing:
        try:
            from perspective.virtual_servers.duckdb import DuckDBVirtualServer
            from perspective.handlers.starlette import PerspectiveStarletteHandler
            virtual=callable(DuckDBVirtualServer) and callable(PerspectiveStarletteHandler)
        except (ImportError,AttributeError,OSError,RuntimeError):pass
    return result,source,missing,virtual


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--auto-build',action='store_true')
    parser.add_argument('--auto-bootstrap',action='store_true', help='First run may install pinned npm dependencies automatically')
    parser.add_argument('--status-only',action='store_true')
    args=parser.parse_args()
    result,source,missing,virtual=readiness()
    if result['ready'] and source and not missing and virtual:
        print('[BI READY] Perspective JS/CSS/WASM checksum + source fingerprint + Python Virtual Server',flush=True)
        return 0
    build_exists=(BUNDLE/'perspective-build.json').is_file()
    print('[BI CHECK] Offline bundle: '+('valid' if result['ready'] else 'missing/not built' if not build_exists else 'damaged')+
          ' | frontend source: '+('current' if source else 'not built' if not build_exists else 'outdated')+
          ' | Python imports: '+('ok' if not missing else ', '.join(missing)), flush=True)
    if args.status_only:
        for msg in result['problems'][:5]:print(' - '+msg)
        if not build_exists:
            print('[NEXT] Source-only archive: on an online Mac with Node 24, run '
                  '`bash TIMEPILOT.sh bi-setup --bootstrap-lock`. '
                  'No Python environment or data directory is recreated.')
        return 1
    if (not result['ready'] or not source) and args.auto_build:
        # The source distribution deliberately omits node_modules and JS/WASM.
        # A normal first start must therefore bootstrap them in the same command.
        # In offline mode, only already installed dependencies may be used.
        npm=shutil.which('npm')
        offline=os.environ.get('TIMEPILOT_OFFLINE','').lower() in {'1','true','yes'}
        installed=(FRONTEND/'node_modules').is_dir()
        if not npm:
            print('[BI BUILD] Node/npm not found. Install Node.js 24 and retry bash TIMEPILOT.sh.',file=sys.stderr)
        elif offline and not installed:
            print('[BI BUILD] Offline mode enabled and no local npm dependencies found.',file=sys.stderr)
        elif installed or (args.auto_bootstrap and not offline):
            print('[BI BUILD] First-start repair: compile and verify Perspective with existing Node/npm.',flush=True)
            if not installed and args.auto_bootstrap:
                print('[BI BUILD] One-time npm dependency installation will be performed. '
                      'No Python environment changes or browser downloads.',flush=True)
            env=os.environ.copy()
            if offline or not args.auto_bootstrap:
                env['TIMEPILOT_SKIP_NPM_INSTALL']='1'
            else:
                env.pop('TIMEPILOT_SKIP_NPM_INSTALL',None)
            # Prevent two shell launches from corrupting a shared OneDrive bundle.
            try:
                from timepilot.build_lock import exclusive_build_lock
                with exclusive_build_lock(Path(tempfile.gettempdir())/('timepilot-bi-'+hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:16]+'.lock')):
                    # Another process may have built it while waiting for the lock.
                    result,source,missing,virtual=readiness()
                    if not (result['ready'] and source):
                        completed=subprocess.run([sys.executable,'scripts/build_native_bi.py'],
                                                  cwd=ROOT,env=env,check=False)
                        if completed.returncode:
                            print('[BI BUILD FAILED] Existing valid bundle was kept. Check npm/network '
                                  'and the frontend build log.',file=sys.stderr)
                            return 3
            except (OSError,TimeoutError) as exc:
                print('[BI BUILD FAILED] Cannot acquire build lock: '+str(exc),file=sys.stderr)
                return 3
            result,source,missing,virtual=readiness()
            if result['ready'] and source and not missing and virtual:
                print('[BI READY] Automatically built and verified.',flush=True)
                return 0
    print('[BI BLOCKED] Full application startup was stopped before publishing an incomplete BI.',file=sys.stderr)
    for msg in result['problems'][:6]:print(' - '+msg,file=sys.stderr)
    if missing:print(' - Missing in CURRENT Python environment: '+', '.join(missing),file=sys.stderr)
    if not source:print(' - Source-to-bundle checksum mismatch.',file=sys.stderr)
    if not virtual:print(' - Perspective DuckDB Virtual Server import not verified.',file=sys.stderr)
    print('Check Node/npm 24, network availability on first start, or use TIMEPILOT_OFFLINE=1 with a prebuilt offline distribution.',file=sys.stderr)
    print('Run `bash TIMEPILOT.sh bi-setup --bootstrap-lock` on an online build machine with Node 24. '
          'Normal startup does not install dependencies.',file=sys.stderr)
    return 2


if __name__=='__main__':
    raise SystemExit(main())
