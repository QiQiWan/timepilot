#!/usr/bin/env python3
"""Data-driven functional QA of TimePilot using reproducible synthetic corpora.

Functional acceptance and production acceptance are separate. This runner
never asserts production readiness from fixture data. No dependency install,
Chrome download, app DB mutation, or TIMEPILOT_HOME writes.

    python scripts/featurelab361_acceptance.py --mode smoke
    python scripts/featurelab361_acceptance.py --mode full --with-browser
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from featurelab360_acceptance import run_check, load_json, blocked
from timepilot.featurelab361_fixtures import generate_suite,verify_suite,digest_file


def _validate_root_folder(folder: Path, *,kind:str):
    folder=folder.expanduser().resolve()
    if folder==ROOT.resolve() or folder==Path.home().resolve():
        raise ValueError(f'{kind} cannot use application or home root')
    if os.environ.get('TIMEPILOT_HOME') and folder.is_relative_to(Path(os.environ['TIMEPILOT_HOME']).expanduser().resolve()):
        raise ValueError(f'{kind} must not use TIMEPILOT_HOME')
    return folder


def _status(items,required):
    lookup={x['name']:x for x in items}
    passed=[name for name in required if lookup.get(name,{}).get('status')=='passed']
    failed=[name for name in required if lookup.get(name,{}).get('status')=='failed']
    missing=[name for name in required if lookup.get(name,{}).get('status') not in ('passed','failed')]
    return {'status':'failed' if failed else 'blocked' if missing else 'passed',
            'passed':passed,'failed':failed,'blocked':missing,
            'complete':not failed and not missing}


def main(argv=None):
    p=argparse.ArgumentParser(description='TimePilot v0.36.1 audited synthetic functional QA')
    p.add_argument('--mode',choices=('smoke','full'),default='smoke')
    p.add_argument('--out',type=Path,default=Path('reports/featurelab361_acceptance'))
    p.add_argument('--data-dir',type=Path,default=Path('reports/featurelab361_data'))
    p.add_argument('--with-browser',action='store_true',help='Explicitly run real local Chrome + compiled Perspective receipts')
    p.add_argument('--with-performance',action='store_true',help='Run existing physical 10k/100k/1m DuckDB benchmark')
    p.add_argument('--force-generate',action='store_true',help='Explicitly regenerate changed fixture data')
    a=p.parse_args(argv)
    try:
        out=_validate_root_folder(a.out,kind='QA report folder')
        dataset=_validate_root_folder(a.data_dir,kind='QA fixture folder')
    except ValueError as exc:
        p.error(str(exc))
    if out==dataset or out.is_relative_to(dataset) or dataset.is_relative_to(out):
        p.error('data-dir and out must be separate sibling locations')
    out.mkdir(parents=True,exist_ok=True)
    steps=[]
    def append(row):
        steps.append(row)
        print(row['name'],row['status'],row.get('reason',''),flush=True)
    try:
        generate_suite(dataset,force=a.force_generate)
        checked=verify_suite(dataset)
        append({'name':'fixture_manifest','status':checked['status'],
            'reason':'8 deterministic CSV cases; SHA256 and semantic oracles verified',
            'details':checked.get('checked'),'errors':checked.get('errors')})
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        append({'name':'fixture_manifest','status':'failed',
            'reason':type(exc).__name__+': '+str(exc)[:500]})
    if steps[-1]['status']=='passed':
        append(run_check('fixture_contract_and_pit',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab361_fixtures.py',
        'tests/test_featurelab361_public.py'],out,
            missing=('pytest','pandas'),timeout=120,output_format='pytest'))
    else:
        append(blocked('fixture_contract_and_pit','fixture verification did not pass'))
    append(run_check('legacy_fixed_models',[sys.executable,'-m','pytest','-q',
        'tests/test_featurelab332_fixed_benchmarks.py',
        'tests/test_featurelab355_evidence.py'],out,
        missing=('pytest','sklearn'),timeout=180,output_format='pytest'))
    append(run_check('workflow_security_regression',[sys.executable,'-m','pytest','-q',
        'tests/test_featurelab360_gate.py',
        'tests/test_featurelab360_migration.py',
        'tests/test_featurelab360_real_lease.py',
        'tests/test_featurelab335_immutable_reports.py'],out,
        missing=('pytest',),timeout=210,output_format='pytest'))
    append(run_check('strict_source',[sys.executable,'scripts/verify_installation.py',
        '--source-only','--strict-source'],out,timeout=120))
    modules=sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
    append(run_check('frontend_tests',[shutil.which('node') or 'node','--test',
        *[str(x.relative_to(ROOT)) for x in modules]],out,
        timeout=240,output_format='node'))
    backend={};browser={}
    if a.mode=='smoke':
        append(blocked('duckdb_parquet','smoke: physical DuckDB tests not executed'))
        append(blocked('synthetic_pipeline','smoke: full project import/materialization/evaluation not executed'))
        append(blocked('native_browser','smoke: real Chrome/Perspective not executed'))
        append(blocked('physical_performance','smoke: no physical performance benchmark'))
    else:
        physical_deps=('pytest','duckdb','pyarrow')
        append(run_check('duckdb_parquet',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab332_real_duckdb.py',
            'tests/test_featurelab335_real_duckdb_charts.py',
            'tests/test_featurelab344_real_duckdb.py',
            'tests/test_featurelab352_real_duckdb.py',
            'tests/test_featurelab362_duckdb.py'],out,
            missing=physical_deps,timeout=500,output_format='pytest'))
        if next(x for x in steps if x['name']=='fixture_manifest')['status']!='passed':
            append(blocked('synthetic_pipeline','fixture manifest invalid'))
        else:
            field_map=load_json(dataset/'industrial_field_map.json')
            report=out/'synthetic_pipeline.json'
            qa=out/('isolated_app_'+uuid4().hex[:12])
            command=[sys.executable,'scripts/featurelab30_e2e.py',
                '--csv',str((dataset/'industrial_pit.csv').resolve()),
                '--provenance',field_map['provenance'],
                '--models','linear','--backend-only',
                '--audit-home',str(qa),'--report',str(report)]
            for field in ('event_time','available_at','prediction_time','label_available',
                          'target','device','regime','measure_a','measure_b'):
                command.extend(['--'+field.replace('_','-'),field_map[field]])
            result=run_check('synthetic_pipeline',command,out,
                missing=('duckdb','pyarrow','pandas','sklearn','pytest'),timeout=900)
            backend=load_json(report)
            if result['status']=='passed':
                src=backend.get('source') or {}
                if (backend.get('status')!='passed_backend_same_source_browser_pending' or
                    src.get('sha256')!=digest_file(dataset/'industrial_pit.csv')):
                    result.update(status='failed',reason='synthetic pipeline provenance mismatch')
            append(result)
        if not a.with_browser:
            append(blocked('native_browser','--with-browser not requested'))
        elif next(x for x in steps if x['name']=='synthetic_pipeline')['status']!='passed':
            append(blocked('native_browser','synthetic project backend has not succeeded'))
        else:
            browser_path=out/'synthetic_browser.json'
            result=run_check('native_browser',[sys.executable,'scripts/featurelab31_browser_acceptance.py',
                '--backend',str(out/'synthetic_pipeline.json'),
                '--out',str(browser_path),'--timeout','45'],out,
                missing=('playwright','duckdb','pyarrow','perspective'),timeout=300)
            browser=load_json(browser_path)
            if result['status']=='passed':
                from timepilot.featurelab360_gate import same_source_evidence
                errors=same_source_evidence(backend,browser)
                if errors:result.update(status='failed',reason='; '.join(errors))
            append(result)
        if a.with_performance:
            result=run_check('physical_performance',[sys.executable,
                'scripts/featurelab360_performance.py','--out',str(out/'performance.json')],out,
                missing=('duckdb','pyarrow'),timeout=650)
            if result['status']=='passed' and load_json(out/'performance.json').get('status')!='passed':
                result.update(status='failed',reason='performance report did not pass')
            append(result)
        else:
            append(blocked('physical_performance','--with-performance not requested'))
    smoke=('fixture_manifest','fixture_contract_and_pit','legacy_fixed_models',
           'workflow_security_regression','strict_source','frontend_tests')
    full=smoke+('duckdb_parquet','synthetic_pipeline','native_browser')
    summary={'version':'0.36.1','data_provenance':'synthetic',
       'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'mode':a.mode,
       'fixture_dir':str(dataset),'fixture_source_manifest':str(dataset/'MANIFEST.json'),
       'results':steps,'synthetic_smoke':_status(steps,smoke),
       'synthetic_full':_status(steps,full),
       'production_ready':False,'production_gate':'NOT_ASSESSED',
       'notes':['Synthetic or public examples do not certify production forecasting accuracy',
                'Physical DuckDB/Parquet and native browser require the active Mac runtime',
                'Source CSV files are not included in the evidence logs ZIP']}
    (out/'ACCEPTANCE.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# TimePilot v0.36.1 数据驱动功能验收','',
      '验收数据：确定性合成数据；**此报告不代表生产验收**。','',
      '| 模块 | 状态 | 原因/执行情况 |','|---|---|---|',
      *[f"| {x['name']} | {x['status']} | {x.get('reason','').replace('|','/')} |" for x in steps],
      '',f"核心功能测试：**{summary['synthetic_smoke']['status']}**", 
      f"完整合成数据链路：**{summary['synthetic_full']['status']}**", 
      '','真实企业数据泛化和正式生产验收必须独立完成。']
    (out/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    archive=out.parent/'featurelab361_acceptance.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in out.iterdir():
            if f.is_file() and f.suffix in ('.md','.json','.log'):
                z.write(f,f.name)
    print('DATA-QA',summary['synthetic_smoke']['status'],
          'full=',summary['synthetic_full']['status'],
          'production=',summary['production_ready'])
    print('Report:',out/'ACCEPTANCE.md')
    print('Logs:',archive)
    return 0 if _status(steps,smoke if a.mode=='smoke' else full)['complete'] else 2


if __name__=='__main__':raise SystemExit(main())
