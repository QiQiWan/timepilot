"""v0.37.3 deterministic evidence QA; no hidden installation or mock SQL.

Exit 0 only if the online, actual DuckDB/Parquet numerical test also passes.
Exit 2 when offline evidence passed but physical engine is unavailable.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'benchmarks/featurelab_v0372/FIXED_DASHBOARD.csv'
MANIFEST=ROOT/'benchmarks/featurelab_v0372/MANIFEST.json'


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='reports/v0373/evidence_acceptance.json')
    opts=parser.parse_args(argv)
    output=Path(opts.out)
    if not output.is_absolute():output=ROOT/output
    output.parent.mkdir(parents=True,exist_ok=True)
    steps=[]
    report={'version':'0.37.3','created_at':datetime.now(timezone.utc).isoformat(),
            'python':sys.version.split()[0], 'source_kind':'synthetic_fixed',
            'steps':steps,'status':'blocked'}
    try:
        manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
        digest=hashlib.sha256(DATA.read_bytes()).hexdigest()
        assert digest==manifest['sha256']
        with DATA.open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
        assert len(rows)==manifest['rows']==12
        values=[float(r['measurement']) for r in rows if r['measurement'].strip()]
        assert len(values)==11 and sum(values)/len(values)==20.0
        steps.append({'stage':'independent_python_fixed_csv','status':'passed',
                      'sha256':digest,'rows':len(rows),'valid_measurements':len(values),
                      'mean_measurement':sum(values)/len(values)})
        env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+os.environ.get('PYTHONPATH','')}
        suites=[(['tests/test_featurelab373_evidence.py'],'python_evidence_contract'),
                (['frontend/native-bi/tests/featurelab373_evidence_ui.test.mjs'],'frontend_evidence_contract')]
        for target,stage in suites:
            cmd=[sys.executable,'-m','pytest','-q',*target] if stage.startswith('python') else ['node','--test',*target]
            process=subprocess.run(cmd,env=env,cwd=ROOT,stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,text=True,timeout=120)
            result={'stage':stage,'status':'passed' if process.returncode==0 else 'failed',
                    'exit_code':process.returncode,'output_tail':process.stdout[-5000:]}
            steps.append(result)
            if process.returncode:raise ValueError(stage+' failed')
        if importlib.util.find_spec('duckdb') is None:
            steps.append({'stage':'actual_duckdb_parquet','status':'blocked',
                          'reason':'DuckDB unavailable; physical SQL was not executed'})
            report['status']='blocked';exit_code=2
        else:
            p=subprocess.run([sys.executable,'-m','pytest','-q','-rs',
                  'tests/test_featurelab372_real_duckdb.py'],cwd=ROOT,env=env,
                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
            passed=p.returncode==0 and 'skipped' not in p.stdout and 'passed' in p.stdout
            steps.append({'stage':'actual_duckdb_parquet',
                          'status':'passed' if passed else 'failed',
                          'exit_code':p.returncode,'output_tail':p.stdout[-5000:]})
            report['status']='passed' if passed else 'failed'
            exit_code=0 if passed else 1
    except Exception as exc:
        report.update(status='failed',error=type(exc).__name__+': '+str(exc));exit_code=1
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'output':str(output),'exit_code':exit_code},ensure_ascii=False))
    return exit_code

if __name__=='__main__':raise SystemExit(main())
