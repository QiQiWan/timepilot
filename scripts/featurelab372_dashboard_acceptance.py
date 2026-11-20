"""Independent physical v0.37.2 DuckDB dashboard acceptance, fail closed.

Result 0 = actual native SQL/Parquet assertions passed, 1 = numerical
mismatch or runtime error, 2 = required DuckDB dependency unavailable.
No fake DB adapter, package download, or automatic environment mutation.
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

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'benchmarks/featurelab_v0372/FIXED_DASHBOARD.csv'
MANIFEST=ROOT/'benchmarks/featurelab_v0372/MANIFEST.json'

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='reports/v0372/dashboard_physical.json')
    args=parser.parse_args(argv)
    dst=Path(args.out);dst=dst if dst.is_absolute() else ROOT/dst
    dst.parent.mkdir(parents=True,exist_ok=True)
    steps=[]
    report={'version':'0.37.2','source':'synthetic_fixed',
            'started_at':datetime.now(timezone.utc).isoformat(),
            'python':sys.version.split()[0],'steps':steps,'status':'blocked'}
    expected=json.loads(MANIFEST.read_text())
    actual=hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if actual!=expected['sha256']:
        report.update(status='failed',error='Pinned fixture SHA256 does not match')
        steps.append({'stage':'pinned_fixture','status':'failed'})
        code=1
    elif importlib.util.find_spec('duckdb') is None:
        steps.append({'stage':'pinned_fixture','status':'passed','sha256':actual})
        steps.append({'stage':'real_duckdb_sql_parquet','status':'blocked','reason':'DuckDB not installed'})
        code=2
    else:
        steps.append({'stage':'pinned_fixture','status':'passed','sha256':actual})
        env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
        tests=['tests/test_featurelab372_real_duckdb.py']
        cmd=[sys.executable,'-m','pytest','-q','-rs',*tests]
        p=subprocess.run(cmd,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        text=p.stdout[-30000:]
        if p.returncode==0 and 'skipped' not in text and 'passed' in text:
            steps.append({'stage':'real_duckdb_sql_parquet','status':'passed','detail':text.strip()})
            report['status']='passed';code=0
        else:
            steps.append({'stage':'real_duckdb_sql_parquet','status':'failed','detail':text.strip()})
            report.update(status='failed',error='Physical DuckDB parity assertion failed or was skipped');code=1
    dst.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'out':str(dst),'exit':code},ensure_ascii=False))
    return code

if __name__=='__main__':raise SystemExit(main())
