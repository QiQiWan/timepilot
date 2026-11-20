"""Fail-closed native BI readiness check.

Normal start auto-builds the verified frontend once; Windows can provision a
user-local Node 24 runtime. Subsequent starts reuse a signed bundle without npm.
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
    parser.add_argument('--auto-bootstrap',action='store_true', help='First run may bootstrap unsigned npm lock automatically')
    parser.add_argument('--auto-node',action='store_true', help='Windows: resolve/download verified user-local Node 24')
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
            print('[NEXT] Source-only archive: run `TIMEPILOT.bat start` on Windows, '
                  'or `bash TIMEPILOT.sh start` with Node 24 on macOS/Linux. '
                  'Online startup builds the verified BI bundle once.')
        return 1
    if (not result['ready'] or not source) and args.auto_build:
        offline=os.environ.get('TIMEPILOT_OFFLINE','').lower() in {'1','true','yes'}
        installed=(FRONTEND/'node_modules').is_dir()
        if offline and not installed:
            print('[BI BUILD] Offline source tree has no compiled bundle or cached dependencies. '
                  'Use an offline release bundle or first build online.',file=sys.stderr)
        else:
            from timepilot.build_lock import exclusive_build_lock
            lock=Path(tempfile.gettempdir())/('timepilot-bi-'+
                hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:16]+'.lock')
            try:
                with exclusive_build_lock(lock):
                    result,source,missing,virtual=readiness()
                    if not (result['ready'] and source):
                        env=os.environ.copy()
                        # The bootstrap script verifies Node 24, the npm lock,
                        # and builds JS/CSS/WASM to an atomic staging directory.
                        # Only a verified bundle becomes active; no fake assets.
                        command=[sys.executable,'scripts/setup_native_bi.py']
                        if args.auto_bootstrap and not offline:
                            command.append('--bootstrap-lock')
                        if args.auto_node:
                            command.append('--auto-node')
                        if offline:
                            env['TIMEPILOT_SKIP_NPM_INSTALL']='1'
                        print('[BI AUTO BUILD] Missing/stale Perspective resources; '
                              'building once with the current Node/npm and lock.',flush=True)
                        completed=subprocess.run(command,cwd=ROOT,env=env,check=False)
                        if completed.returncode:
                            print('[BI AUTO BUILD BLOCKED] Build failed. Previous verified files '
                                  'were kept. Check npm logs and access to nodejs.org/npm registry.',file=sys.stderr)
                            return 3
            except (OSError,TimeoutError) as exc:
                print('[BI AUTO BUILD BLOCKED] Build lock error: '+str(exc),file=sys.stderr)
                return 3
            result,source,missing,virtual=readiness()
            if result['ready'] and source and not missing and virtual:
                print('[BI READY] Verified new frontend bundle and active Python runtime.',flush=True)
                return 0
    print('[BI BLOCKED] Full application startup was stopped before publishing an incomplete BI.',file=sys.stderr)
    for msg in result['problems'][:6]:print(' - '+msg,file=sys.stderr)
    if missing:print(' - Missing in CURRENT Python environment: '+', '.join(missing),file=sys.stderr)
    if not source:print(' - Source-to-bundle checksum mismatch.',file=sys.stderr)
    if not virtual:print(' - Perspective DuckDB Virtual Server import not verified.',file=sys.stderr)
    print('Check Node/npm 24, network availability on first start, or use TIMEPILOT_OFFLINE=1 with a prebuilt offline distribution.',file=sys.stderr)
    print('Windows: run `TIMEPILOT.bat start` online for one-command setup. '
          'If network access is restricted, set TIMEPILOT_NODE_HOME to Node 24 or use a prebuilt bundle.',file=sys.stderr)
    return 2


if __name__=='__main__':
    raise SystemExit(main())
