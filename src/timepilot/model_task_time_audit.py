"""Source-backed point-in-time audit for ModelTaskSpec.

A declaration of publication lag is explicitly reported as a *business assumption*;
source timestamp columns provide observed evidence. No row values are published.
The caller must independently freeze/verify the source SHA before and after reads.
"""
from __future__ import annotations

from .model_task_spec import canonical_sha
from .model_task_store import ModelTaskError


def _timestamps(frame, column, *, label, timezone_name):
    import pandas as pd
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if column not in frame.columns:
        raise ModelTaskError(f'Missing {label} timestamp column: {column}')
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ModelTaskError(f'Unknown IANA timezone: {timezone_name}') from None
    raw = frame[column]
    values = raw.astype(str).str.strip()
    offset = values.str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', regex=True, case=False)
    if offset.any() and (~offset).any():
        raise ModelTaskError(f'{label} mixes timezone-aware and naive timestamps: {column}')
    if offset.any():
        parsed = pd.to_datetime(raw, utc=True, errors='coerce')
    else:
        local = pd.to_datetime(raw, utc=False, errors='coerce')
        try:
            parsed = local.dt.tz_localize(tz, ambiguous='NaT', nonexistent='NaT').dt.tz_convert('UTC')
        except (AttributeError, ValueError, TypeError) as exc:
            raise ModelTaskError(f'{label} timestamp timezone normalization failed: {column}') from exc
    if parsed.isna().any():
        raise ModelTaskError(f'{label} timestamp has {int(parsed.isna().sum())} null/invalid/DST-ambiguous rows: {column}')
    return parsed


def audit_availability(frame, spec, *, require_labels=True):
    """Return (immutable, hashed summary, clocks); reject any future-X at decision.

    The returned clocks are local-only Pandas arrays; they are never persisted.
    Offset-less inputs are localized using the declared IANA timezone before
    conversion to UTC. Mixed offset-aware and naive sources are rejected.
    """
    import pandas as pd
    if not spec.time.event_time_column:
        raise ModelTaskError('Time audit needs an explicit event timestamp')
    event = _timestamps(frame, spec.time.event_time_column, label='Event', timezone_name=spec.time.timezone)
    decision = (_timestamps(frame, spec.time.decision_time_column, label='Decision', timezone_name=spec.time.timezone)
                if spec.time.decision_time_column else event)
    if (decision > event).any():
        raise ModelTaskError('Decision timestamp is later than observed event timestamp')
    if spec.time.label_available_at_column:
        labels = _timestamps(frame, spec.time.label_available_at_column, label='Label maturity', timezone_name=spec.time.timezone)
        label_source = {'kind':'source_column','column':spec.time.label_available_at_column}
    elif spec.time.label_maturity_lag_seconds is not None:
        labels = event + pd.to_timedelta(spec.time.label_maturity_lag_seconds,unit='s')
        label_source = {'kind':'declared_lag_assumption',
                        'lag_seconds':spec.time.label_maturity_lag_seconds}
    else:
        if require_labels:
            raise ModelTaskError('Time validation requires explicit label maturity timestamp or lag')
        labels = None
        label_source = {'kind':'unverified_no_label_maturity'}
    if labels is not None and (labels < event).any():
        raise ModelTaskError('Label maturity timestamp precedes the event')
    ledger = []
    for feature in spec.features:
        available_col = feature.available_at_column or spec.time.feature_available_at_column
        if available_col:
            available = _timestamps(frame, available_col, label=f'X {feature.column} publication', timezone_name=spec.time.timezone)
            source = {'kind':'source_column','column':available_col}
        elif spec.time.publication_lag_days is not None:
            available = event + pd.to_timedelta(spec.time.publication_lag_days,unit='D')
            source = {'kind':'declared_lag_assumption',
                      'lag_days':spec.time.publication_lag_days}
        else:
            raise ModelTaskError(f'X {feature.column} has no verified or declared publication time')
        late = available > decision
        # Do not reveal raw timestamps/rows in reportable summary.
        entry = {'feature':feature.column,'role':feature.role,'source':source,
                 'checked_rows':int(len(frame)),'late_rows':int(late.sum()),
                 'first_available_utc':available.min().isoformat(),
                 'last_available_utc':available.max().isoformat(),
                 'available_times_sha256':canonical_sha([t.isoformat() for t in available]),
                 'decision_times_sha256':canonical_sha([t.isoformat() for t in decision]),
                 'status':'violation' if late.any() else 'checked'}
        ledger.append(entry)
        if late.any():
            raise ModelTaskError(f'X is not available at decision time (PIT violation): '
                                 f'{feature.column}, {int(late.sum())} rows')
    summary = {'schema_version':'time-availability-ledger-v1',
               'rows':int(len(frame)), 'timezone':spec.time.timezone,
               'naive_timestamps_localized_using_timezone':True,
               'event_column':spec.time.event_time_column,
               'decision_column':spec.time.decision_time_column or spec.time.event_time_column,
               'decision_clock_defaulted':not bool(spec.time.decision_time_column),
               'label_source':label_source,
               'label_maturity_verified':labels is not None,
               'label_maturity_times_sha256':(canonical_sha([t.isoformat() for t in labels])
                                                 if labels is not None else None),
               'features':ledger,'status':'checked' if labels is not None else 'partial',
               'assumption_count':sum(x['source']['kind']=='declared_lag_assumption' for x in ledger)
                    +int(label_source['kind']=='declared_lag_assumption'),
               'full_sample_checked':True}
    summary['ledger_sha256'] = canonical_sha(summary)
    return summary, event, decision, labels


def make_temporal_folds(frame, spec, *, event, label_available):
    """Expanding, forward-only folds; return (train, validation, cutoff, note).

    All rows on identical timestamps remain on the same side of any boundary.
    A gap is expressed as a number of last *unique timestamps*, avoiding
    partially purging devices from a shared business decision instant.
    """
    import numpy as np
    import pandas as pd
    split = spec.validation.kind
    unique = pd.DatetimeIndex(event.unique()).sort_values()
    if len(unique)<10:
        raise ModelTaskError('Temporal validation requires at least 10 distinct timestamps')
    if split=='temporal_holdout':
        pivots = [unique[max(1,int(len(unique)*.8))]]
        periods = [(pivots[0],None)]
    elif split=='rolling_origin':
        blocks = np.array_split(unique, spec.validation.folds+2)
        if any(len(b)==0 for b in blocks):
            raise ModelTaskError('Not enough distinct timestamps for rolling folds')
        periods = [(b[0], blocks[i+3][0] if i+3<len(blocks) else None)
                   for i,b in enumerate(blocks[2:])]
    elif split=='leave_month_out':
        months=sorted(event.dt.strftime('%Y-%m').unique().tolist())
        if len(months)<spec.validation.folds+1:
            raise ModelTaskError('Cross-month evaluation requires more months than folds')
        selected=months[-spec.validation.folds:]
        periods = []
        for month in selected:
            mask=event.dt.strftime('%Y-%m')==month
            periods.append((event[mask].min(),
                            (event[~mask & (event>event[mask].max())].min()
                             if ((~mask)&(event>event[mask].max())).any() else None)))
    else:
        raise ModelTaskError(f'Unsupported forward-only protocol: {split}')
    output=[]
    for i,(cutoff,end) in enumerate(periods,1):
        tr=np.flatnonzero((event<cutoff).to_numpy())
        va=np.flatnonzero(((event>=cutoff)&((event<end) if end is not None else True)).to_numpy())
        if spec.validation.gap:
            train_times=pd.DatetimeIndex(event.iloc[tr].unique()).sort_values()
            if len(train_times)<=spec.validation.gap:
                raise ModelTaskError('Embargo gap removes all historical training timestamps')
            bound=train_times[-spec.validation.gap]
            tr=tr[(event.iloc[tr]<bound).to_numpy()]
        # A training label must be mature before the *first validation decision*.
        # The cutoff is intentionally conservative when later decisions exist.
        tr=tr[(label_available.iloc[tr]<=cutoff).to_numpy()]
        output.append((tr,va,cutoff,{'fold':i,'gap_unit':'unique_event_timestamps'}))
    return output
