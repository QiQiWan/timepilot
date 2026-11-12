"""Strict label domain and fold reporting for industrial quality tasks."""
from __future__ import annotations
import numpy as np
import pandas as pd


def validate_target(values,task_type):
    """Reject unknown class codes rather than silently converting >=0.5 to 1."""
    raw=pd.Series(values)
    arr=pd.to_numeric(raw,errors='coerce').to_numpy(dtype=float)
    if not np.isfinite(arr).all():
        raise ValueError('标签含缺失、非数值或无穷值，需先确认标注与缺失处理策略')
    if task_type=='binary_classification':
        if not np.isin(arr,[0,1]).all():
            bad=np.unique(arr[~np.isin(arr,[0,1])])[:10].tolist()
            raise ValueError(f'二分类标签必须使用经确认的 0/1 编码，检测到非法标签：{bad}')
        return arr,{'kind':'binary','negative_label':0,'positive_label':1,
                    'threshold':0.5,'total_count':len(arr),
                    'class_counts':{'0':int((arr==0).sum()),'1':int((arr==1).sum())},
                    'mapped':False}
    if task_type=='regression':
        return arr,{'kind':'continuous','total_count':len(arr),'missing_count':0}
    raise ValueError('未支持的监督任务类型')


def fold_quality(values):
    arr=np.asarray(values)
    labels=np.unique(arr)
    return {'observed_classes':labels.tolist(),'single_class':len(labels)<2,
            'positive_count':int((arr==1).sum()),'negative_count':int((arr==0).sum()),
            'sample_count':len(arr)}
