#!/usr/bin/env python3
"""TimePilot 0.35.5 offline acceptance, existing environment only.

Execute with: python scripts/featurelab355_acceptance.py
Never installs packages, downloads browsers, or changes the original dataset.
Reports blocked physical/database/browser checks rather than counting skips as passes.
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
OUT=ROOT/'reports/featurelab355_acceptance'


def execute(name,cmd,*,required=(),timeout=240):
    missing=[x for x in required if importlib.util.find_spec(x) is None]
    if missing:return {'name':name,'status':'blocked','reason':'missing dependencies: '+', '.join(missing)}
    if not shutil.which(str(cmd[0])) and not Path(cmd[0]).exists():
        return {'name':name,'status':'blocked','reason':'missing executable: '+str(cmd[0])}
    started=time.perf_counter()
    try:
        run=subprocess.run(cmd,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            timeout=timeout,env={**os.environ,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests')})
        path=OUT/(name+'.log')
        path.write_text(run.stdout,encoding='utf8')
        status='passed' if run.returncode==0 else 'failed'
        if 'skipped' in run.stdout and name=='physical_duckdb':status='blocked'
        return {'name':name,'status':status,'return_code':run.returncode,
                'elapsed_seconds':round(time.perf_counter()-started,2),'log':path.name,
                'tail':'\n'.join(run.stdout.splitlines()[-12:])}
    except subprocess.TimeoutExpired:
        return {'name':name,'status':'failed','reason':f'timeout after {timeout} seconds'}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    node_tests=sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))
    checks=[
        execute('strict_source',[sys.executable,'scripts/verify_installation.py','--source-only','--strict-source']),
        execute('journey_and_regression',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab353_chart_insights.py','tests/test_featurelab355_evidence.py','tests/test_featurelab355_api.py',
            'tests/test_featurelab354_journey.py',
            'tests/test_featurelab340_data_analysis.py','tests/test_featurelab344_351_integration.py',
            'tests/test_featurelab32_auto_workflow.py','tests/test_featurelab333_resilience.py',
            'tests/test_featurelab335_immutable_reports.py'],timeout=240),
        execute('physical_duckdb',[sys.executable,'-m','pytest','-q',
            'tests/test_featurelab335_real_duckdb_charts.py','tests/test_featurelab344_real_duckdb.py',
            'tests/test_featurelab352_real_duckdb.py'],required=('duckdb','pyarrow'),timeout=240),
        execute('node_ui',['node','--test',*[str(x) for x in node_tests]],timeout=240),
    ]
    manual=['在 Mac 本机 Chrome 上从项目上传、发布到分析工作台全流程点击，核对刷新、后退与资产切换',
            '校验不同设备和不同单位字段的真实 DuckDB 聚合值与图表、注释一致',
            '关闭或清空模型 API 后，确认基础画像、16 项受控候选预览及结果恢复仍可运行',
            '核验无标签状态绝不展示准确率、AP 或 MAE 改善；存在标签但尚未通过同源校验时仍不可宣称预测收益',
            '确认后才允许正式物化、模型训练或发布；未确认字段不绕过 PIT/标签权限门禁',
            'Perspective 原生 BI 实际图表、来源哈希与同一版本数值对照',
            '在 Mac Chrome 上验证同一数据的数值解释、三方案模型对照与报告内容一致',
            '超过 200 万行或 256 列时，后台应按默认预算安全跳过自动预览']
    report={'version':'0.35.5','environment':{'python':sys.version.split()[0],
            'os':platform.platform(),'node_available':shutil.which('node') is not None,
            'duckdb':importlib.util.find_spec('duckdb') is not None,
            'pyarrow':importlib.util.find_spec('pyarrow') is not None},
            'checks':checks,'manual_required':manual,
            'all_passed':all(x['status']=='passed' for x in checks)}
    (OUT/'ACCEPTANCE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (OUT/'ACCEPTANCE.md').write_text('\n'.join([
        '# TimePilot 0.35.5 验收结果','',
        '自动脚本只复用现有 Python/Node 环境，不安装或修改依赖。','',
        *[f"- {x['name']}: **{x['status']}** · {x.get('reason','')} · {x.get('elapsed_seconds','—')}s" for x in checks],
        '','## 现场必须补测',*[f'- {x}' for x in manual]])+'\n',encoding='utf8')
    archive=ROOT/'reports/featurelab355_acceptance.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.glob('*')):
            if p.is_file():z.write(p,arcname=p.name)
    print(' '.join(x['name']+'='+x['status'] for x in checks))
    print('Full report:',OUT/'ACCEPTANCE.md')
    return 0 if report['all_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
