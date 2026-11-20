"""v0.40.3 deterministic, version-bound tabular validation -> input-row proof.

The approved, ordered training data are reloaded for every source-row lookup.
A run-signed ordered-rowset digest prevents a native overlay with changed rows
or row order from being silently interpreted as historical source data.
"""
from __future__ import annotations

import hashlib
import json

from .model_task_spec import canonical_sha
from .model_task_store import ModelTaskConflict, ModelTaskError

SCHEMA = 'tabular-selected-row-v1'


def rowset_sha256(frame):
    """Keep the exact pandas CSV serialization used on both read paths."""
    return hashlib.sha256(frame.to_csv(index=False).encode('utf-8')).hexdigest()


def resolve(*, frame, prediction, result, source, spec):
    if (result.get('source_lineage_schema') != SCHEMA or
            not result.get('source_rowset_sha256')):
        return {'status': 'legacy_lineage_not_recorded',
                'reason': 'Historical run has no signed tabular rowset/ordering proof'}
    if (result.get('source_sha256') != source['original_data_sha256'] or
            result.get('data_version') != spec.version_id or
            result.get('native_sha256') != spec.asset.native_sha256):
        raise ModelTaskConflict('Tabular run/source identities differ')
    if (len(frame) != result.get('rows') or
            rowset_sha256(frame) != result['source_rowset_sha256']):
        raise ModelTaskConflict('Tabular training source rows or row ordering changed')
    try:
        pos = int(prediction['source_row_index'])
    except (ValueError, TypeError, KeyError) as exc:
        raise ModelTaskError('Prediction source row position invalid') from exc
    if (pos < 0 or pos >= len(frame) or
            canonical_sha({'source_sha256': source['original_data_sha256'],
                           'row_position': pos}) != prediction['source_row_sha256']):
        raise ModelTaskError('Signed prediction does not match ordered tabular source row')
    record = frame.iloc[pos]
    values = {str(k): None if str(v) in ('nan', '<NA>', 'NaT') else str(v)
              for k, v in record.items()}
    content_sha = canonical_sha(values)
    identity = canonical_sha({'schema': SCHEMA, 'asset_id': spec.asset_id,
        'version_id': spec.version_id, 'source_sha256': source['original_data_sha256'],
        'row_position': pos, 'content_sha256': content_sha})
    return {
        'status': 'traced_to_selected_snapshot', 'mapping': 'tabular_frozen_ordered_source_row',
        'source_lineage_schema': SCHEMA, 'source_rowset_sha256': result['source_rowset_sha256'],
        'source_identity_semantics': 'approved_version_row_ordinal_and_full_content',
        'original_file_row_ordinal_certified': False,
        'selected_source_count': 1,
        'selected_source_rows': [{'selected_row_position': pos, 'selected_row_id': identity,
                                  'selected_row_sha256': content_sha}],
        'source_page': {'offset': 0, 'limit': 1, 'total': 1,
                        'items': [{'selected_row_position': pos, 'selected_row_id': identity,
                                   'values': values}], 'next_offset': None},
        'source_version_id': spec.version_id, 'asset_id': spec.asset_id,
    }
