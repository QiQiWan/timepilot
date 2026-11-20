#!/usr/bin/env python3
"""FeatureLab 0.35.1 local acceptance, no installation or browser download.

Run from the existing checkout and Python environment:
    python scripts/featurelab351_acceptance.py
Results and a ZIP are created beneath reports/featurelab351_acceptance.
A skipped/absent physical dependency counts as BLOCKED, never as PASS.
"""
from __future__ import annotations
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
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports'/'featurelab351_acceptance'
OUT.mkdir(parents=True,exist_ok=True)


def _run(name,args,*,needed=(),timeout=200):
    absent=[name for name in needed if importlib.util.find_spec(name) is None]
    if absent:
        return {'name':name,'status':'blocked','message':'Required installed dependencies missing: '+', '.join(absent),
                'command':' '.join(map(str,args))}
    t=time.perf_counter()
    try:
        p=subprocess.run(args,cwd=ROOT,text=True,stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,timeout=timeout,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
        output=p.stdout
        (OUT/(name+'.log')).write_text(output,encoding='utf-8')
        status='passed' if p.returncode==0 else 'failed'
        if re.search(r'\b\d+\s+skipped\b',output) and name.startswith('physical'):
            status='blocked'
        return {'name':name,'status':status,'exit_code':p.returncode,
                'duration_seconds':round(time.perf_counter()-t,3),'log':name+'.log',
                'tail':'\n'.join(output.strip().splitlines()[-8:])}
    except subprocess.TimeoutExpired as ex:
        (OUT/(name+'.log')).write_text(str(ex),encoding='utf-8')
        return {'name':name,'status':'failed','error':'timeout','timeout_seconds':timeout}
    except FileNotFoundError:
        return {'name':name,'status':'blocked','message':'Executable not present: '+str(args[0])}


def main():
    checks=[
        _run('strict_source',[sys.executable,'scripts/verify_installation.py','--source-only','--strict-source']),
        _run('core_python',[sys.executable,'-m','pytest','-q','tests/test_featurelab344_351_integration.py',
                            'tests/test_featurelab341_343_integration.py','tests/test_featurelab332_fixed_benchmarks.py',
                            'tests/test_featurelab333_resilience.py','tests/test_featurelab340_data_analysis.py'],timeout=180),
        _run('physical_duckdb',[sys.executable,'-m','pytest','-q','tests/test_featurelab344_real_duckdb.py',
                                'tests/test_featurelab335_real_duckdb_charts.py',
                                'tests/test_featurelab332_real_duckdb.py'],needed=['duckdb','pyarrow'],timeout=200),
        _run('frontend_node',['node','--test',*map(str,sorted((ROOT/'frontend'/'native-bi'/'tests').glob('*.test.mjs')))],timeout=180),
    ]
    payload={'version':'0.35.1','environment':{'python':sys.version.split()[0],
             'system':platform.platform(),'node_present':bool(shutil.which('node')),
             'duckdb_installed':importlib.util.find_spec('duckdb') is not None,
             'pyarrow_installed':importlib.util.find_spec('pyarrow') is not None},
             'checks':checks,
             'manual_required':['Mac existing Chrome: real project navigation, browser back/refresh, Perspective numerical verification',
                                'Mac Worker kill/restart: lease fencing and one-artifact audit',
                                'Business dataset: 10k/100k/1m rows latency and RSS benchmark',
                                'Independent final holdout not yet operationally integrated with FeatureLab evaluator']}
    (OUT/'ACCEPTANCE.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# TimePilot FeatureLab 0.35.1 本机验收','',
           '本脚本不安装依赖，不下载浏览器，不修改业务源数据。','']
    for item in checks:
        lines.append(f"- {item['name']}: **{item['status']}** · {item.get('duration_seconds','—')}s · {item.get('message','')}")
    lines.extend(['','## 未闭合验收','',*(f'- {x}' for x in payload['manual_required'])])
    (OUT/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    outzip=ROOT/'reports'/'featurelab351_acceptance.zip'
    with zipfile.ZipFile(outzip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(OUT.glob('*')):
            if p.is_file():archive.write(p,arcname=p.name)
    print('Acceptance report:',OUT/'ACCEPTANCE.md')
    print('Log ZIP:',outzip)
    print(' '.join(f"{x['name']}={x['status']}" for x in checks))
    # Source and core contract failures reject release. A physical dependency
    # blocked gate also rejects FULL production acceptance, even if safe to test.
    return 0 if all(x['status']=='passed' for x in checks) else 1


if __name__=='__main__':raise SystemExit(main())
