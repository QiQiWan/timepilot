"""Explicitly gated real DuckDB/Arrow/Perspective acceptance of v0.23.5.

Source/fixture tests live in the other targets. This target refuses to present
an offline mock or skipped dependency check as production acceptance.
"""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    missing=[name for name in ('duckdb','pyarrow','playwright','fastapi') if importlib.util.find_spec(name) is None]
    native_bundle=ROOT/'src/timepilot/static/native-bi/perspective-build.json'
    if not native_bundle.is_file():missing.append('compiled local Perspective JS/WASM bundle')
    if missing:
        print(json.dumps({'status':'blocked','acceptance':'bi-fuzzy-v0235-real',
          'reason':'Required actual runtime dependencies are missing',
          'missing':missing,'source_only_tests_are_not_real_acceptance':True},ensure_ascii=False,indent=2),file=sys.stderr)
        return 2
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
    tasks=[
        [sys.executable,'-m','pytest','-q','-rs','tests/test_bi_correlation_duckdb_v0235.py'],
        [sys.executable,'scripts/verify_native_bi_release.py','--require-duckdb','--require-build',
           '--require-source-current','--require-virtual-server'],
        [sys.executable,'scripts/system_chrome.py','--check'],
        [sys.executable,'scripts/accept_native_bi_live.py','--max-seconds',
            env.get('TIMEPILOT_ACCEPTANCE_MAX_SECONDS','300')],
    ]
    for command in tasks:
        print('[REAL ACCEPT]',' '.join(command),flush=True)
        result=subprocess.run(command,cwd=ROOT,env=env,check=False)
        if result.returncode:return result.returncode
    # Existing native live and statistical acceptance are separate paths;
    # they do not prove the *linked* fuzzy request -> matrix -> heatmap journey.
    print('[PARTIAL] Real DuckDB numerical and Native Perspective acceptance passed separately. '
          'Linked fuzzy-plan -> rendering -> save/reload Chrome evidence remains required.',flush=True)
    return 3

if __name__=='__main__':raise SystemExit(main())
