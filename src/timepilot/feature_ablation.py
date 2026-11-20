"""Bounded, paired outer-fold feature-group ablation.

This is predictive *sensitivity*, not a causal or independent-test claim.
Groups are nominated from the fixed feature specification before validation,
never ranked by validation labels to choose a model. Every variant re-fits on
exactly the same training indices and scores the same held-out fold.
"""
from __future__ import annotations
from collections import defaultdict
import math


def ablate_fold(train, valid, train_y, valid_y, *, raw_count, names,
                selected_indices, task_type, family, seed, groups=None,
                max_groups=3):
    from timepilot.feature_evaluation import _fit_predict, _metrics
    import numpy as np
    full=[*range(raw_count),*list(selected_indices)]
    if not selected_indices:return {'status':'no_selected_features','groups':[],
         'scope':'outer_training_fold_fit_validation_fold_score'}
    name_list=list(names)
    buckets=defaultdict(list)
    for idx in selected_indices:
        name=name_list[idx]
        label=(groups or {}).get(name) or 'feature:'+name
        buckets[label].append(idx)
    # Fixed deterministic order independent of validation labels. The first
    # max_groups groups are analyzed; the others stay in the selected model.
    chosen=sorted(buckets,key=lambda k:(-len(buckets[k]),str(k)))[:max_groups]
    fit=lambda idx:_fit_predict(train[:,idx],train_y,valid[:,idx],task_type,family,seed=seed)
    full_scores=_metrics(valid_y,fit(full),task_type)
    key='mae' if task_type=='regression' else 'average_precision'
    sign=1 if task_type=='regression' else -1
    rows=[]
    for label in chosen:
        removed=set(buckets[label]);remaining=[j for j in full if j not in removed]
        if not remaining:continue
        reduced=_metrics(valid_y,fit(remaining),task_type)
        left=full_scores.get(key);right=reduced.get(key)
        loss=sign*(right-left) if (isinstance(left,(int,float)) and isinstance(right,(int,float))
                                    and math.isfinite(float(left)) and math.isfinite(float(right))) else None
        rows.append({'group':label,'features':[name_list[j] for j in buckets[label]],
            'removed_feature_count':len(buckets[label]),'metric':key,
            'selected_score':left,'without_group_score':right,
            'loss_when_removed':round(float(loss),8) if loss is not None else None,
            'interpretation':'正值表示该验证折中删掉这一组后模型变差；非因果解释。'})
    return {'status':'evaluated' if rows else 'unavailable','groups':rows,
        'full_selected_model_metrics':full_scores,'fits':len(rows)+1,
        'paired_fold':True,'source':'model_refit_on_same_train_valid_rows',
        'scope':'outer_training_fold_fit_validation_fold_score',
        'selection_of_groups':'fixed_spec_not_based_on_validation_label'}


def summarize_ablation(folds):
    from collections import defaultdict
    values=defaultdict(list)
    for f in folds:
        for g in (f.get('ablation') or {}).get('groups',[]):
            if g.get('loss_when_removed') is not None:
                values[g['group']].append(g['loss_when_removed'])
    return {'groups':[{'group':key,'evaluated_folds':len(v),'mean_loss_when_removed':round(sum(v)/len(v),8),
                        'positive_loss_folds':sum(x>0 for x in v),
                        'min_loss':round(min(v),8),'max_loss':round(max(v),8)}
                       for key,v in sorted(values.items())],
            'scope':'paired_outer_validation_folds','causal_claim':False,
            'limitation':'仅为已选择特征组的再拟合敏感性；候选选择仍需独立外部验证。'}
