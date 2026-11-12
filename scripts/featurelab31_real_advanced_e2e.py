#!/usr/bin/env python3
"""Real tsfresh and Featuretools calculations on the exact configured CSV bytes.

Same SHA as supervised backend report is mandatory; no demonstration fixtures,
mock modules or synthetic measurements are accepted. Outputs are candidate
Parquet, still requiring explicit review and full PIT model-input join.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def evaluate(args):
    import pandas as pd
    from timepilot.feature_advanced import run_tsfresh,run_featuretools
    from featurelab30_e2e import verify_input
    from timepilot.storage import sha256_file
    backend=json.loads(args.backend.read_text(encoding='utf-8'))
    if backend.get('status')!='passed_backend_same_source_browser_pending':
        raise ValueError('必须先完成真实 CSV 的同源 PIT 物化及模型评价')
    rawsha=sha(args.csv)
    if rawsha!=backend.get('source',{}).get('sha256'):
        raise ValueError('高级算法所读 CSV 与已评价源文件 SHA256 不一致')
    if not all(importlib.util.find_spec(n) for n in ('tsfresh','featuretools','pyarrow')):
        raise ImportError('真实 tsfresh、Featuretools、PyArrow 缺失，禁止使用模拟算法代替')
    raw=pd.read_csv(args.csv,low_memory=False)
    check=SimpleNamespace(event_time=args.event_time,available_at=args.available_at,
       prediction_time=args.prediction_time,label_available=args.label_available,
       target=args.target,device=args.device,regime=args.regime,
       measure_a=args.measure_a,measure_b=args.measure_b,
       task=args.task,timezone=args.timezone,rows_limit=200000)
    frame=verify_input(raw,check)
    # Materialize windows exclusively from observed rows. Original source is never
    # modified, and target/quality label is not supplied to either algorithm.
    frame=frame.sort_values([args.device,args.event_time],kind='stable').reset_index(drop=True)
    ts_rows=[];decision_rows=[]
    for index,record in frame.iterrows():
        cutoff=record[args.prediction_time]
        past=frame[(frame[args.device]==record[args.device]) &
          (frame[args.event_time]<=cutoff) & (frame[args.available_at]<=cutoff)]
        history=past.sort_values(args.event_time).tail(6)
        if len(history)<2 or history[args.event_time].nunique()<2:continue
        name=f'row_{index:06d}'
        decision_rows.append({'id':name,'device':record[args.device],'cutoff':cutoff})
        for _,event in history.iterrows():
            ts_rows.append({'sample':name,'event_time':event[args.event_time],
              'available_at':event[args.available_at],'cutoff':cutoff,
              'signal_a':event[args.measure_a],'signal_b':event[args.measure_b]})
        if len(decision_rows)>=min(48,args.max_decisions):break
    if len(decision_rows)<12:raise ValueError('同源业务文件中有效历史窗口不足12组，不能验证高级时序算法')
    limits={'max_rows':5000,'max_pairs':50000,'max_features':256,
            'max_seconds':120,'max_memory_mb':1024}
    ts,tsproof=run_tsfresh(pd.DataFrame(ts_rows),{
        'engine':'tsfresh','sample':'sample','event_time':'event_time',
        'available_at':'available_at','cutoff':'cutoff',
        'values':['signal_a','signal_b'],'preset':'minimal',
        'min_observations':2,'min_distinct_timestamps':2,
        'min_time_span_seconds':1,'min_valid_ratio':.7,'limits':limits})
    if len(ts)!=len(decision_rows) or '__candidate_cutoff_utc' not in ts:
        raise ValueError('tsfresh 产物未一一绑定预测时点')
    decisions=pd.DataFrame(decision_rows).rename(columns={'id':'decision_id','cutoff':'prediction_time'})
    events=frame[[args.device,args.event_time,args.available_at,args.measure_a,args.measure_b]].copy()
    events=events.rename(columns={args.device:'device',args.event_time:'event_time',
        args.available_at:'available_at',args.measure_a:'signal_a',args.measure_b:'signal_b'})
    events=events.sort_values('event_time',kind='stable').tail(500)
    ft,ftproof=run_featuretools(decisions,events,{
        'engine':'featuretools','decision_id':'decision_id','decision_entity':'device',
        'cutoff':'prediction_time','event_entity':'device',
        'event_time':'event_time','available_at':'available_at',
        'values':['signal_a','signal_b'],'primitives':['mean','max','count'],
        'limits':limits})
    if len(ft)!=len(decisions) or '__candidate_cutoff_utc' not in ft:
        raise ValueError('Featuretools 产物缺失逐预测实例 PIT 截止时点')
    outdir=args.out.parent/'advanced_real_artifacts';outdir.mkdir(parents=True,exist_ok=True)
    results={}
    for engine,data,proof in [('tsfresh',ts,tsproof),('featuretools',ft,ftproof)]:
        path=outdir/(engine+'_'+rawsha[:12]+'.parquet')
        data.to_parquet(path,index=False)
        results[engine]={'status':'passed','source_sha256':rawsha,'artifact_sha256':sha256_file(path),
            'artifact_path':str(path.resolve()),'rows':len(data),'columns':list(data.columns),
            'pit':proof,'scope':'same_real_csv_observed_historical_windows'}
    return {'status':'passed','source_sha256':rawsha,
      'source_provenance':'same_operator_declared_external_csv_as_backend',
      'sampled_decision_rows':len(decisions),'labels_used_as_algorithm_inputs':False,
      'tsfresh':results['tsfresh'],'featuretools':results['featuretools'],
      'review':'候选尚未模型回接/验证收益，不允许自动发布'}

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--csv',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True)
    for name in ('event-time','available-at','prediction-time','label-available','target','device','regime','measure-a','measure-b'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--timezone',default='Asia/Shanghai')
    p.add_argument('--task',default='binary_classification',choices=('binary_classification','regression'))
    p.add_argument('--max-decisions',type=int,default=48)
    p.add_argument('--out',type=Path,default=Path('reports/v031/advanced_real.json'))
    a=p.parse_args(argv)
    try:
        if a.max_decisions<12 or a.max_decisions>128:raise ValueError('最大实例数须为12—128')
        report=evaluate(a)
    except Exception as exc:
        report={'status':'blocked','reason':type(exc).__name__+': '+str(exc)[:1600]}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({k:v if not isinstance(v,dict) else v.get('status') for k,v in report.items()},ensure_ascii=False,indent=2))
    return 0 if report['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
