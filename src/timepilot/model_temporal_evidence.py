"""Shared fold and row fingerprints for legacy chronological model executors."""
from __future__ import annotations

import hashlib
import pandas as pd

from .model_task_spec import canonical_sha


def indices(frame):
    """Stable normalized observations, never raw source row positions.

    The upstream source PIT ledger proves raw availability; this fingerprint
    separately pins the *materialized* temporal rows that plugins actually see.
    """
    return [canonical_sha({'series':str(row.series_id),
         'time':pd.Timestamp(row.timestamp).isoformat(),
         'target':float(row.target),
         'available_at':pd.Timestamp(row.available_at).isoformat()})
        for row in frame[['series_id','timestamp','target','available_at']].itertuples(index=False)]


def fold_manifest(train,validation,*,fold,series,cutoff,protocol,extra=None):
    t=indices(train);v=indices(validation)
    if not t or not v or set(t)&set(v):raise ValueError('Chronological fold contains empty/overlapping rows')
    events=pd.to_datetime(train['timestamp'],utc=True)
    future=pd.to_datetime(validation['timestamp'],utc=True)
    maturity=pd.to_datetime(train['label_available_at' if 'label_available_at' in train else 'available_at'],utc=True)
    cutoff=pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:raise ValueError('Fold cutoff must have timezone')
    if events.max() >= future.min():raise ValueError('Temporal training includes future validation observations')
    if (maturity>cutoff).any():raise ValueError('Training labels are not mature at fold decision cutoff')
    evidence={'fold':int(fold),'series_id':str(series),'split':protocol,
      'train_rows':len(train),'validation_rows':len(validation),
      'train_index_sha256':canonical_sha(t),'validation_index_sha256':canonical_sha(v),
      'latest_train_event_utc':events.max().isoformat(),
      'first_validation_event_utc':future.min().isoformat(),
      'cutoff_utc':cutoff.isoformat(),
      'training_labels_mature_at_cutoff':True,'shared_by_all_models':True,
      'index_scope':'immutable_materialized_temporal_observations_v1'}
    evidence.update(extra or {})
    return evidence


def prediction_row(*,model,fold,series,row_index,truth,prediction,timestamp,origin,
                   label_known_at=None,source_available_at=None,classification=False):
    event=pd.Timestamp(timestamp)
    return {'model':str(model),'fold':int(fold),'source_row_index':int(row_index),
      'source_row_sha256':canonical_sha({'series':str(series),
                    'time':event.isoformat(),'target':float(truth),
                    'available_at':pd.Timestamp(source_available_at).isoformat()})
                    if source_available_at is not None else '',
      'series_id':str(series),'event_time_utc':event.isoformat(),
      'decision_time_utc':pd.Timestamp(origin).isoformat(),
      'label_mature_utc':pd.Timestamp(label_known_at).isoformat() if label_known_at is not None else None,
      'month':event.strftime('%Y-%m'),'actual':float(truth),'prediction':float(prediction),
      'residual':float(prediction)-float(truth),
      'absolute_error':abs(float(prediction)-float(truth)),
      'predicted_label':int(float(prediction)>=.5) if classification else None,
      'actual_label':int(truth) if classification else None}
