#!/usr/bin/env python3
"""v0.36.0 reproducible release gates, grounded in actual executed evidence.

No installs, browser downloads, source-file edits, or writes to TIMEPILOT_HOME.
Full acceptance REQUIRES physical DuckDB/Parquet, a real externally supplied
industrial CSV, installed Chrome + real compiled Perspective, OS crash recovery,
forward migration and 10k/100k/1m physical performance. Missing evidence BLOCKS.

For a quick code-only check:
    python scripts/featurelab360_acceptance.py --mode smoke
For a full Mac check: supply --real-csv, --mapping-json and --with-browser.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.featurelab360_gate import (
    check_pytest_output, digest_file, same_source_evidence, summarize_gates,
)

FIELDS=('event_time','available_at','prediction_time','label_available',
        'target','device','regime','measure_a','measure_b')


def run_check(name, command, folder, *, missing=(), timeout=240,
              output_format='command'):
    missing=[p for p in missing if importlib.util.find_spec(p) is None]
    if missing:
        return {'name':name,'status':'blocked','reason':'missing installed dependencies: '+', '.join(missing)}
    executable=str(command[0])
    if not Path(executable).is_file() and not shutil.which(executable):
        return {'name':name,'status':'blocked','reason':'executable not available: '+executable}
    start=time.perf_counter()
    try:
        result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
             text=True,timeout=timeout,env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests')})
    except subprocess.TimeoutExpired as exc:
        logfile=folder/(name+'.log')
        logfile.write_text((exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout,bytes)
            else (exc.stdout or '')+'\nTIMEOUT: '+str(timeout),encoding='utf-8')
        return {'name':name,'status':'failed','reason':f'timeout after {timeout}s','log':logfile.name}
    except OSError as exc:
        return {'name':name,'status':'failed','reason':type(exc).__name__+': '+str(exc)[:200]}
    logfile=folder/(name+'.log');logfile.write_text(result.stdout,encoding='utf-8')
    status='passed' if result.returncode==0 else 'failed'
    reason='process exit '+str(result.returncode)
    if output_format=='pytest':
        status, reason=check_pytest_output(result.stdout,result.returncode,no_skip=True)
    elif output_format=='node' and result.returncode==0:
        success=re.search(r'^# pass\s+(\d+)\s*$',result.stdout,re.M)
        failures=re.search(r'^# fail\s+(\d+)\s*$',result.stdout,re.M)
        skipped=re.search(r'^# skipped\s+(\d+)\s*$',result.stdout,re.M)
        if not success or int(success.group(1))<1 or (failures and int(failures.group(1))) or (skipped and int(skipped.group(1))):
            status='blocked';reason='node completed without unskipped passing tests'
        else:reason=f'{success.group(1)} frontend tests passed'
    return {'name':name,'status':status,'reason':reason,'return_code':result.returncode,
            'elapsed_seconds':round(time.perf_counter()-start,2),
            'log':logfile.name,'tail':'\n'.join(result.stdout.splitlines()[-6:])}


def blocked(name,reason):
    return {'name':name,'status':'blocked','reason':reason}


def load_json(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (ValueError,OSError):return {}


def main(argv=None):
    p=argparse.ArgumentParser(description='TimePilot v0.36.0 audited physical release acceptance')
    p.add_argument('--mode',choices=('smoke','full'),default='full')
    p.add_argument('--out',type=Path,default=Path('reports/featurelab360_acceptance'))
    p.add_argument('--real-csv',type=Path,help='External production CSV, never a synthetic fixture')
    p.add_argument('--mapping-json',type=Path,help='JSON mapping with 9 columns and declared provenance')
    p.add_argument('--with-browser',action='store_true',help='Require existing local Google Chrome and compiled native Perspective assets')
    p.add_argument('--bench-sizes',default='10000,100000,1000000')
    p.add_argument('--bench-memory-mb',type=int,default=2048)
    p.add_argument('--bench-max-seconds',type=float,default=180)
    p.add_argument('--max-real-rows',type=int,default=200000)
    a=p.parse_args(argv)
    folder=a.out.expanduser().resolve()
    protected=[ROOT.resolve(),Path.home().resolve()]
    if os.environ.get('TIMEPILOT_HOME'):
        protected.append(Path(os.environ['TIMEPILOT_HOME']).expanduser().resolve())
    if any(folder==protected_path for protected_path in protected) or (
        os.environ.get('TIMEPILOT_HOME') and
        folder.is_relative_to(Path(os.environ['TIMEPILOT_HOME']).expanduser().resolve())
    ):
        p.error('验收日志不能写入源码根目录、用户主目录或 TIMEPILOT_HOME 数据目录')
    folder.mkdir(parents=True,exist_ok=True)
    gates=[]
    def add(**kwargs):gates.append(kwargs)
    def append(item):gates.append(item);print(item['name'],item['status'],item.get('reason',''),flush=True)

    append(run_check('strict_source',[sys.executable,'scripts/verify_installation.py','--source-only','--strict-source'],folder,timeout=120))
    append(run_check('python_regression',[sys.executable,'-m','pytest','-q','tests/test_featurelab360_gate.py',
        'tests/test_featurelab360_migration.py','tests/test_featurelab353_chart_insights.py',
        'tests/test_featurelab355_evidence.py'],folder,missing=('pytest',),timeout=210,output_format='pytest'))
    modules=sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
    append(run_check('frontend_node',[shutil.which('node') or 'node','--test',
        *[str(x.relative_to(ROOT)) for x in modules]],folder,timeout=200,output_format='node'))
    append(run_check('migration_compatibility',[sys.executable,'-m','pytest','-q',
        'tests/test_featurelab360_migration.py'],folder,missing=('pytest',),timeout=120,output_format='pytest'))
    append(run_check('worker_crash_recovery',[sys.executable,'-m','pytest','-q',
        'tests/test_featurelab360_real_lease.py','tests/test_featurelab334_worker_crash.py'],folder,
        missing=('pytest',),timeout=180,output_format='pytest'))
    backend_report={};browser_report={}
    if a.mode=='smoke':
        for name in ('physical_duckdb','performance','real_source_pipeline','native_browser'):
            append(blocked(name,'smoke mode: physical/production acceptance not executed'))
    else:
        append(run_check('physical_duckdb',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab332_real_duckdb.py',
            'tests/test_featurelab335_real_duckdb_charts.py',
            'tests/test_featurelab344_real_duckdb.py',
            'tests/test_featurelab352_real_duckdb.py'],folder,
            missing=('pytest','duckdb','pyarrow'),timeout=480,output_format='pytest'))
        sizes=set(s.strip() for s in a.bench_sizes.split(','))
        if not {'10000','100000','1000000'}.issubset(sizes):
            append(blocked('performance','required 10k/100k/1m physical benchmark sizes were omitted'))
        else:
            report_path=folder/'performance.json'
            performance=run_check('performance',[sys.executable,'scripts/featurelab360_performance.py',
                '--sizes',a.bench_sizes,'--max-rss-mb',str(a.bench_memory_mb),
                '--max-seconds-per-size',str(a.bench_max_seconds),'--out',str(report_path)],folder,
                missing=('duckdb','pyarrow'),timeout=max(600,int(a.bench_max_seconds*len(sizes)+60)))
            if performance['status']=='passed':
                perf=load_json(report_path)
                if perf.get('status')!='passed' or len(perf.get('cases') or [])<3:
                    performance.update(status='blocked',reason='incomplete physical benchmark report')
            append(performance)
        if not (a.real_csv and a.mapping_json):
            append(blocked('real_source_pipeline','provide both --real-csv and --mapping-json; synthetic fixtures cannot satisfy the real-data gate'))
        elif not a.real_csv.is_file() or not a.mapping_json.is_file():
            append(blocked('real_source_pipeline','real data or field mapping file does not exist'))
        elif a.real_csv.resolve().is_relative_to(ROOT.resolve()):
            append(blocked('real_source_pipeline','source file is inside source checkout; demo and fixture files cannot certify a real external dataset'))
        elif any(importlib.util.find_spec(x) is None for x in ('duckdb','pyarrow')):
            append(blocked('real_source_pipeline','DuckDB/PyArrow are required for a real source pipeline'))
        else:
            mapping=load_json(a.mapping_json)
            missing=[x for x in FIELDS if not isinstance(mapping.get(x),str) or not mapping[x].strip()]
            provenance=mapping.get('provenance')
            if missing or not isinstance(provenance,str) or not provenance.strip():
                append(blocked('real_source_pipeline','invalid field mapping or missing operator-declared provenance: '+', '.join(missing)))
            else:
                qa=folder/('isolated_qa_'+uuid4().hex[:12])
                report=folder/'real_backend.json'
                command=[sys.executable,'scripts/featurelab30_e2e.py','--csv',str(a.real_csv.resolve()),
                    '--provenance',provenance,'--task',mapping.get('task','binary_classification'),
                    '--timezone',mapping.get('timezone','Asia/Shanghai'),
                    '--models',mapping.get('models','linear'),'--rows-limit',str(a.max_real_rows),
                    '--backend-only','--audit-home',str(qa),'--report',str(report)]
                for field in FIELDS:
                    command.extend(['--'+field.replace('_','-'),mapping[field]])
                record=run_check('real_source_pipeline',command,folder,
                    missing=('duckdb','pyarrow','pandas','sklearn','pytest'),timeout=900)
                backend_report=load_json(report)
                if record['status']=='passed':
                    origin=backend_report.get('source') or {}
                    actual=digest_file(a.real_csv)
                    if backend_report.get('status')!='passed_backend_same_source_browser_pending' or origin.get('sha256')!=actual:
                        record.update(status='failed',reason='external CSV SHA256 or backend status mismatch')
                    else:record['declared_source_sha256']=actual
                append(record)
        if not a.with_browser:
            append(blocked('native_browser','--with-browser not supplied: no native Perspective receipt'))
        elif not backend_report or next((g for g in gates if g['name']=='real_source_pipeline'),{}).get('status')!='passed':
            append(blocked('native_browser','real data backend gate not complete'))
        else:
            path=folder/'real_backend.json'
            browser_path=folder/'native_browser.json'
            record=run_check('native_browser',[sys.executable,'scripts/featurelab31_browser_acceptance.py',
                '--backend',str(path),'--out',str(browser_path),'--timeout','45'],folder,
                missing=('playwright','duckdb','pyarrow','perspective'),timeout=300)
            browser_report=load_json(browser_path)
            if record['status']=='passed':
                errors=same_source_evidence(backend_report,browser_report)
                if errors:record.update(status='failed',reason='; '.join(errors))
            append(record)

    decision=summarize_gates(gates,backend_evidence=backend_report if backend_report else None,
                             browser_evidence=browser_report if browser_report else None)
    summary={'release':'0.36.0','timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'mode':a.mode,'environment':{'python':sys.version.split()[0],
         'platform':platform.platform(),'node':bool(shutil.which('node')),
         'duckdb':importlib.util.find_spec('duckdb') is not None,
         'pyarrow':importlib.util.find_spec('pyarrow') is not None,
         'perspective':importlib.util.find_spec('perspective') is not None},
        'gates':gates,'decision':decision,'production_ready':decision['fully_accepted'],
        'limits':{'synthetic_benchmark_is_not_production_accuracy':True,
                  'source_provenance_operator_declaration_only':True,
                  'reports_retain_no_raw_business_data':True}}
    (folder/'ACCEPTANCE.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# TimePilot v0.36.0 真实验收与发布门禁','',
        '**结论：'+('可进入下一步正式审查' if decision['fully_accepted'] else '未达到完整生产验收')+'**',
        '',f'运行模式：{a.mode}；数据来源声明和真实数据证明应由业务负责人复核。','',
        '| 验收门禁 | 状态 | 依据 / 阻断原因 |','|---|---|---|',
        *[f"| {g['name']} | {g['status']} | {str(g.get('reason','')).replace('|','/')} |" for g in gates],
        '','## 尚待闭合的事项',*[f'- {s}' for s in decision['blocking_findings']],
        '','## 边界','- 性能数据仅为确定性合成压力测试，不作为真实业务算法准确率。',
        '- 外部 CSV 不复制进验收 ZIP；完整验收要求由业务负责人确认字段含义与来源。',
        '- 缺少 DuckDB/Parquet/已编译 Perspective 或已安装 Chrome 时，不能把测试标记为通过。',
        '- 所有检查只调用现有环境，不下载浏览器或安装新环境。']
    (folder/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    archived=folder.parent/'featurelab360_acceptance.zip'
    with zipfile.ZipFile(archived,'w',zipfile.ZIP_DEFLATED) as zipout:
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix in ('.log','.json','.md'):
                zipout.write(path,path.name)
    print('v0.36.0',decision['status'],'fully_accepted=',decision['fully_accepted'])
    print('Report:',folder/'ACCEPTANCE.md')
    print('Log ZIP:',archived)
    if a.mode=='smoke':
        return 0 if all(g['status']=='passed' for g in gates if g['name'] in
            ('strict_source','python_regression','frontend_node','migration_compatibility','worker_crash_recovery')) else 1
    return 0 if decision['fully_accepted'] else 2


if __name__=='__main__':raise SystemExit(main())
