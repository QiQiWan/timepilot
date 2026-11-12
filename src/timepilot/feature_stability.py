"""Explicit OOT and group slices. Validation rows used for measurement, never fitting.

PSI is a descriptive shift indicator, not a p-value or a model-gain score.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from timepilot.feature_evaluation import _metrics


def psi(train, valid, bins=10):
    a=np.asarray(train,dtype=float);b=np.asarray(valid,dtype=float)
    a=a[np.isfinite(a)];b=b[np.isfinite(b)]
    if len(a)<20 or len(b)<10:return None
    edges=np.unique(np.quantile(a,np.linspace(0,1,bins+1)[1:-1]))
    if len(edges)<2:return None
    pa=np.bincount(np.searchsorted(edges,a,side='right'),minlength=len(edges)+1).astype(float)
    pb=np.bincount(np.searchsorted(edges,b,side='right'),minlength=len(edges)+1).astype(float)
    pa=(pa+0.5)/(pa.sum()+0.5*len(pa));pb=(pb+0.5)/(pb.sum()+0.5*len(pb))
    return round(float(np.sum((pb-pa)*np.log(pb/pa))),6)


def drift_summary(train,valid,names,max_fields=30):
    a=np.asarray(train,dtype=float);b=np.asarray(valid,dtype=float)
    if a.ndim!=2 or b.ndim!=2 or a.shape[1]!=b.shape[1] or len(names)!=a.shape[1]:
        raise ValueError('训练/验证特征维度不匹配')
    res=[]
    for i in range(min(len(names),max_fields)):
        n_train=int(np.isfinite(a[:,i]).sum());n_val=int(np.isfinite(b[:,i]).sum())
        p=psi(a[:,i],b[:,i])
        res.append({'feature':names[i],'psi':p,
            'train_missing':round(1-n_train/len(a),5),
            'valid_missing':round(1-n_val/len(b),5)})
    res.sort(key=lambda row:row['psi'] if row['psi'] is not None else -1,reverse=True)
    return {'basis':'train_quantiles_validation_monitor_only',
            'fields':res,'warnings':['PSI 测量分布漂移，不表示统计显著或预测因果贡献']}


def sliced_metrics(frame,truth,pred,kind,group_columns,policy):
    if not policy.enabled or not group_columns:return []
    groups=[]
    frame=frame.reset_index(drop=True)
    for field in group_columns:
        if field not in frame:continue
        values=frame[field].astype('string').fillna('（缺失）')
        counts=values.value_counts(dropna=False).head(policy.max_groups)
        slices=[]
        for name,size in counts.items():
            idx=np.flatnonzero((values==name).to_numpy(dtype=bool))
            if len(idx)<policy.min_rows:continue
            target=np.asarray(truth)[idx]
            if kind=='binary_classification' and int((target==1).sum())<policy.min_positives:
                slices.append({'group':str(name),'rows':len(idx),'metrics':None,
                               'status':'insufficient_positives'})
                continue
            slices.append({'group':str(name),'rows':len(idx),
                'metrics':_metrics(target,np.asarray(pred)[idx],kind),'status':'measured'})
        groups.append({'field':field,'observed_groups':int(values.nunique()),'slices':slices,
                       'total_validation_rows':len(frame),'sample_selection':'validation_only'})
    return groups


def summarize_folds(folds,task_type):
    """Cross-fold descriptive consistency; missing metrics are excluded, not converted to zero."""
    names=sorted({model for fold in folds for model in fold.get('model_metrics',{})})
    metric_order=['mae','rmse'] if task_type=='regression' else ['average_precision','recall','precision','false_reject_rate','balanced_accuracy']
    direction={'mae':-1,'rmse':-1,'false_reject_rate':-1}
    output={}
    for model in names:
        variants=sorted({var for fold in folds for var in fold.get('model_metrics',{}).get(model,{})})
        results={}
        for variant in variants:
            row={}
            for metric in metric_order:
                vals=[fold['model_metrics'][model][variant].get(metric)
                      for fold in folds if variant in fold.get('model_metrics',{}).get(model,{})]
                vals=[float(v) for v in vals if isinstance(v,(float,int)) and math.isfinite(v)]
                row[metric]={'mean':round(float(np.mean(vals)),6) if vals else None,
                             'std':round(float(np.std(vals)),6) if vals else None,
                             'worst':round(float(min(vals) if direction.get(metric,1)>0 else max(vals)),6) if vals else None,
                             'folds':len(vals)}
            results[variant]=row
        output[model]=results
    return {'models':output,'outer_folds':len(folds),
            'interpretation':'多折稳定性为描述性统计，跨折并非独立重复实验，不能直接推断显著性'}
