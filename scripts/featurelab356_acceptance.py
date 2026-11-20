#!/usr/bin/env python3
"""v0.35.6 offline checks: integrity, guarded report APIs, workspace JS and physical data.

Uses the active interpreter and existing Node only. No environment installation,
network downloads or browser downloads. A missing DuckDB/PyArrow gate is BLOCKED.
"""
from __future__ import annotations
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports'/'featurelab356_acceptance'


def run(name, command, *, required=(), timeout=240):
    missing = [module for module in required if importlib.util.find_spec(module) is None]
    if missing:
        return {'name': name, 'status': 'blocked', 'reason': 'missing modules: '+', '.join(missing)}
    if not shutil.which(str(command[0])) and not Path(command[0]).exists():
        return {'name': name, 'status': 'blocked', 'reason': 'missing executable: '+str(command[0])}
    started = time.perf_counter()
    try:
        process = subprocess.run(command, cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
            env={**os.environ, 'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(ROOT/'tests')})
        logfile = OUT / (name+'.log')
        logfile.write_text(process.stdout, encoding='utf-8')
        status = 'passed' if process.returncode == 0 else 'failed'
        if name == 'physical_duckdb' and 'skipped' in process.stdout:
            status = 'blocked'
        return {'name': name, 'status': status, 'return_code': process.returncode,
            'elapsed_seconds': round(time.perf_counter()-started, 2),
            'log': logfile.name, 'tail': '\n'.join(process.stdout.splitlines()[-14:])}
    except subprocess.TimeoutExpired as exc:
        return {'name': name, 'status': 'failed', 'reason': f'timeout {timeout}s: {exc}'}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks = [
        run('strict_source',[sys.executable, 'scripts/verify_installation.py', '--source-only','--strict-source']),
        run('python_workspace_and_reports',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab356_delivery_contract.py',
            'tests/test_featurelab335_immutable_reports.py',
            'tests/test_featurelab355_evidence.py',
            'tests/test_featurelab355_api.py',
            'tests/test_featurelab354_journey.py',
            'tests/test_featurelab333_resilience.py'],required=('pytest',),timeout=360),
        run('physical_duckdb',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab332_real_duckdb.py',
            'tests/test_featurelab335_real_duckdb_charts.py',
            'tests/test_featurelab344_real_duckdb.py',
            'tests/test_featurelab352_real_duckdb.py'],required=('pytest','duckdb','pyarrow'),timeout=420),
        run('frontend_all',[shutil.which('node') or 'node','--test',
            *[str(x.relative_to(ROOT)) for x in sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))]],timeout=360),
    ]
    manual_checks = [
        'Mac Chrome: 从 /projects/{id}/analysis 进入 手工构造，新增受控公式/算子并预览、保存；返回仪表盘后草稿仍保留。',
        'Mac Chrome: 选择已有特征集，点击报告页继续管理，确认加载的是同一个 set_id、asset_id、version_id。',
        'Mac Chrome: 高级算法页面开启 tsfresh、Featuretools 受控任务，在依赖缺失时显示可解释失败，PIT 回接须人工确认。',
        'Mac Chrome: 在同一工作台的 BI 数据加载源表及经过 SHA256 校验的正式特征集，确认真实 Perspective 原生双面板及数值。',
        'Mac Chrome: 保存 Perspective 布局，刷新、浏览器后退、切换数据版本后确认正确恢复或阻止跨版本加载。',
        'Mac Chrome: 预览与导出报告，归档两版，重新读取并复核 SHA256、查看差异；模拟报告文件篡改时阻断。',
        'Mac Chrome: 关闭大模型 API 后重复上述非模型任务，基础分析、手动算子及报告仍可用。',
        '生产数据: 用独立 DuckDB SQL 核对物化特征值、图表聚合与报告值；使用真实计算成本、内存与任务异常记录。',
    ]
    result = {'version':'0.35.6','environment':{'python':sys.version.split()[0],
        'os':platform.platform(),'node':shutil.which('node'),
        'duckdb':importlib.util.find_spec('duckdb') is not None,
        'pyarrow':importlib.util.find_spec('pyarrow') is not None},
        'checks':checks,'manual_required':manual_checks,
        'all_passed':all(item['status']=='passed' for item in checks)}
    (OUT/'ACCEPTANCE.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    md=['# TimePilot v0.35.6 验收日志','',
        '仅使用现有环境执行自动化测试；缺少物理计算依赖时 BLOCKED，不计通过。','',
        *[f"- {item['name']}: **{item['status']}** · {item.get('reason', '')} · {item.get('elapsed_seconds','—')}s" for item in checks],
        '','## Mac 本地与真实数据验收清单',*['- '+x for x in manual_checks]]
    (OUT/'ACCEPTANCE.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    archive=ROOT/'reports/featurelab356_acceptance.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as fh:
        for path in sorted(OUT.glob('*')):
            if path.is_file(): fh.write(path,arcname=path.name)
    print(' '.join(f"{item['name']}={item['status']}" for item in checks))
    print('Report:',OUT/'ACCEPTANCE.md')
    return 0 if result['all_passed'] else 1

if __name__=='__main__': raise SystemExit(main())
