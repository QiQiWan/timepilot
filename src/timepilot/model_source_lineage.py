"""v0.40.2 signed selected-input -> materialized-observation lineage.

A selected input row is an immutable row in the approved, version-pinned BI
selection, not necessarily a physically identifiable row of the original file.
This distinction is deliberately exposed by the API. Aggregation creates
many-to-one edges. No raw values or arbitrary paths are returned to callers.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .model_temporal_evidence import indices
from .model_task_spec import canonical_sha
from .modeling_adapters import parse_timestamp
from .storage import atomic_write_bytes, sha256_file

SCHEMA = 'model-selected-row-lineage-v1'
FILENAME = 'selected_row_lineage_v1.json.gz'
MAX_ROWS = 200000


def _text(value):
    return None if pd.isna(value) else str(value)


def build(selected, normalized, mapping, plan, *, scope):
    """Build a deterministic map; require full, one-to-one input consumption.

    The normalized frame MUST be the reloaded immutable snapshot, so the
    fingerprints agree with the actual training reader after float formatting.
    """
    if not 0 < len(selected) <= MAX_ROWS or not 0 < len(normalized) <= MAX_ROWS:
        raise ValueError('Lineage rows are empty or over the materialization limit')
    if not all(scope.get(k) for k in ('asset_id','version_id','selection_hash','selected_input_sha256')):
        raise ValueError('Lineage scope lacks immutable source identity')
    timestamps=parse_timestamp(selected[mapping.time],mapping.timezone)
    entities=(selected[mapping.entity].astype('string') if mapping.entity else
              pd.Series('series_1',index=selected.index,dtype='string'))
    if timestamps.isna().any() or entities.isna().any():
        raise ValueError('Cannot identify input source rows')
    bucketed=bool(plan.resampling.enabled)
    if bucketed and plan.frequency not in ('h','D'):
        raise ValueError('Lineage bucket frequency is unsupported')
    groups=defaultdict(list)
    for position,(_,record) in enumerate(selected.iterrows()):
        values={str(k):_text(v) for k,v in record.items()}
        content=canonical_sha(values)
        identity=canonical_sha({'scope':scope,'selected_position':position,'content_sha256':content})
        ts=timestamps.iloc[position]
        if bucketed:
            if plan.frequency=='D':
                ts=ts.tz_convert(mapping.timezone).floor('D').tz_convert('UTC')
            else:ts=ts.floor('h')
        key=(str(entities.iloc[position]),ts.isoformat())
        groups[key].append({'selected_row_position':position,'selected_row_id':identity,
                            'selected_row_sha256':content})
    for row in groups.values():
        row.sort(key=lambda x:x['selected_row_position'])
    normalized_sha=indices(normalized)
    output=[]
    consumed=0
    for pos,record in enumerate(normalized.itertuples(index=False)):
        key=(str(record.series_id),pd.Timestamp(record.timestamp).isoformat())
        inputs=groups.get(key,[])
        if not inputs:
            raise ValueError(f'Missing selected-input lineage for materialized row {pos}')
        if bucketed:
            # One normalized row per source aggregation bucket.
            source_rows=inputs
            groups.pop(key)
        else:
            # Unaggregated classification may have repeated device/time keys;
            # stable sorting preserves their original within-key order.
            source_rows=[inputs.pop(0)]
            if not inputs:groups.pop(key)
        consumed+=len(source_rows)
        output.append({'normalized_row_position':pos,
                       'normalized_row_sha256':normalized_sha[pos],
                       'source_rows':source_rows,
                       'mapping':'complete_bucket_aggregation' if bucketed else 'source_observation'})
    if consumed!=len(selected) or groups:
        raise ValueError('Selected-source lineage does not cover every input exactly once')
    return {'schema':SCHEMA,'scope':scope,
            'row_identity_semantics':'selected_immutable_BI_snapshot_ordinal_and_content',
            'original_file_row_ordinal_certified':False,
            'raw_selected_rows':len(selected),'normalized_rows':len(normalized),
            'resampling':{'enabled':bucketed,'frequency':plan.frequency,
                          'aggregation':plan.resampling.aggregation if bucketed else None},
            'rows':output}


def publish(folder, lineage):
    path=Path(folder)/FILENAME
    payload=json.dumps(lineage,sort_keys=True,separators=(',',':'),ensure_ascii=False,
                       allow_nan=False).encode('utf-8')
    archive=gzip.compress(payload,compresslevel=6,mtime=0)
    atomic_write_bytes(path,archive)
    return sha256_file(path)


def read(folder, *, expected_sha256, expected_scope, expected_rows, source_fingerprint):
    path=Path(folder)/FILENAME
    if (not path.is_file() or not isinstance(expected_sha256,str) or
            sha256_file(path)!=expected_sha256):
        raise ValueError('LINEAGE_SHA256_MISMATCH: immutable lineage file is missing or altered')
    # Decompression bound protects against accidental corruption or gzip bombs.
    with gzip.open(path,'rb') as stream:
        data=stream.read(100_000_001)
    if len(data)>100_000_000:
        raise ValueError('LINEAGE_TOO_LARGE: signed lineage expands beyond 100 MB')
    document=json.loads(data)
    if (document.get('schema')!=SCHEMA or document.get('scope')!=expected_scope or
            document.get('normalized_rows')!=expected_rows or
            len(document.get('rows',[]))!=expected_rows):
        raise ValueError('LINEAGE_SCOPE_MISMATCH: source or normalized snapshot has changed')
    matches=[row for row in document['rows'] if row.get('normalized_row_sha256')==source_fingerprint]
    if len(matches)!=1:
        return {'status':'ambiguous_normalized_observation' if matches else 'unmapped_prediction',
                'matches':len(matches),'lineage_sha256':expected_sha256,
                'source_scope':document['scope']}
    row=matches[0]
    return {'status':'traced_to_selected_snapshot','lineage_sha256':expected_sha256,
            'source_scope':document['scope'],'normalized_row_position':row['normalized_row_position'],
            'normalized_row_sha256':row['normalized_row_sha256'],
            'mapping':row['mapping'],'selected_source_count':len(row['source_rows']),
            'selected_source_rows':row['source_rows'],
            'source_identity_semantics':document['row_identity_semantics'],
            'original_file_row_ordinal_certified':False}


def read_window(folder, *, expected_sha256, expected_scope, expected_rows,
                normalized_frame, history_positions, offset=0, limit=100):
    """Page signed selected source edges for an independently verified fold history.

    `history_positions` come from chronological reconstruction against a
    stage-signed train-index SHA, not from client-controlled row identifiers.
    Every returned link is checked against the same immutable normalized CSV.
    """
    if not isinstance(offset,int) or not 0<=offset<=200000 or not isinstance(limit,int) or not 1<=limit<=100:
        raise ValueError('Invalid bounded history lineage page')
    path=Path(folder)/FILENAME
    if not path.is_file() or sha256_file(path)!=expected_sha256:
        raise ValueError('LINEAGE_SHA256_MISMATCH: history source edge archive changed')
    with gzip.open(path,'rb') as stream:
        body=stream.read(100_000_001)
    if len(body)>100_000_000:
        raise ValueError('LINEAGE_TOO_LARGE: history edge map exceeded bounded read')
    doc=json.loads(body)
    if (doc.get('schema')!=SCHEMA or doc.get('scope')!=expected_scope or
        doc.get('normalized_rows')!=expected_rows or len(doc.get('rows',[]))!=expected_rows):
        raise ValueError('LINEAGE_SCOPE_MISMATCH: history reference is not current')
    fingerprints=indices(normalized_frame)
    total=0;links=[]
    for position in history_positions:
        if type(position) is not int or position<0 or position>=expected_rows:
            raise ValueError('HISTORY_ROW_OUT_OF_BOUNDS')
        row=doc['rows'][position]
        if (row['normalized_row_position']!=position or
            row['normalized_row_sha256']!=fingerprints[position]):
            raise ValueError('HISTORY_NORMALIZED_ROW_MISMATCH')
        for source in row['source_rows']:
            if offset<=total<offset+limit:
                links.append({'normalized_row_position':position,
                    'normalized_row_sha256':fingerprints[position],
                    'selected_row_position':source['selected_row_position'],
                    'selected_row_id':source['selected_row_id'],
                    'selected_row_sha256':source['selected_row_sha256']})
            total+=1
    return {'source_edge_total':total,'source_edges':links,
            'next_offset':offset+len(links) if offset+len(links)<total else None,
            'source_page_offset':offset,'source_page_limit':limit,
            'lineage_sha256':expected_sha256}
