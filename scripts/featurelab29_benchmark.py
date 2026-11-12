#!/usr/bin/env python3
"""v0.29 numerical protocol check on an explicitly supplied CSV.

This is a VALUE-LEVEL REPLAY, not the full TimePilot materialization/PIT gate.
For production evidence, first materialize a PIT-verified FeatureSet in the app.
Synthetic mode is explicit and never labelled real industrial measurements.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.feature_contracts import (DataContract,FieldContract,TaskSpecV2,
    EvaluationProtocol,QualitySelectionPolicy,AutoSearchPolicy,SlicePolicy)
from timepilot.feature_evaluation import _evaluate_matrix


def make_fixture(path,n=360):
    rng=np.random.default_rng(43)
    times=pd.date_range('2025-01-01T00:00:00Z',periods=n,freq='h')
    p=rng.normal(0,1,n);t=rng.normal(0,1,n)
    y=(.7*p+.4*t+rng.normal(0,.8,n)>.4).astype(int)
    frame=pd.DataFrame({'prediction_time':times,'label_available_at':times+pd.Timedelta(minutes=8),
      'device_id':[f'DEV-{i%3:02d}' for i in range(n)],
      'air_pressure':p,'surface_temperature':t,'pressure_delta':p-t,
      'temperature_absolute':abs(t),'constant_test':np.ones(n),'quality_flag':y})
    frame.to_csv(path,index=False)
    return path


def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description='TimePilot 0.29 fold-only numerical evaluation acceptance')
    parser.add_argument('--csv',type=Path,help='CSV from an external source; never modified')
    parser.add_argument('--synthetic-fixture',action='store_true',help='Generate deterministic test data')
    parser.add_argument('--time',default='prediction_time',help='Confirmed prediction-time column')
    parser.add_argument('--label-available',default='label_available_at',help='Confirmed label availability column')
    parser.add_argument('--target',default='quality_flag',help='Confirmed binary 0/1 or numeric regression target')
    parser.add_argument('--process',default='air_pressure,surface_temperature',help='Confirmed as-of process columns')
    parser.add_argument('--features',default='pressure_delta,temperature_absolute,constant_test',help='Already computed derived columns')
    parser.add_argument('--group',default='device_id',help='Confirmed group column (optional)')
    parser.add_argument('--task-type',choices=['binary_classification','regression'],default='binary_classification')
    parser.add_argument('--timezone',default='UTC',help='IANA timezone of naive timestamps; must be confirmed')
    parser.add_argument('--models',choices=['linear','forest','both'],default='linear')
    parser.add_argument('--search',action='store_true')
    parser.add_argument('--report',type=Path,default=Path('reports/v029/benchmark.json'))
    args=parser.parse_args(argv)
    report={'version':'0.29.0','status':'blocked','acceptance_level':'value_only_not_full_pit_or_bi',
            'provenance':'synthetic_fixture' if args.synthetic_fixture else 'external_file_declared_by_operator',
            'limitations':['未通过 TimePilot 原生物化产物及 PIT 源字段逐列可用时间验证',
                           '离线数值验证不能替代真实产线数据审计或 Perspective 原生浏览器回执']}
    try:
        if args.synthetic_fixture and args.csv:raise ValueError('测试生成模式不得与外部 CSV 并用')
        if not args.synthetic_fixture and not args.csv:
            raise ValueError('必须显式提供 --csv，或通过 --synthetic-fixture 启用模拟数据')
        import tempfile
        with tempfile.TemporaryDirectory(prefix='tp29_') as tmp:
            csvfile=make_fixture(Path(tmp)/'generated.csv') if args.synthetic_fixture else args.csv.expanduser().resolve()
            if not csvfile.is_file():raise ValueError('CSV 文件不存在：'+str(csvfile))
            report['input']={'filename':csvfile.name,'sha256':sha256(csvfile),'bytes':csvfile.stat().st_size}
            frame=pd.read_csv(csvfile,low_memory=False)
            if len(frame)<120:raise ValueError('最少需要120条样本做时序验证')
            sources=[s.strip() for s in args.process.split(',') if s.strip()]
            derived=[s.strip() for s in args.features.split(',') if s.strip()]
            required=[args.time,args.label_available,args.target,*sources,*derived]
            if args.group:required.append(args.group)
            if len(required)!=len(set(required)) or set(required)-set(frame.columns):
                raise ValueError('列名重复或不存在：'+str(sorted(set(required)-set(frame.columns))))
            if not sources or not derived:raise ValueError('必须提供至少一列过程字段和一列事先计算的派生字段')
            from zoneinfo import ZoneInfo
            ZoneInfo(args.timezone)
            for key in (args.time,args.label_available):
                series=pd.to_datetime(frame[key],errors='raise',utc=False)
                if series.dt.tz is None:
                    series=series.dt.tz_localize(args.timezone,ambiguous='raise',nonexistent='raise')
                frame[key]=series.dt.tz_convert('UTC')
            from timepilot.feature_contracts import DataContract,FieldContract
            fields=[]
            for i,key in enumerate(frame.columns):
                role=('prediction_time' if key==args.time else 'available_at' if key==args.label_available else
                    'label' if key==args.target else 'entity_key' if key==args.group else 'value')
                is_time=key in (args.time,args.label_available)
                typ='TIMESTAMPTZ' if is_time else 'VARCHAR' if key==args.group else 'DOUBLE'
                fields.append(FieldContract(field_id='fld_'+str(i).zfill(4),source_name=key,
                       source_column_index=i,physical_type=typ,
                       semantic_type='datetime' if is_time else 'id' if key==args.group else 'numeric',
                       role=role,confirmation='confirmed'))
            by_name={f.source_name:f.field_id for f in fields}
            contract=DataContract(asset_id='csv_benchmark',version_id='base',
                source_sha256=report['input']['sha256'],revision=1,state='confirmed',
                fields=fields,timezone=args.timezone)
            task=TaskSpecV2(task_id='operator_supplied_evaluation',mode='supervised',
                task_type=args.task_type,prediction_time_field_id=by_name[args.time],
                label_field_id=by_name[args.target],label_available_at_field_id=by_name[args.label_available],
                n_splits=3)
            protocol=EvaluationProtocol(task=task,set_id='benchmark_only',revision=1,
                selection=QualitySelectionPolicy(max_selected=16),
                search=AutoSearchPolicy(enabled=args.search,max_candidates=24,max_model_fits=6,max_seconds_per_fold=20),
                slices=SlicePolicy(field_ids=[by_name[args.group]] if args.group else []),
                models=['linear','forest'] if args.models=='both' else [args.models])
            results=_evaluate_matrix(frame,sources,derived,task,protocol,contract,
                source_sha256=report['input']['sha256'],spec_sha256='offline_unverified')
            report.update(status='passed_numerical_only',rows=len(frame),
                observed_label_counts=frame[args.target].value_counts(dropna=False).to_dict(),
                report=results)
            print('Numerical folds:',len(results['folds']),'source_sha256:',report['input']['sha256'])
            for fold in results['folds']:
                print('Fold',fold['fold'],'train',fold['train_count'],'validation',fold['valid_count'],
                      'selected',fold['training_feature_selection']['selected_features'])
    except Exception as exc:
        report.update(status='blocked',error=f'{type(exc).__name__}: {exc}')
        print('BLOCKED:',report['error'],file=sys.stderr)
    finally:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('Report:',args.report.resolve())
    return 0 if report['status']=='passed_numerical_only' else 2

if __name__=='__main__':raise SystemExit(main())
