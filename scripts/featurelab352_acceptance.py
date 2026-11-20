#!/usr/bin/env python3
"""TimePilot 0.35.2 acceptance. No environment installation, no downloads.

python scripts/featurelab352_acceptance.py
Physical DuckDB/PyArrow absence is BLOCKED, never a successful production gate.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports'/'featurelab352_acceptance'
OUT.mkdir(parents=True,exist_ok=True)


def execute(name, cmd, *, required=(), timeout=180):
    missing=[x for x in required if importlib.util.find_spec(x) is None]
    if missing:
        return {'name':name,'status':'blocked','reason':'missing dependencies: '+', '.join(missing)}
    if not shutil.which(str(cmd[0])) and not Path(cmd[0]).exists():
        return {'name':name,'status':'blocked','reason':'executable missing: '+str(cmd[0])}
    start=time.perf_counter()
    try:
        run=subprocess.run(cmd,cwd=ROOT,text=True,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,timeout=timeout,
            env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
        path=OUT/(name+'.log');path.write_text(run.stdout,encoding='utf-8')
        result={'name':name,'status':'passed' if run.returncode==0 else 'failed',
            'return_code':run.returncode,'elapsed_seconds':round(time.perf_counter()-start,2),
            'log':path.name,'tail':'\n'.join(run.stdout.splitlines()[-12:])}
        if name=='physical_duckdb' and 'skipped' in run.stdout:
            result['status']='blocked'
        return result
    except subprocess.TimeoutExpired as exc:
        return {'name':name,'status':'failed','reason':'timeout','seconds':timeout}


def main():
    tests=sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
    checks=[
        execute('strict_source',[sys.executable,'scripts/verify_installation.py','--source-only','--strict-source']),
        execute('industrial_python',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab352_industrial_mechanisms.py',
            'tests/test_featurelab352_transfer.py',
            'tests/test_featurelab332_fixed_benchmarks.py',
            'tests/test_featurelab344_351_integration.py'],timeout=180),
        execute('physical_duckdb',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab352_real_duckdb.py',
            'tests/test_featurelab344_real_duckdb.py',
            'tests/test_featurelab335_real_duckdb_charts.py'],
            required=('duckdb','pyarrow'),timeout=180),
        execute('node_ui',['node','--test',*[str(p) for p in tests]],timeout=180),
    ]
    unresolved=['目标 Mac 上的真实 DuckDB/Parquet 物化和设备/工况数值逐项比对',
        '现有 Chrome + Perspective 的真实多设备图表、刷新/后退及筛选状态',
        '维护事件和工况字段由工艺人员签字确认；维护事件可能具有记录延迟',
        '真实产线新设备/新工况的前瞻性留出数据及风险指标，不能以合成数据替代',
        '全部设备完整周期的长时间 Worker 中断/恢复与百万行资源压测']
    data={'version':'0.35.2','environment':{'python':sys.version.split()[0],
        'os':platform.platform(),'node':shutil.which('node') or '',
        'duckdb':importlib.util.find_spec('duckdb') is not None,
        'pyarrow':importlib.util.find_spec('pyarrow') is not None},
        'checks':checks,'manual_required':unresolved,
        'all_passed':all(x['status']=='passed' for x in checks)}
    (OUT/'ACCEPTANCE.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=['# TimePilot 0.35.2 可信计算与机理泛化验收','',
        '脚本不安装依赖、不修改原始业务数据。', '',
        *[f"- {x['name']}: **{x['status']}** · {x.get('reason','')} · {x.get('elapsed_seconds','—')} s" for x in checks],
        '', '## 必须在真实环境补测', *[f'- {x}' for x in unresolved]]
    (OUT/'ACCEPTANCE.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    archive=ROOT/'reports/featurelab352_acceptance.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.glob('*')):
            if p.is_file():z.write(p,arcname=p.name)
    print(' '.join(f"{x['name']}={x['status']}" for x in checks))
    print('report:',OUT/'ACCEPTANCE.md','archive:',archive)
    return 0 if data['all_passed'] else 1


if __name__=='__main__':raise SystemExit(main())
