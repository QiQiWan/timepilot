"""Leakage-safe selection; all thresholds, correlations and ranks are fit on outer training rows.

The raw-only comparator is intentionally unchanged. Selection applies only to
newly engineered candidates and cannot discard required process baseline fields.
"""
from __future__ import annotations
import math
import warnings
import numpy as np


def _arr(data):
    a=np.asarray(data,dtype=np.float64)
    if a.ndim!=2:raise ValueError('特征矩阵必须为二维')
    return np.where(np.isfinite(a),a,np.nan)


def _abs_corr(a,b,minimum=16):
    good=np.isfinite(a)&np.isfinite(b)
    if int(good.sum())<minimum:return None
    x=a[good];y=b[good]
    if x.std()<1e-12 or y.std()<1e-12:return None
    v=float(np.corrcoef(x,y)[0,1])
    return abs(v) if math.isfinite(v) else None


def select_training_features(train, y, raw_names, candidate_names, policy, *, task_type):
    """Select derived features with training rows only; return absolute column indices.

    Raw columns are not ranked by target and are retained as the fixed baseline.
    Candidates with insufficient finite coverage / variance are removed, then
    training-only target association is used for deterministic ordering.
    """
    matrix=_arr(train)
    names=list(raw_names)+list(candidate_names)
    if len(names)!=matrix.shape[1] or len(names)!=len(set(names)):
        raise ValueError('候选矩阵与字段名称不匹配或存在重复')
    if matrix.shape[1]>1024 or matrix.size>12_000_000:
        raise ValueError('特征选择超过 1024 列或 1200 万单元资源限额')
    if len(matrix)<16:raise ValueError('训练折样本不足，无法稳定筛选特征')
    y=np.asarray(y,dtype=float)
    if len(y)!=len(matrix) or not np.isfinite(y).all():
        raise ValueError('筛选必须仅使用已确认且完整的训练标签')
    base_count=len(raw_names)
    rejected=[];ranked=[]
    for j in range(base_count,len(names)):
        col=matrix[:,j];valid=np.isfinite(col)
        finite=int(valid.sum());miss=1-finite/len(col)
        if finite<policy.min_pair_observations or miss>policy.max_missing_fraction:
            rejected.append({'feature':names[j],'reason':'insufficient_coverage','missing_fraction':round(miss,6)});continue
        spread=float(col[valid].std())
        if not math.isfinite(spread) or spread<=policy.min_std:
            rejected.append({'feature':names[j],'reason':'constant_or_invalid','std':spread if math.isfinite(spread) else None});continue
        relevance=_abs_corr(col,y,policy.min_pair_observations) if policy.rank_method=='univariate' else None
        ranked.append((j,relevance or 0.0,miss))
    ranked.sort(key=lambda t:(-t[1],t[2],names[t[0]]))
    selected=[];scores=[]
    # Protect scientifically meaningful raw columns against duplicated derived outputs.
    raw_comparisons=[matrix[:,j] for j in range(base_count)]
    for j,association,miss in ranked:
        if len(selected)>=policy.max_selected:
            rejected.append({'feature':names[j],'reason':'budget_cap'});continue
        blocked=None
        for k,ref in enumerate([*raw_comparisons,*[matrix[:,idx] for idx in selected]]):
            corr=_abs_corr(matrix[:,j],ref,policy.min_pair_observations)
            if corr is not None and corr>=policy.max_abs_correlation:
                blocked={'feature':names[j], 'reason':'redundant',
                         'kept_field':names[k if k<base_count else selected[k-base_count]],
                         'abs_correlation':round(corr,6)}
                break
        if blocked:
            rejected.append(blocked);continue
        selected.append(j)
        scores.append({'feature':names[j],'rank':len(scores)+1,
                       'train_target_abs_corr':round(association,6),
                       'missing_fraction':round(miss,6)})
    return {'scope':'outer_training_fold_only', 'training_rows':len(matrix),
        'raw_field_count':base_count,'candidate_count':len(candidate_names),
        'selected_indices':selected,'selected_features':[names[j] for j in selected],
        'ranks':scores,'rejected':rejected,'policy':policy.model_dump(),
        'rank_warning':'单变量相关性仅用于训练折排序，不代表特征因果或最终预测贡献'}


def train_fold_diagnostics(train,columns,*,redundancy_abs_corr=.98,max_columns=100):
    """Legacy diagnostic-only API used by existing scripts and tests."""
    matrix=_arr(train)
    if matrix.shape[1]!=len(columns):raise ValueError('训练列数组与特征名称不匹配')
    if matrix.shape[1]>max_columns:raise ValueError('当前特征筛选列数超过资源预算')
    if len(matrix)<3:raise ValueError('特征筛选训练样本不足')
    if len(set(columns))!=len(columns):raise ValueError('重复特征名称')
    invalid=[];redundant=[];kept=[]
    for idx,name in enumerate(columns):
        good=matrix[:,idx][np.isfinite(matrix[:,idx])]
        if len(good)<3 or good.std()<1e-12:
            invalid.append(str(name));continue
        match=next(((columns[k],_abs_corr(matrix[:,idx],matrix[:,k],3))
                    for k in kept if (_abs_corr(matrix[:,idx],matrix[:,k],3) or 0)>=redundancy_abs_corr),None)
        if match:redundant.append({'field':str(name),'kept_field':str(match[0]),'correlation':match[1]})
        else:kept.append(idx)
    return {'scope':'training_fold_only','rows_scanned':len(matrix),
            'kept_fields':[str(columns[i]) for i in kept],
            'constant_or_invalid':invalid,'redundant':redundant,
            'threshold':redundancy_abs_corr,'selection_applied_to_model':False}
