"""Validation-only diagnostics. Never serialize individual y/prediction pairs."""
from __future__ import annotations

import math
from collections import defaultdict

from .model_task_spec import canonical_sha

MIN_SLICE_ROWS = 15
MAX_SLICE_GROUPS = 20
MAX_IMPORTANCE_FEATURES = 12


def finite(v):
    if v is None: return None
    value=float(v)
    return round(value,8) if math.isfinite(value) else None


def metrics(y, prediction, classification):
    import numpy as np
    from sklearn.metrics import (mean_absolute_error,mean_squared_error,r2_score,
             average_precision_score,balanced_accuracy_score,recall_score,precision_score)
    if classification:
        binary=(prediction>=.5).astype(int)
        return {'average_precision':finite(average_precision_score(y,prediction)) if len(np.unique(y))==2 else None,
                'balanced_accuracy':finite(balanced_accuracy_score(y,binary)) if len(np.unique(y))==2 else None,
                'recall':finite(recall_score(y,binary,zero_division=0)),
                'precision':finite(precision_score(y,binary,zero_division=0))}
    denominator=float(np.sum(np.abs(y)))
    return {'mae':finite(mean_absolute_error(y,prediction)),
            'rmse':finite(math.sqrt(mean_squared_error(y,prediction))),
            'r2':finite(r2_score(y,prediction)) if len(y)>1 else None,
            'wape':finite(float(np.sum(np.abs(y-prediction)))/denominator) if denominator>0 else None}


def diagnostics(y, pred, classification):
    import numpy as np
    if classification:
        predicted=pred>=.5
        actual=y==1
        return {'kind':'confusion_matrix','threshold':0.5,
                'tn':int((~predicted&~actual).sum()),
                'fp':int((predicted&~actual).sum()),
                'fn':int((~predicted&actual).sum()),
                'tp':int((predicted&actual).sum()),
                'evaluated_rows':int(len(y))}
    residual=np.asarray(pred-y,dtype=float)
    absolute=np.abs(residual)
    return {'kind':'regression_error', 'evaluated_rows':int(len(y)),
            'mean_signed_error':finite(np.mean(residual)),
            'p50_absolute_error':finite(np.percentile(absolute,50)),
            'p90_absolute_error':finite(np.percentile(absolute,90)),
            'under_prediction_count':int((residual<0).sum()),
            'over_prediction_count':int((residual>0).sum()),
            'exact_count':int((residual==0).sum())}


def aggregate_slices(fold_items, classification):
    """Fold prediction tuples are process-memory only and never persist in JSON.

    Each entry: {model,dimension,value,fold,y,pred}. Small groups are withheld.
    A slice is a retrospective diagnostics filter, never a new training set.
    """
    import numpy as np
    by_key=defaultdict(list)
    for it in fold_items:
        by_key[(it['model'],it['dimension'],it['value'])].append(it)
    output={}
    for (model,dimension,value),records in sorted(by_key.items()):
        if len(records)<MIN_SLICE_ROWS: continue
        y=np.asarray([r['y'] for r in records])
        p=np.asarray([r['prediction'] for r in records],dtype=float)
        metric=metrics(y,p,classification)
        item={'value':value,'validation_rows':len(records),
              'fold_ids':sorted(set(r['fold'] for r in records)),
              'metrics':metric,'diagnostics':diagnostics(y,p,classification),
              'value_sha256':canonical_sha(value)}
        output.setdefault(model,{}).setdefault(dimension,[]).append(item)
    for model,dimensions in output.items():
        for dimension,items in dimensions.items():
            # Stable largest groups first to avoid accidental cherry-picking.
            items.sort(key=lambda v:(-v['validation_rows'],v['value']))
            dimensions[dimension]=items[:MAX_SLICE_GROUPS]
    return output


def permutation_importance(model,x,y,feature_names,classification,*,seed=42):
    """One fixed validation-fold permutation per feature, with a 12-X bound.

    Importance is the drop in held-out average precision (classification) or
    increase in held-out MAE (regression). No causal interpretation.
    """
    import numpy as np
    names=list(feature_names)[:MAX_IMPORTANCE_FEATURES]
    def predict(matrix):
        return (model.predict_proba(matrix)[:,1] if classification else model.predict(matrix))
    primary='average_precision' if classification else 'mae'
    baseline=metrics(y,predict(x),classification)[primary]
    if baseline is None:
        return {'status':'metric_undefined','primary_metric':primary,'items':[]}
    rng=np.random.default_rng(seed)
    items=[]
    for col,name in enumerate(names):
        shuffled=x.copy()
        shuffled[:,col]=shuffled[rng.permutation(len(x)),col]
        score=metrics(y,predict(shuffled),classification)[primary]
        change=(baseline-score if classification else score-baseline) if score is not None else None
        items.append({'feature':name,'delta_metric':finite(change)})
    return {'status':'validation_only_permutation', 'primary_metric':primary,
            'fold_scope':'last_validation_fold_only','repeats':1,'seed':seed,
            'evaluated_features':len(names),
            'omitted_features':list(feature_names)[MAX_IMPORTANCE_FEATURES:],
            'items':items,'interpretation':'Predictive association only; not causal attribution.'}
