#!/usr/bin/env python3
"""Offline-only v0.34.0 smoke and physical gate, saving a shareable review ZIP.

Run with the active environment's Python. Never installs dependencies, creates
Conda environments or downloads a browser. A skipped DuckDB test is BLOCKED.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
CASES={
 'source': [sys.executable,'scripts/verify_installation.py','--source-only','--strict-source'],
 'worker_and_reports':[sys.executable,'-m','pytest','-q','tests/test_featurelab334_worker_crash.py',
   'tests/test_featurelab335_immutable_reports.py','tests/test_featurelab340_data_analysis.py'],
 'duckdb_physical':[sys.executable,'-m','pytest','-q','tests/test_featurelab332_real_duckdb.py',
   'tests/test_feature_workbench_v033.py','tests/test_featurelab335_real_duckdb_charts.py'],
 'frontend':['node','--test','frontend/native-bi/tests/*.test.mjs'],
}


def run(name,argv,outdir):
    path=outdir/(name+'.log')
    env=dict(os.environ, PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT/'tests'))
    start=time.monotonic()
    try:
        # No shell expansion: glob is expanded using Python before launching Node.
        if name=='frontend':
            argv=['node','--test',*[str(x.relative_to(ROOT)) for x in
                 sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))]]
        proc=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,text=True,
                            errors='replace',timeout=240)
        output=(proc.stdout or '')+'\n'+(proc.stderr or '')
        code=proc.returncode
    except (OSError,subprocess.TimeoutExpired) as exc:
        output=f'Unable to execute: {type(exc).__name__}: {exc}\n'
        code=-1
    path.write_text(output,encoding='utf-8')
    skipped=sum(int(n) for n in re.findall(r'(\d+) skipped',output))
    failures=sum(int(n) for n in re.findall(r'(\d+) failed',output))
    passed=sum(int(n) for n in re.findall(r'(\d+) passed',output))
    if name=='frontend':
        match=re.search(r'(?m)^# pass (\d+)\s*$',output)
        if match:passed=int(match.group(1))
        match=re.search(r'(?m)^# fail (\d+)\s*$',output)
        if match:failures=int(match.group(1))
    status='pass' if code==0 and not skipped and not failures else ('blocked' if code==0 and skipped else 'fail')
    return {'status':status,'exit_code':code,'passed':passed,'failed':failures,
            'skipped':skipped,'seconds':round(time.monotonic()-start,2),
            'log':path.name,'command':' '.join(argv)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default='reports/featurelab340_acceptance',
                        help='Folder under current project for review logs/ZIP')
    args=parser.parse_args()
    output=Path(args.output_dir)
    if not output.is_absolute():output=ROOT/output
    output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    results={key:run(key,cmd,output) for key,cmd in CASES.items()}
    environment={'created_at':datetime.now(timezone.utc).isoformat(),
      'python':sys.version.split()[0], 'python_path':sys.executable,
      'node':shutil.which('node'),
      'duckdb_installed':importlib.util.find_spec('duckdb') is not None,
      'pyarrow_installed':importlib.util.find_spec('pyarrow') is not None,
      'perspective_browser_verified':False}
    report={'environment':environment,'tests':results,
      'manual_gates':{'mac_real_process_kill':'pending',
        'perspective_two_panel_render':'pending',
        'perspective_numeric_independent_reconciliation':'pending',
        'new_import_auto_eda':'pending'}}
    (output/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    markdown=['# TimePilot v0.34.0 本机验收','',
      '- Python：'+environment['python'],
      '- DuckDB：'+('可用' if environment['duckdb_installed'] else '未安装'),
      '- PyArrow：'+('可用' if environment['pyarrow_installed'] else '未安装'),'',
      '| 测试 | 状态 | 通过 | 跳过 | 耗时(s) |','|---|---|---:|---:|---:|']
    for key,r in results.items():
        markdown.append(f"| {key} | {r['status']} | {r['passed']} | {r['skipped']} | {r['seconds']} |")
    markdown+=['','真实 Chrome + Perspective 图表核对及现场进程故障需另行人工签署验收，不能由模拟回执替代。',
      'DuckDB 集成测试若出现 skipped，则整项物理计算验收为 blocked。']
    (output/'ACCEPTANCE.md').write_text('\n'.join(markdown)+'\n',encoding='utf-8')
    zip_path=output.with_suffix('.zip')
    with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for f in sorted(output.glob('*')):
            if f.is_file():archive.write(f,f.name)
    print('Review bundle:',zip_path)
    for name,item in results.items():print(name,item['status'],item['passed'],'passed',item['skipped'],'skipped')
    return 0 if all(x['status']=='pass' for x in results.values()) else 2


if __name__=='__main__':
    raise SystemExit(main())
