"""Evidence-based time-role suggestions. Nothing changes the source asset.

Names are weak evidence, never proof of a real calendar timestamp. A numeric
sequence may be used for row-based operators but is not a PIT timestamp.
"""
from __future__ import annotations

import math
import re

_TIME = re.compile(r'(^|[_\s-])(time|date|datetime|timestamp|ts|dt|\u65f6\u95f4|\u65e5\u671f|\u65f6\u523b|\u91c7\u6837\u65f6\u95f4)([_\s-]|$)', re.I)
_ELAPSED = re.compile(r'(elapsed|duration|uptime|time_delta|since_start|elapsed_s|elapsed_ms|\u65f6\u957f|\u8fd0\u884c\u65f6\u957f|\u65f6\u95f4\u95f4\u9694)', re.I)
_SEQ = re.compile(r'(^|[_\s-])(index|seq|sequence|row|step|sample|order|\u5e8f\u53f7|\u884c\u5e8f\u53f7|\u7d22\u5f15|\u6b65\u9aa4)([_\s-]|$)', re.I)
_NUMERIC = re.compile(r'^(U?INT|BIGINT|SMALLINT|TINYINT|HUGEINT|FLOAT|DOUBLE|REAL|DECIMAL)', re.I)


def infer_time_basis(field, statistics=None, row_count=None):
    """Return conservative interpretation with auditable evidence (no side effects)."""
    name = str(getattr(field, 'source_name', '') or '')
    physical = str(getattr(field, 'physical_type', '') or '').upper()
    stats = statistics or {}
    role = getattr(field, 'role', 'unspecified')
    confirmation = getattr(field, 'confirmation', 'unconfirmed')
    is_calendar = ('TIMESTAMP' in physical or 'DATE' in physical)
    is_numeric = bool(_NUMERIC.match(physical))
    evidence=[]
    if is_numeric and role=='order_index' and confirmation=='confirmed':
        kind='sequence_order_candidate'; confidence=1.0
        evidence.append('Numeric row order explicitly confirmed by user; not calendar time')
    elif is_numeric and role=='elapsed_time' and confirmation=='confirmed':
        kind='elapsed_time_candidate'; confidence=1.0
        evidence.append('Elapsed-time role confirmed; an explicit unit still governs duration')
    elif is_calendar:
        kind='calendar_time'
        evidence.append('Physical DATE/TIMESTAMP type')
        confidence=0.98 if role in ('event_time','available_at','prediction_time') and confirmation=='confirmed' else 0.85
    elif _ELAPSED.search(name) and is_numeric:
        kind='elapsed_time_candidate'
        evidence.append('Name suggests duration/elapsed time; the time unit is unverified')
        confidence=0.72
    elif is_numeric and (_TIME.search(name) or _SEQ.search(name)):
        kind='sequence_order_candidate'
        confidence=0.62
        evidence.append('Numeric values with a time/order-like column name are not calendar timestamps')
        low=stats.get('min');high=stats.get('max');distinct=stats.get('distinct_count')
        n=int(row_count or stats.get('rows_scanned') or 0)
        if n>1 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in (low,high,distinct)):
            if (low in (0,1) and int(high-low+1)==n and int(distinct)==n):
                evidence.append('Range and distinct count match a consecutive integer sequence; ordering not independently verified')
                confidence=0.90
            elif float(low)>1_000_000_000 and float(high)<4_000_000_000_000:
                kind='epoch_candidate';confidence=0.58
                evidence.append('Value range might be epoch seconds/milliseconds; unit and origin require validation')
    elif _SEQ.search(name):
        kind='sequence_order_candidate';confidence=0.60
        evidence.append('Column name suggests row order, but ordering must be checked')
    else:
        kind='not_time';confidence=0.15
        evidence.append('No sufficient evidence of a time role')
    confirmed = (confirmation=='confirmed' and
                 ((kind=='calendar_time' and role in ('event_time','available_at','prediction_time'))
                  or (kind=='sequence_order_candidate' and role=='order_index')
                  or (kind=='elapsed_time_candidate' and role=='elapsed_time')))
    return {'field_id':field.field_id,'name':name,'kind':kind,'confidence':confidence,
            'evidence':evidence,'confirmed':confirmed,'requires_user_confirmation':not confirmed,
            'supports_row_windows':kind in ('sequence_order_candidate','elapsed_time_candidate') or confirmed,
            'supports_duration_windows':confirmed and kind=='calendar_time',  # numeric elapsed time has no datetime window compiler
            'supports_pit':confirmed and kind=='calendar_time',
            'recommended_role':('order_index' if kind=='sequence_order_candidate' else
                                'elapsed_time' if kind=='elapsed_time_candidate' else
                                'event_time' if kind=='calendar_time' else 'unspecified')}


def infer_time_fields(contract, profile=None):
    stats={item.get('field_id'):item for item in (profile or {}).get('fields',[])}
    count=(profile or {}).get('rows_scanned')
    results=[infer_time_basis(f,stats.get(f.field_id),count) for f in contract.fields]
    return sorted((r for r in results if r['kind']!='not_time'),
                  key=lambda r: (-r['confidence'],r['name']))
