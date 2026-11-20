#!/usr/bin/env python3
"""Long-lived TimePilot QA gate: frozen data, state flows, models and physical BI.

  python scripts/featurelab362_acceptance.py --mode smoke
  python scripts/featurelab362_acceptance.py --mode full --with-browser

Never installs dependencies or rewrites an approved baseline. Full physical
checks remain BLOCKED without the required DuckDB/Parquet/native browser.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from featurelab360_acceptance import run_check, blocked, load_json
from featurelab361_acceptance import _validate_root_folder
from timepilot.featurelab361_fixtures import generate_suite,verify_suite
from timepilot.featurelab362_reference import verify_frozen_baseline, BASELINE_PATH
from timepilot.featurelab361_fixtures import digest_file

SMOKE_GATES=('frozen_data','api_state_flow','numeric_models',
             'leakage_and_qa','worker_recovery','report_and_provenance',
             'frontend_ui','strict_source')
PHYSICAL_GATES=('physical_duckdb','synthetic_parquet_e2e','native_perspective')


def summarize(gates,required):
    lookup={r['name']:r for r in gates}
    missing=[name for name in required if lookup.get(name,{}).get('status')!='passed']
    failed=[name for name in missing if lookup.get(name,{}).get('status')=='failed']
    return {'status':'failed' if failed else 'blocked' if missing else 'passed',
        'passed':[name for name in required if name not in missing],
        'failed':failed,'blocked':[name for name in missing if name not in failed]}


def main(argv=None):
    p=argparse.ArgumentParser(description='Synthetic regression QA and physical release gates')
    p.add_argument('--mode',choices=('smoke','full'),default='smoke')
    p.add_argument('--out',type=Path,default=Path('reports/featurelab362_acceptance'))
    p.add_argument('--data-dir',type=Path,default=Path('reports/featurelab362_data'))
    p.add_argument('--with-browser',action='store_true')
    p.add_argument('--with-performance',action='store_true')
    args=p.parse_args(argv)
    try:
        out=_validate_root_folder(args.out,kind='QA report folder')
        data=_validate_root_folder(args.data_dir,kind='QA fixture folder')
    except ValueError as exc:p.error(str(exc))
    if out==data or out.is_relative_to(data) or data.is_relative_to(out):
        p.error('data-dir and output must be separate paths')
    out.mkdir(parents=True,exist_ok=True)
    gates=[]
    def add(g):
        gates.append(g)
        print(g['name'],g['status'],g.get('reason',''),flush=True)
    try:
        generate_suite(data)
        manifest=verify_suite(data)
        frozen=verify_frozen_baseline(data)
        errors=(manifest.get('errors') or [])+(frozen.get('errors') or [])
        add({'name':'frozen_data','status':'passed' if not errors else 'failed',
            'reason':'8 datasets, independent numeric oracles and frozen SHA256' if not errors else '; '.join(errors[:4]),
            'baseline_sha256':frozen.get('baseline_sha256'),
            'fixture_source_sha256':digest_file(data/'MANIFEST.json')})
    except Exception as exc:
        add({'name':'frozen_data','status':'failed','reason':type(exc).__name__+': '+str(exc)[:450]})
    run_py=lambda name,tests,timeout=240:run_check(name,[sys.executable,'-m','pytest','-q',*tests],out,
        missing=('pytest',),timeout=timeout,output_format='pytest')
    if gates[-1]['status']=='passed':
        add(run_py('api_state_flow',['tests/test_featurelab362_flow.py',
            'tests/test_featurelab354_journey.py',
            'tests/test_universal_import_v0221.py::test_one_click_batch_idempotent_and_reusable_version',
            'tests/test_universal_import_v0221.py::test_partial_failure_then_retry_only_failed_region',
            'tests/test_universal_import_v0221.py::test_duplicate_blank_csv_header_preserves_raw_names_values',
            'tests/test_featurelab360_migration.py'],160))
        model=run_check('numeric_models',[sys.executable,'scripts/featurelab362_model_audit.py',
            '--data-dir',str(data),'--out',str(out/'NUMERIC_MODEL_EVIDENCE.json')],out,
            missing=('pandas','sklearn'),timeout=300)
        if model['status']=='passed' and load_json(out/'NUMERIC_MODEL_EVIDENCE.json').get('status')!='passed':
            model.update(status='failed',reason='model evidence report invalid')
        add(model)
        add(run_py('leakage_and_qa',['tests/test_featurelab362_baselines.py',
            'tests/test_featurelab362_leakage_policy.py','tests/test_featurelab361_fixtures.py',
            'tests/test_featurelab332_fixed_benchmarks.py',
            'tests/test_featurelab352_industrial_mechanisms.py',
            'tests/test_featurelab352_transfer.py'],360))
    else:
        for name in ('api_state_flow','numeric_models','leakage_and_qa'):
            add(blocked(name,'frozen QA corpus verification failed'))
    add(run_py('worker_recovery',['tests/test_featurelab360_real_lease.py',
        'tests/test_featurelab334_worker_crash.py','tests/test_featurelab333_resilience.py'],260))
    add(run_py('report_and_provenance',['tests/test_featurelab335_immutable_reports.py',
        'tests/test_featurelab355_evidence.py',
        'tests/test_featurelab356_delivery_contract.py',
        'tests/test_featurelab355_api.py'],260))
    modules=sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
    add(run_check('frontend_ui',[shutil.which('node') or 'node','--test',
        *[str(f.relative_to(ROOT)) for f in modules]],out,timeout=240,output_format='node'))
    add(run_check('strict_source',[sys.executable,'scripts/verify_installation.py',
        '--source-only','--strict-source'],out,timeout=120))
    if args.mode=='smoke':
        for name in PHYSICAL_GATES:add(blocked(name,'smoke mode; not a physical integration test'))
        add(blocked('physical_performance','smoke mode; not a physical benchmark'))
    else:
        child=out/'physical_361'
        cmd=[sys.executable,'scripts/featurelab361_acceptance.py','--mode','full',
            '--out',str(child),'--data-dir',str(data)]
        if args.with_browser:cmd.append('--with-browser')
        if args.with_performance:cmd.append('--with-performance')
        status=run_check('existing_physical_pipeline',cmd,out,timeout=1400)
        report=load_json(child/'ACCEPTANCE.json')
        by_name={x['name']:x for x in report.get('results',[])}
        for name,upstream in [('physical_duckdb','duckdb_parquet'),
                              ('synthetic_parquet_e2e','synthetic_pipeline'),
                              ('native_perspective','native_browser'),
                              ('physical_performance','physical_performance')]:
            details=by_name.get(upstream)
            add({'name':name,'status':details.get('status','blocked') if details else 'blocked',
                'reason':details.get('reason','missing physical evidence') if details else 'missing physical evidence',
                'upstream_gate':upstream})
        if not report:
            print('Physical downstream runner:',status['status'],status['reason'])
    functional=summarize(gates,SMOKE_GATES)
    physical=summarize(gates,PHYSICAL_GATES)
    performance=next(x for x in gates if x['name']=='physical_performance')
    cases=load_json(out/'NUMERIC_MODEL_EVIDENCE.json').get('cases') or {}
    qa={'schema_version':1,'release':'0.36.2','mode':args.mode,
        'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'environment':{'python':sys.version.split()[0],'platform':platform.platform(),
            'duckdb':importlib.util.find_spec('duckdb') is not None,
            'pyarrow':importlib.util.find_spec('pyarrow') is not None,
            'node':bool(shutil.which('node'))},
        'goldens':{'frozen_data':str(BASELINE_PATH.relative_to(ROOT)),
                   'frozen_models':str(BASELINE_PATH.with_name('MODEL_BASELINE.json').relative_to(ROOT))},
        'results':gates,'functional':functional,'physical':physical,
        'performance':performance['status'],
        'synthetic_model_observations':{key:item.get('actual',{}).get('means') for key,item in cases.items()},
        'production_ready':False,
        'limitations':['HTTP import test uses stub NativeBI; it does not certify DuckDB/Parquet',
           'Frozen model metrics are synthetic regression oracles, not production accuracy',
           'Perspective JS/DOM unit tests cannot certify real native browser pixels',
           'No API, installer, new Python environment or browser is downloaded by this runner']}
    (out/'AUDIT.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# TimePilot v0.36.2 long-lived synthetic regression audit','',
        'Functional acceptance: **'+functional['status']+'**',
        'Physical synthetic acceptance: **'+physical['status']+'**',
        'Production: **NOT ASSESSED**','',
        '| Gate | Status | Detail |','|---|---|---|',
        *[f"| {x['name']} | {x['status']} | {str(x.get('reason','')).replace('|','/')} |" for x in gates],
        '', '## Frozen synthetic model metrics (never production KPIs)',
        '| Case | Raw | All | Selected | Objective |','|---|---:|---:|---:|---|',
        *[f"| {k} | {v.get('raw_only','-')} | {v.get('with_features','-')} | {v.get('selected_features','-')} | {'MAE' if k=='static_regression' else 'AP'} |" for k,v in qa['synthetic_model_observations'].items()],
        '','## Audit boundaries',*[f'- {x}' for x in qa['limitations']]]
    (out/'AUDIT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    archive=out.parent/'featurelab362_acceptance.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for file in sorted(out.rglob('*')):
            if file.is_file() and file.suffix.lower() in ('.json','.md','.log'):
                z.write(file,file.relative_to(out))
    print('Functional:',functional['status'],'Physical:',physical['status'])
    print('Report:',out/'AUDIT.md')
    print('ZIP:',archive)
    return 0 if functional['status']=='passed' and (args.mode=='smoke' or physical['status']=='passed') else 2

if __name__=='__main__':raise SystemExit(main())
