"""Run source-faithful multiregion summary and optional-model fallback acceptance."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
    args=[sys.executable,'-m','pytest','-q','-rs','tests/test_import_summary_v0234.py',
          'tests/test_import_summary_llm_fallback_v0234.py']
    print('[RUN] Evidence-based multiregion summary and offline LLM fallback',flush=True)
    return subprocess.call(args,cwd=ROOT,env=env)

if __name__=='__main__':raise SystemExit(main())
