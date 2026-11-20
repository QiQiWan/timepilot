"""Fail-closed v0.37.5-7 physical SQL parity gate (real DuckDB and Parquet)."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from release_integrity import project_version
FIXED=ROOT/'benchmarks/featurelab_v0372/FIXED_DASHBOARD.csv'
PIN=ROOT/'benchmarks/featurelab_v0372/MANIFEST.json'


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--out',default='reports/v0377/physical_dashboard.json')
    a=p.parse_args(argv)
    out=Path(a.out);out=out if out.is_absolute() else ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True)
    pin=json.loads(PIN.read_text())
    sha=hashlib.sha256(FIXED.read_bytes()).hexdigest()
    report={'version':project_version(ROOT),'at':datetime.now(timezone.utc).isoformat(),
        'python':sys.version.split()[0],'status':'blocked','fixture_sha256':sha,
        'pinned_fixture_passed':sha==pin['sha256'],
        'physical_gate':'required','steps':[]}
    if sha!=pin['sha256']:
        report.update(status='failed',reason='Fixed source SHA256 mismatch');code=1
    elif importlib.util.find_spec('duckdb') is None:
        report.update(reason='DuckDB missing; real Parquet cannot be executed here');code=2
    else:
        cmd=[sys.executable,'-m','pytest','-q','-rs',
             'tests/test_featurelab372_real_duckdb.py','tests/test_featurelab377_real_duckdb.py', 'tests/test_featurelab378_physical.py']
        env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
        task=subprocess.run(cmd,cwd=ROOT,env=env,text=True,capture_output=True)
        transcript=(task.stdout+'\n'+task.stderr)[-35000:]
        report['steps'].append({'stage':'real_duckdb_parquet_sql','exit_code':task.returncode,'output':transcript})
        if task.returncode==0 and 'skipped' not in transcript and 'passed' in transcript:
            report['status']='passed';code=0
        else:
            report.update(status='failed',reason='Real DuckDB numerical parity assertion failed or was skipped');code=1
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'exit':code,'out':str(out)},ensure_ascii=False))
    return code

if __name__=='__main__':raise SystemExit(main())
