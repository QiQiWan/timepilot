"""Budget-capped nested feature search; outer validation is never consulted for search.

Scalar formulas are reifiable into the v0.28 formula registry after explicit review.
Search is off by default. Multi-table / tsfresh remain independent optional plugins.
"""
from __future__ import annotations
import math
import time
import warnings
import numpy as np
from timepilot.feature_selection import _abs_corr


def _score(y,pred,kind):
    from sklearn.metrics import average_precision_score,mean_absolute_error
    if kind=='binary_classification':
        if len(np.unique(y))<2:return None
        return float(average_precision_score(y,pred))
    return -float(mean_absolute_error(y,pred))


def _search_model(x,y,xv,kind):
    from timepilot.feature_evaluation import _fit_predict
    return _fit_predict(x,y,xv,kind,'linear')


def _vector(kind, columns, raw):
    """Pure numeric operations; no learned preprocessing or future-row lookups."""
    with np.errstate(all='ignore'):
        a=raw[:,columns[0]]
        if kind=='absolute':value=np.abs(a)
        elif kind=='square':value=a*a
        elif kind=='log_abs':value=np.log1p(np.abs(a))
        elif kind=='sqrt_abs':value=np.sqrt(np.abs(a))
        elif kind=='difference':value=a-raw[:,columns[1]]
        elif kind=='ratio':value=np.divide(a,raw[:,columns[1]],out=np.full(len(a),np.nan),where=raw[:,columns[1]]!=0)
        elif kind=='product':value=a*raw[:,columns[1]]
        else:raise ValueError('未知搜索算子')
    return np.where(np.isfinite(value),value,np.nan)


def _recipes(raw, names, field_ids, units, y, max_candidates):
    ranking=sorted(range(raw.shape[1]),
                   key=lambda i:(-(_abs_corr(raw[:,i],y,16) or 0),names[i]))
    ranked=ranking[:min(6,len(ranking))]
    options=[]
    for i in ranked:
        for kind,exp in [('absolute','abs(x0)'),('square','x0 * x0'),
                         ('log_abs','log1p(abs(x0))'),('sqrt_abs','sqrt(abs(x0))')]:
            options.append({'kind':kind,'columns':[i],'source_fields':[field_ids[i]],
                            'expression':exp,'name':f'{names[i]} · {kind}'})
    for pos,i in enumerate(ranked):
        for j in ranked[pos+1:]:
            # Cross-variable arithmetic requires compatible declared units.
            if not units[i] or units[i]!=units[j]:continue
            for kind,exp in [('difference','x0-x1'),('ratio','safe_div(x0,x1)')]:
                options.append({'kind':kind,'columns':[i,j],
                        'source_fields':[field_ids[i],field_ids[j]],
                        'expression':exp,'name':f'{names[i]} / {names[j]} · {kind}'})
    return options[:max_candidates]


def search_training_fold(raw_train,raw_valid,y_train,kind,policy,raw_names,field_ids,units,
                         *,inner_times=None,inner_groups=None):
    """Returns selected inner-validated expressions and outer matrices.

    The outer validation matrix is transformed only after the inner ranking has
    been frozen. This method never uses outer y_valid.
    """
    start=time.monotonic()
    n=len(raw_train)
    blank={'status':'skipped','reason':'budget_or_class','recipes':[],
           'evaluated':0,'time_seconds':0,'inner_split':'last_quarter_of_outer_train'}
    if not policy.enabled:return {**blank,'reason':'disabled'},np.empty((n,0)),np.empty((len(raw_valid),0))
    if n<60 or raw_train.shape[1]>768:
        return {**blank,'reason':'insufficient_rows_or_too_many_raw_columns'},np.empty((n,0)),np.empty((len(raw_valid),0))
    mid=int(n*.75)
    y1=np.asarray(y_train[:mid]);y2=np.asarray(y_train[mid:])
    if kind=='binary_classification' and (len(np.unique(y1))<2 or len(np.unique(y2))<2):
        return {**blank,'reason':'inner_holdout_single_class'},np.empty((n,0)),np.empty((len(raw_valid),0))
    recipes=_recipes(raw_train[:mid],raw_names,field_ids,units,y1,policy.max_candidates)
    if not recipes:return {**blank,'reason':'no_compatible_candidates'},np.empty((n,0)),np.empty((len(raw_valid),0))
    base_pred=_search_model(raw_train[:mid],y1,raw_train[mid:],kind)
    base_score=_score(y2,base_pred,kind)
    if base_score is None:return {**blank,'reason':'inner_metric_unavailable'},np.empty((n,0)),np.empty((len(raw_valid),0))
    results=[];tried=0;stop='candidates_exhausted'
    for recipe in recipes:
        if tried>=policy.max_model_fits:stop='max_model_fits';break
        if time.monotonic()-start>policy.max_seconds_per_fold:stop='elapsed_budget';break
        vector=_vector(recipe['kind'],recipe['columns'],raw_train)
        if np.isfinite(vector[:mid]).mean()<.4 or np.nanstd(vector[:mid])<=1e-10:continue
        # New candidate cannot simply reproduce an existing process column.
        if any((_abs_corr(vector[:mid],raw_train[:mid,i],16) or 0)>.995
                for i in range(raw_train.shape[1])):continue
        aug=np.column_stack([raw_train,vector]);tried+=1
        p=_search_model(aug[:mid],y1,aug[mid:],kind)
        val_score=_score(y2,p,kind)
        if val_score is None:continue
        results.append((val_score-base_score,recipe))
    results.sort(key=lambda row:(-row[0],row[1]['name']))
    winners=[r for gain,r in results if gain>policy.min_gain][:policy.max_winners]
    train_cols=[_vector(r['kind'],r['columns'],raw_train) for r in winners]
    valid_cols=[_vector(r['kind'],r['columns'],raw_valid) for r in winners]
    tr=np.column_stack(train_cols) if train_cols else np.empty((n,0))
    va=np.column_stack(valid_cols) if valid_cols else np.empty((len(raw_valid),0))
    report={'status':'completed','inner_split':'chronological_last_quarter_of_outer_train',
            'inner_train_rows':mid,'inner_valid_rows':n-mid,
            'inner_baseline_metric':base_score,'objective':'average_precision' if kind=='binary_classification' else 'negative_mae',
            'candidates_generated':len(recipes),'evaluated':tried,'model_fits':tried+1,
            'stop_reason':stop,'time_seconds':round(time.monotonic()-start,3),
            'recipes':[{**r,'inner_gain':round(float(gain),6)} for gain,r in results[:policy.max_winners] if gain>policy.min_gain],
            'caution':'仅训练数据内部验证确定搜索结果，外层验证仅用于最终报告，配方须审核后重新物化'}
    return report,tr,va
