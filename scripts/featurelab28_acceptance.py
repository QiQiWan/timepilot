#!/usr/bin/env python3
"""Source-pinned FeatureLab v0.28 data-to-Parquet acceptance.

Run inside the application's EXISTING Python environment. A fresh temporary
TimePilot home is used by default; no user DB is modified. Without --csv, a
DETERMINISTIC GENERATED fixture is used and must never be described as actual
production measurements. No screenshot or real plant data is copied into the
source package. Interactive Perspective browser acceptance is a separate gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def fixture(path, n=216):
    path=Path(path)
    with path.open('w',encoding='utf-8',newline='') as out:
        writer=csv.writer(out)
        writer.writerow(['sample_id','device_id','event_time','available_at','batch_id',
                         'negative_pressure_kpa','air_pressure_kpa','run_state'])
        for i in range(n):
            device=i%3;sequence=i//3
            stamp=datetime(2026,11,9,8,0,0)+timedelta(minutes=sequence)
            writer.writerow([f'S{i+1:06d}',f'EQ-{device+1}',stamp.isoformat(),
                             (stamp+timedelta(seconds=3)).isoformat(),f'B{sequence//12:04d}',
                             round(-15.0+0.017*sequence+0.13*device,3),
                             round(420+0.11*sequence-0.3*device,3),
                             'MAINT' if sequence%20==0 else 'RUN'])
    return path


def main():
    parser=argparse.ArgumentParser(description='TimePilot v0.28 candidate -> preview -> materialize -> BI SQL acceptance')
    parser.add_argument('--csv',type=Path,help='External CSV containing actual measurements; never modified')
    parser.add_argument('--report',type=Path,default=Path('featurelab28_acceptance.json'))
    parser.add_argument('--qa-home',type=Path,help='Explicit scratch home; omit for temporary isolated home')
    args=parser.parse_args()
    report={'version':'0.28.0','source_kind':'external_user_supplied_provenance_unverified' if args.csv else 'generated_test_fixture',
            'overall':'blocked','steps':[],'perspective_browser_render':'not_executed'}
    def log(name,status,**values):
        row={'step':name,'status':status};row.update(values);report['steps'].append(row)
        print(f'[{status.upper()}] {name}',flush=True)
    def request(response,stage,allowed=(200,201,202)):
        if response.status_code not in allowed:
            raise RuntimeError(f'{stage}: HTTP {response.status_code}: {response.text[:1000]}')
        return response.json()
    def write():
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Report:',args.report.resolve())
    try:
        deps={key:bool(importlib.util.find_spec(key)) for key in ('duckdb','pyarrow','perspective')}
        report['dependencies']=deps
        if not deps['duckdb'] or not deps['pyarrow']:
            log('runtime_dependencies','blocked',missing=[k for k in ('duckdb','pyarrow') if not deps[k]])
            report['overall']='blocked';report['message']='请在现有 TimePilot Python 环境安装 requirements.txt 后重新验收'
            return 2
        from fastapi.testclient import TestClient
        from timepilot.api import create_app
        from timepilot.storage import sha256_file
        from timepilot.native_bi_api import prepare_perspective_source
        from timepilot.native_bi_bundle import bundle_status
        with tempfile.TemporaryDirectory(prefix='timepilot_028_acceptance_') as root:
            base=Path(args.qa_home).resolve() if args.qa_home else Path(root)
            base.mkdir(parents=True,exist_ok=True)
            source=Path(args.csv).expanduser().resolve() if args.csv else fixture(base/'industrial_fixture.csv')
            if source.suffix.lower()!='.csv' or not source.is_file():
                raise ValueError('验收数据必须是存在的 CSV 文件（不会改写原件）')
            report['input']={'file':source.name,'sha256':file_hash(source),
                             'bytes':source.stat().st_size,'origin_verified':False}
            log('input_sha256','passed',sha256=report['input']['sha256'])
            app=create_app(home=base/'isolated_home',embedded_worker=False)
            with TestClient(app) as client:
                pid=request(client.post('/api/v1/projects',json={'name':'FeatureLab 0.28 QA '+uuid4().hex[:8]}),'project')['id']
                with source.open('rb') as opened:
                    uploaded=request(client.post(f'/api/v1/projects/{pid}/imports',
                                  files=[('files',(source.name,opened,'text/csv'))]),'import')
                iid=uploaded['import_id']
                catalog=request(client.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog'),'catalog')
                regions=[region for f in catalog['files'] for sheet in f['worksheets']
                         for region in sheet['regions']]
                if len(regions)!=1:
                    raise RuntimeError('自动验收要求 CSV 恰好一张表，检测到 '+str(len(regions))+' 个区域；请人工确认表头后验收')
                rid=regions[0]['region_id']
                queued=request(client.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                             json={'region_ids':[rid],'confirm':True}),'publish')
                task=app.state.project_publication_jobs.work_once()
                if not task or task.get('status')!='succeeded':
                    raise RuntimeError('真实 CSV 发布任务未成功：'+repr(task))
                published=request(client.get(f'/api/v1/projects/{pid}/publish-batches/{queued["batch_id"]}'),'published')
                aid=next(x['native_asset_id'] for x in published['items'] if x['status']=='succeeded')
                source_meta=app.state.feature_semantic.source(pid,aid,'base')
                row_count=int(source_meta.get('row_count') or 0)
                if row_count<12:raise RuntimeError('须提供至少12行数据以测试滚动高级特征')
                if not app.state.native_bi.verify(aid).get('valid'):
                    raise RuntimeError('已导入的源 Parquet SHA256 校验失败')
                log('csv_import_published','passed',asset_id=aid,rows=row_count)

                api=f'/api/v1/projects/{pid}/feature-lab'
                contract=request(client.get(api+'/contracts',params={'asset_id':aid}),'contract')
                fields={f['field_id']:f for f in contract['fields']}
                suggestions=request(client.get(api+'/field-suggestions',params={'asset_id':aid}),'fields')
                allowed=[f['field_id'] for f in suggestions['fields'] if f['eligible_numeric']]
                if len(allowed)<2:
                    raise RuntimeError('必须识别到至少两列数值测量字段；先在系统中人工确认字段契约')
                log('semantic_fields','passed',numeric_fields=[fields[f]['source_name'] for f in allowed],
                    contract_revision=contract['revision'])
                sess=request(client.post(api+'/candidate-sessions',json={
                    'asset_id':aid,'request_id':uuid4().hex,'template':'advanced',
                    'budget':18,'selected_field_ids':allowed[:3]}),'candidates')
                candidates=sess['candidates']
                picked=[]
                for name in ('rolling.median','relative.change'):
                    entry=next((c for c in candidates if c['operator_id']==name),None)
                    if entry:picked.append(entry)
                if len(picked)!=2:
                    raise RuntimeError('自动候选缺少滚动中位数及历史相对变化率')
                log('advanced_candidates','passed',candidate_count=len(candidates),selected=[x['operator_id'] for x in picked])
                preview=request(client.post(api+'/candidate-sessions/'+sess['session_id']+'/preview-jobs',json={
                    'request_id':uuid4().hex,'candidate_ids':[x['candidate_id'] for x in picked],
                    'max_rows':30,'scan_budget':max(100,row_count)}),'preview_queue')
                work=app.state.feature_jobs.work_once()
                result=request(client.get(api+'/runs/'+preview['job_id']),'preview_status')
                if result['status']!='succeeded':
                    raise RuntimeError('真实 DuckDB 候选计算失败：'+str(result.get('error_text') or result.get('error_code')))
                data=result['result']
                if len(data['quality'])!=2 or any(q['rows_scanned']!=row_count for q in data['quality']):
                    raise RuntimeError('候选全数据质量统计的样本范围错误')
                if data['scope']!='sample' or data['statistics_scope']!='full_source_up_to_preview_scan_budget':
                    raise RuntimeError('候选样本与全量统计范围标记不正确')
                log('candidate_duckdb_preview','passed',sample_rows=data['rows_returned'],
                    statistics_rows=[q['rows_scanned'] for q in data['quality']])
                saved=request(client.post(api+'/candidate-sessions/'+sess['session_id']+'/save-set',json={
                    'request_id':uuid4().hex,'name':'0.28 E2E advanced','candidate_ids':[x['candidate_id'] for x in picked]}),'save_set')
                sid=saved['set_id']
                first,second=allowed[:2]
                cformula=request(client.post(api+'/expressions/validate',json={
                    'asset_id':aid,'source_sha256':source_meta['sha256'],
                    'contract_revision':contract['revision'],'input_field_ids':[first,second],
                    'expression':'safe_div(x0-x1,x1)','display_name':'受控公式：相对差值'}),'formula')
                spec=dict(saved['spec']);spec['features']=[*spec['features'],cformula['definition']]
                updated=request(client.patch(api+'/sets/'+sid,json={'expected_revision':saved['revision'],
                                         'spec':spec}),'set_patch')
                log('custom_formula_saved','passed',set_id=sid,revision=updated['revision'])
                queued=request(client.post(api+'/sets/'+sid+'/runs',json={'request_id':uuid4().hex}),'run_queue')
                app.state.feature_jobs.work_once()
                job=request(client.get(api+'/runs/'+queued['job_id']),'run_result')
                if job['status']!='succeeded':
                    raise RuntimeError('实际物化失败：'+str(job.get('error_text') or job.get('error_code')))
                verified=request(client.get(api+'/sets/'+sid+'/latest-artifact'),'latest_artifact')
                if not verified['ready']:
                    raise RuntimeError('物化结果未通过项目/修订/源文件/字节级验收')
                artifact=verified['artifact']; path=app.state.feature_jobs.artifact_path(pid,artifact['artifact_id'])
                if artifact['row_count']!=row_count or sha256_file(path)!=artifact['sha256']:
                    raise RuntimeError('物化 Parquet 行数或 SHA256 不一致')
                run_result=request(client.get(api+'/runs/'+queued['job_id']+'/result',
                    params={'preview_rows':5}),'materialized_api')
                if run_result['scope']!='full' or run_result['preview_count']!=5:
                    raise RuntimeError('全量物化的结果 API 样本读取失败')
                log('full_materialize_verified','passed',artifact_id=artifact['artifact_id'],
                    sha256=artifact['sha256'],rows=artifact['row_count'],features=[x['output_name'] for x in spec['features']])
                with app.state.native_bi._connection() as con:
                    source_sql=app.state.native_bi._effective_sql(aid,'base',source_meta['columns'])
                    source_table=prepare_perspective_source(con,source_sql)
                    location=str(path).replace("'","''")
                    con.execute('''CREATE VIEW timepilot_feature_artifact AS
                        SELECT ROW_NUMBER() OVER ()-1 AS "__tp_rowid", * FROM read_parquet('''+"'"+location+"'"+''')''')
                    status=con.execute('''SELECT COUNT(*) AS n FROM timepilot_feature_artifact''').fetchone()[0]
                    panel_cols={x[0] for x in con.execute('DESCRIBE SELECT * FROM timepilot_feature_artifact').fetchall()}
                    if status!=row_count or not {x['output_name'] for x in spec['features']}<=panel_cols:
                        raise RuntimeError('Perspective 原始表/特征表 SQL 视图校验失败')
                log('bi_duckdb_hosted_views','passed',source_table=source_table,
                    feature_view='timepilot_feature_artifact',row_count=status)
                bundle=bundle_status(REPO/'src'/'timepilot'/'static'/'native-bi')
                caps=app.state.native_bi.capabilities()
                if not caps.get('perspective_virtual_server_ready') or not bundle.get('ready'):
                    log('perspective_interactive_browser','blocked',
                        reason='Perspective Python VirtualServer 或打包的 JS/WASM 未就绪；需要浏览器实机验收')
                else:
                    log('perspective_interactive_browser','not_executed',
                        reason='SQL 视图可加载；仍需打开页面、验证两个真实面板并回收 WS 回执')
            report['overall']='passed_except_interactive_perspective'
            report['input']['source_rows']=row_count
    except Exception as exc:
        report['overall']='failed';report['error_type']=type(exc).__name__
        report['error']=str(exc)[:1500]
        log('acceptance_interrupted','failed',reason=str(exc)[:600])
        return 1
    finally:write()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
