#!/usr/bin/env python3
"""TimePilot v0.30 single-source supervised PIT -> Parquet -> evaluator -> BI acceptance.

Requires a user supplied external CSV with verified field mapping. Does not infer
plant provenance, create fake measurements, mutate the user's application home,
or substitute offline numeric replay for project FeatureEvaluator execution.
Perspective HTML/WS render remains an independent browser acceptance gate.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def digest_file(path):
    h=hashlib.sha256()
    with path.open('rb') as inp:
        for part in iter(lambda:inp.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def parse_args(argv=None):
    p=argparse.ArgumentParser(description='Real-source PIT, supervised folds and BI hosted data validation')
    p.add_argument('--csv',type=Path,required=True,help='Existing external industrial CSV; no generated fixture allowed')
    p.add_argument('--provenance',required=True,help='Operator declaration of dataset provenance (not independently certified)')
    for name in ('event-time','available-at','prediction-time','label-available','target','device','regime','measure-a','measure-b'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--timezone',default='Asia/Shanghai',help='IANA timezone used for naive timestamps')
    p.add_argument('--task',choices=('binary_classification','regression'),default='binary_classification')
    p.add_argument('--report',type=Path,default=Path('reports/v030/same_source_e2e.json'))
    p.add_argument('--models',choices=('linear','both'),default='both')
    p.add_argument('--backend-only',action='store_true',help='Explicitly accept backend-only QA; does not count as full release')
    p.add_argument('--audit-home',type=Path,default=None,help='Dedicated, initially empty QA directory kept for subsequent real browser acceptance')
    p.add_argument('--rows-limit',type=int,default=200000)
    return p.parse_args(argv)


def verify_input(frame,args):
    from zoneinfo import ZoneInfo
    import pandas as pd
    names=[args.event_time,args.available_at,args.prediction_time,args.label_available,
           args.target,args.device,args.regime,args.measure_a,args.measure_b]
    if len(set(names))!=len(names) or set(names)-set(frame.columns):
        raise ValueError('输入字段重复或缺失：'+str(sorted(set(names)-set(frame.columns))))
    if frame.columns.duplicated().any():raise ValueError('原始 CSV 存在重复字段名')
    if len(frame)<150 or len(frame)>args.rows_limit:
        raise ValueError('监督时间验证至少 150 行，且不得超过配置行数预算')
    if frame[args.device].nunique(dropna=True)<2 or frame[args.regime].nunique(dropna=True)<2:
        raise ValueError('设备和工况需要至少两个非空组，以完成切片验收')
    if frame[[args.device,args.regime]].isna().any().any():
        raise ValueError('设备或工况有缺失，请业务部门先核实')
    timezone=ZoneInfo(args.timezone)
    for col in (args.event_time,args.available_at,args.prediction_time,args.label_available):
        parsed=pd.to_datetime(frame[col],errors='raise',utc=False)
        if not hasattr(parsed,'dt'):
            raise ValueError('时间字段必须是可解析的逐行时间戳：'+col)
        if parsed.dt.tz is None:
            parsed=parsed.dt.tz_localize(timezone,ambiguous='raise',nonexistent='raise')
        frame[col]=parsed.dt.tz_convert('UTC')
        if frame[col].isna().any():raise ValueError('时间缺失：'+col)
    invalid=(frame[args.event_time]>frame[args.prediction_time]) | (frame[args.available_at]>frame[args.prediction_time])
    if invalid.any():raise ValueError('PIT 阻断：源记录中有 '+str(int(invalid.sum()))+' 条预测时点之后的观测或晚到数据')
    # Label should not be available before the decision. A label is excluded
    # from every feature input; this guard catches misinterpreted label timing.
    if (frame[args.label_available]<frame[args.prediction_time]).any():
        raise ValueError('部分标签在决策时点前已经可用，需业务确认预测任务定义')
    for col in (args.measure_a,args.measure_b):
        frame[col]=pd.to_numeric(frame[col],errors='raise').astype(float)
        if frame[col].notna().sum()<120:raise ValueError('测量有效行数不足：'+col)
    frame[args.target]=pd.to_numeric(frame[args.target],errors='raise')
    if frame[args.target].isna().any():raise ValueError('标签存在空值')
    if args.task=='binary_classification' and not set(frame[args.target].unique())<={0,1}:
        raise ValueError('二分类标签必须为 0 和 1')
    if frame[args.target].nunique()<2:raise ValueError('标签没有有效变化')
    if frame[[args.event_time,args.available_at,args.prediction_time]].isna().any().any():
        raise ValueError('时间不能包含 NaT')
    return frame


def post(client,url,payload,stage):
    response=client.post(url,json=payload)
    if response.status_code not in (200,201,202):
        raise RuntimeError(stage+': HTTP '+str(response.status_code)+' '+response.text[:1200])
    return response.json()


def assert_same_artifact_evidence(evaluated,*,source_sha,spec_sha,artifact_id,artifact_sha,revision,folds=3):
    """Fail closed when an evaluation cannot prove exactly which Parquet it read."""
    expected={'source_sha256':source_sha,'spec_sha256':spec_sha,
        'evaluated_artifact_id':artifact_id,'evaluated_artifact_sha256':artifact_sha,
        'evaluated_contract_revision':revision}
    wrong=[key for key,value in expected.items() if evaluated.get(key)!=value]
    if wrong or len(evaluated.get('folds',[]))!=folds:
        raise ValueError('同源评价证据不匹配：'+', '.join(wrong or ['folds']))
    return True


def main(argv=None):
    args=parse_args(argv)
    report={'version':'0.31.0','mode':'external_csv_requires_operator_provenance','status':'blocked',
      'provenance_status':'declared_by_operator_not_independently_verified',
      'perspective_browser_receipt':'not_executed','steps':[]}
    def log(stage,status,**details):
        report['steps'].append({'stage':stage,'status':status,**details})
        print('['+status.upper()+'] '+stage,flush=True)
    try:
        source=args.csv.expanduser().resolve(strict=True)
        if source.suffix.lower()!='.csv':raise ValueError('只接收明确声明来源的 CSV')
        if len(args.provenance.strip())<12:raise ValueError('必须填写至少12字符的来源说明')
        report['source']={'filename':source.name,'sha256':digest_file(source),
          'bytes':source.stat().st_size,'declared_provenance':args.provenance}
        dependencies={n:bool(importlib.util.find_spec(n)) for n in ('duckdb','pyarrow','sklearn','pandas','perspective')}
        report['dependencies']=dependencies
        absent=[n for n in ('duckdb','pyarrow','sklearn','pandas') if not dependencies[n]]
        if absent:
            log('dependencies','blocked',missing=absent)
            return 2
        import pandas as pd
        from fastapi.testclient import TestClient
        from timepilot.api import create_app
        from timepilot.feature_contracts import (TaskSpecV2,FeatureDefinition,FeatureSpecV2,
          SourceRef,EvaluationProtocol,QualitySelectionPolicy,SlicePolicy,AutoSearchPolicy)
        from timepilot.native_bi_api import prepare_perspective_source
        from timepilot.storage import sha256_file
        frame=pd.read_csv(source,low_memory=False)
        frame=verify_input(frame,args)
        log('external_source_pit_precheck','passed',rows=len(frame),
           devices=int(frame[args.device].nunique()),regimes=int(frame[args.regime].nunique()))
        from contextlib import nullcontext
        if args.audit_home:
            home=args.audit_home.expanduser().resolve()
            if home.exists() and any(home.iterdir()):
                raise ValueError('--audit-home 必须为空目录，避免覆盖真实业务数据')
            if str(home)==str(Path.home().resolve()) or home==ROOT:
                raise ValueError('QA 目录不能是用户主目录或源码目录')
            home.mkdir(parents=True,exist_ok=True)
        context=nullcontext(str(home)) if args.audit_home else tempfile.TemporaryDirectory(prefix='timepilot30_pit_real_')
        with context as tmp:
            home=Path(tmp)
            converted=home/'source_typed.parquet'
            frame.to_parquet(converted,index=False)
            report['source']['typed_parquet_sha256']=sha256_file(converted)
            app=create_app(home=home/'isolated_application',embedded_worker=False)
            with TestClient(app) as client:
                project=post(client,'/api/v1/projects',{'name':'FeatureLab 0.31 PIT '+uuid4().hex[:8]},'project')
                pid=project['id'];api='/api/v1/projects/'+pid+'/feature-lab'
                # Register the source CSV through normal project import so the
                # typed materialization is project-scoped and linked to it.
                with source.open('rb') as f:
                    resp=client.post('/api/v1/projects/'+pid+'/imports',files=[('files',(source.name,f,'text/csv'))])
                if resp.status_code not in (200,201,202):raise RuntimeError('CSV 原始文件导入失败 '+resp.text[:700])
                iid=resp.json()['import_id']
                catalog=client.get('/api/v1/projects/'+pid+'/imports/'+iid+'/catalog').json()
                regions=[r['region_id'] for file in catalog['files'] for sheet in file['worksheets'] for r in sheet['regions']]
                if len(regions)!=1:raise ValueError('原始 CSV 检测到多个区域，请先手动核实表头')
                publish=post(client,'/api/v1/projects/'+pid+'/imports/'+iid+'/publish-batch',
                     {'region_ids':regions,'confirm':True},'import')
                app.state.project_publication_jobs.work_once()
                published=client.get('/api/v1/projects/'+pid+'/publish-batches/'+publish['batch_id']).json()
                raw_id=next(x['native_asset_id'] for x in published['items'] if x['status']=='succeeded')
                link=app.state.projects.require_native(pid,raw_id)
                native=app.state.native_bi.import_from_path(converted,'PIT 原始测量 · 类型已校验',
                      {'type':'typed_from_real_csv','raw_csv_sha256':report['source']['sha256'],
                       'typed_sha256':report['source']['typed_parquet_sha256'],'confirmed_field_names': [args.event_time,args.available_at,args.prediction_time,args.label_available,args.target,args.device,args.regime,args.measure_a,args.measure_b]})
                aid=native['asset_id']
                app.state.projects.attach_native(pid,aid,link['source_asset_id'])
                meta=app.state.feature_semantic.source(pid,aid,'base')
                if int(meta['row_count'])!=len(frame) or not app.state.native_bi.verify(aid).get('valid'):
                    raise RuntimeError('导入后行数、数据哈希或资产完整性检查失败')
                log('native_source_ingestion','passed',rows=len(frame),native_asset_id=aid,
                    native_sha256=meta['sha256'])
                contract=client.get(api+'/contracts',params={'asset_id':aid}).json()
                lookup={f['source_name']:f for f in contract['fields']}
                role_map={args.device:('entity_key','id'),args.regime:('regime','category'),
                  args.event_time:('event_time','datetime'),args.available_at:('available_at','datetime'),
                  args.prediction_time:('prediction_time','datetime'),args.label_available:('available_at','datetime'),
                  args.target:('label','numeric'),args.measure_a:('value','numeric'),args.measure_b:('value','numeric')}
                payload={'expected_revision':contract['revision'],'patch':{'timezone':'UTC',
                  'entity_field_ids':[lookup[args.device]['field_id']],
                  'event_time_field_id':lookup[args.event_time]['field_id'],
                  'available_at_field_id':lookup[args.available_at]['field_id'],
                  'fields':[{'field_id':lookup[name]['field_id'],'role':role,
                             'semantic_type':semantic,'timezone':'UTC' if semantic=='datetime' else None,
                             'confirmation':'confirmed'} for name,(role,semantic) in role_map.items()]}}
                resp=client.patch(api+'/contracts/'+aid,json=payload)
                if resp.status_code!=200:raise RuntimeError('字段契约设置失败 '+resp.text[:1200])
                candidate=resp.json()
                confirmed=post(client,api+'/contracts/'+aid+'/confirm',
                         {'expected_revision':candidate['revision']},'contract_confirm')
                if confirmed['state']!='confirmed':raise RuntimeError('监督契约未确认')
                fid={f['source_name']:f['field_id'] for f in confirmed['fields']}
                log('confirmed_supervised_contract','passed',revision=confirmed['revision'],
                  device=args.device,regime=args.regime,measurements=[args.measure_a,args.measure_b])
                task=TaskSpecV2(task_id='same_source_real_csv',mode='supervised',task_type=args.task,
                  entity_field_ids=[fid[args.device]],prediction_time_field_id=fid[args.prediction_time],
                  label_field_id=fid[args.target],label_available_at_field_id=fid[args.label_available],
                  n_splits=3)
                definitions=[FeatureDefinition(feature_id='feat_derived_abs',output_name='tp_abs_measure_a',
                        operator_id='transform.abs',operator_version='2.8',input_field_ids=[fid[args.measure_a]]),
                  FeatureDefinition(feature_id='feat_derived_b',output_name='tp_abs_measure_b',
                        operator_id='transform.abs',operator_version='2.8',input_field_ids=[fid[args.measure_b]])]
                fs=FeatureSpecV2(name='Same source PIT quality audit',
                  source=SourceRef(asset_id=aid,version_id='base',source_sha256=meta['sha256']),
                  contract_revision=confirmed['revision'],task=task,features=definitions)
                created=post(client,api+'/sets',{'spec':fs.model_dump(mode='json'),
                      'request_id':uuid4().hex},'feature_set')
                sid=created['set_id']
                submitted=post(client,api+'/sets/'+sid+'/runs',{'request_id':uuid4().hex},'materialize')
                app.state.feature_jobs.work_once()
                job=client.get(api+'/runs/'+submitted['job_id']).json()
                if job['status']!='succeeded':raise RuntimeError('PIT 正式物化失败 '+str(job))
                eligible=client.get(api+'/sets/'+sid+'/latest-artifact').json()
                if not eligible.get('ready'):raise RuntimeError('产物未通过 PIT / 哈希 / 审核状态核验')
                info=eligible['artifact']
                artifact_id=info['artifact_id']
                path=app.state.feature_jobs.artifact_path(pid,artifact_id)
                if sha256_file(path)!=info['sha256'] or info['row_count']!=len(frame):
                    raise RuntimeError('同源产物 Parquet 校验失败')
                report['artifact']={'artifact_id':artifact_id,'sha256':info['sha256'],
                    'row_count':info['row_count'],'source_sha256':info['source_sha256'],
                    'pit':info.get('pit')}
                log('pit_materialized_verified','passed',artifact_sha256=info['sha256'],
                    source_sha256=info['source_sha256'],rows=info['row_count'])
                display_asset=app.state.native_bi.import_from_path(path,'PIT 物化特征 · 同源验收',
                    {'type':'same_source_feature_qa','source_sha256':meta['sha256'],
                     'artifact_sha256':info['sha256'],'artifact_id':artifact_id})
                app.state.projects.attach_native(pid,display_asset['asset_id'],link['source_asset_id'])
                report['materialized_native_asset_id']=display_asset['asset_id']
                protocol=EvaluationProtocol(task=task,set_id=sid,revision=created['revision'],
                    models=['linear','forest'] if args.models=='both' else ['linear'],
                    selection=QualitySelectionPolicy(max_selected=12),
                    search=AutoSearchPolicy(enabled=False),
                    slices=SlicePolicy(field_ids=[fid[args.device],fid[args.regime]],min_rows=10))
                plan=post(client,api+'/evaluations/plans',
                          {'protocol':protocol.model_dump(mode='json')},'evaluation_plan')
                evjob=post(client,api+'/evaluations/plans/'+plan['plan_id']+'/run',
                          {'request_id':uuid4().hex},'evaluation_run')
                app.state.feature_jobs.work_once()
                state=client.get(api+'/runs/'+evjob['job_id']).json()
                if state['status']!='succeeded':raise RuntimeError('项目模型评价失败 '+str(state))
                result=client.get(api+'/evaluations/'+evjob['job_id']).json()
                eval_report=result['report']
                assert_same_artifact_evidence(eval_report,source_sha=meta['sha256'],
                    spec_sha=created['spec_sha256'],artifact_id=artifact_id,
                    artifact_sha=info['sha256'],revision=confirmed['revision'])
                report['evaluation']={'evaluation_id':result['evaluation_id'],'source_sha256':eval_report['source_sha256'],
                    'spec_sha256':eval_report['spec_sha256'],
                    'evaluated_artifact_sha256':eval_report['evaluated_artifact_sha256'],
                    'evaluated_artifact_id':eval_report['evaluated_artifact_id'],
                    'folds':eval_report['folds'],
                    'summary':eval_report.get('summary'),'slices':eval_report.get('slices'),
                    'stability':eval_report.get('stability')}
                log('same_artifact_supervised_evaluation','passed',folds=3,
                   source_sha256=eval_report['source_sha256'],models=protocol.models)
                from timepilot.native_bi_bundle import bundle_status
                with app.state.native_bi._connection() as con:
                    app.state.native_bi._install_effective_view(con,aid,'base',meta['columns'])
                    src_count=con.execute('SELECT COUNT(*) FROM __tp_effective').fetchone()[0]
                    from timepilot.native_bi_api import prepare_perspective_source
                    origin=prepare_perspective_source(con,'SELECT * FROM __tp_effective')
                    location=str(path).replace("'","''")
                    con.execute(f"CREATE VIEW tp_materialized AS SELECT * FROM read_parquet('{location}')")
                    final_count=con.execute('SELECT COUNT(*) FROM tp_materialized').fetchone()[0]
                    final_cols={r[0] for r in con.execute('DESCRIBE SELECT * FROM tp_materialized').fetchall()}
                if src_count!=final_count or src_count!=len(frame) or not {x.output_name for x in definitions}<=final_cols:
                    raise RuntimeError('同源 Perspective 双视图的数据行/列不一致')
                log('perspective_hosted_duckdb_views','passed',rows=src_count,feature_columns=sorted(final_cols))
                bundle=bundle_status(ROOT/'src'/'timepilot'/'static'/'native-bi')
                report['perspective_browser_receipt']=('blocked_bundle_missing' if not bundle.get('ready') else
                    'requires_external_browser_visual_and_ws_receipt')
                report['status']='passed_backend_same_source_browser_pending'
                report['qa_project_id']=pid
                report['qa_home']=str(home.resolve())
                report['source_native_asset_id']=aid
                report['feature_artifact_id']=artifact_id
                report['feature_artifact_sha256']=info['sha256']
    except Exception as exc:
        report['status']='failed';report['error']=type(exc).__name__+': '+str(exc)[:2000]
        log('interrupted','failed',message=report['error'])
    finally:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Report:',args.report.resolve(),flush=True)
    return 0 if (report['status']=='passed' or (args.backend_only and report['status']=='passed_backend_same_source_browser_pending')) else 2

if __name__=='__main__':raise SystemExit(main())
