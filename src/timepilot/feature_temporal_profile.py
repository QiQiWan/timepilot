"""Evidence-backed industrial entity/time coverage diagnostics.

All SQL identifiers come from the project-scoped immutable FieldContract and
are escaped via Native BI quoting. Values are not interpreted as timestamps
until a confirmed datetime field and timezone exist.
"""
from __future__ import annotations
from timepilot.native_bi import quote_identifier
from timepilot.feature_contracts import digest


def temporal_diagnostics(con, contract, view='__tp_effective'):
    if contract.event_time_field_id is None:
        return {'status': 'needs_review', 'reason': '没有已确认真实事件时间，仅支持记录顺序分析'}
    by_id = {f.field_id:f for f in contract.fields}
    field = by_id[contract.event_time_field_id]
    if field.semantic_type != 'datetime' or field.confirmation != 'confirmed' or not contract.timezone:
        return {'status': 'needs_review','reason': '真实时间字段或时区尚未确认'}
    time = quote_identifier(field.source_name)
    partition = [quote_identifier(by_id[x].source_name) for x in contract.entity_field_ids]
    clauses = ', '.join(partition)
    entity = f'PARTITION BY {clauses} ' if clauses else ''
    ordering = f'{entity}ORDER BY {time}, "__tp_rowid"'
    source_ordering = f'{entity}ORDER BY "__tp_rowid"'
    query = f'''WITH t AS (SELECT {time} AS event_time,
       LAG({time}) OVER ({ordering}) AS prev_time,
       LAG({time}) OVER ({source_ordering}) AS previous_source_time
       FROM {view})
       SELECT COUNT(*) AS record_count,
       COUNT(*) FILTER (WHERE event_time IS NULL) AS time_missing,
       COUNT(*) FILTER (WHERE prev_time IS NOT NULL AND event_time=prev_time) AS duplicate_time,
       COUNT(*) FILTER (WHERE previous_source_time IS NOT NULL AND event_time<previous_source_time) AS backwards_time,
       MIN(date_diff('second',prev_time,event_time)) FILTER (WHERE prev_time IS NOT NULL) AS min_gap_seconds,
       MAX(date_diff('second',prev_time,event_time)) FILTER (WHERE prev_time IS NOT NULL) AS max_gap_seconds
       FROM t'''
    r=con.execute(query).fetchone()
    records,missing,duplicate,backwards,mn,mx=r
    entity_count=None
    if partition:
        entity_count=int(con.execute(f'SELECT COUNT(*) FROM (SELECT DISTINCT {clauses} FROM {view})').fetchone()[0])
    return {'status':'verified','scope':'full','record_count':int(records),
      'missing_event_time_rows':int(missing),'duplicate_entity_timestamp_rows':int(duplicate),
      'out_of_order_source_rows':int(backwards),'min_gap_seconds':mn,
      'max_gap_seconds':mx,'entity_count':entity_count,'timezone':contract.timezone,
      'evidence_sha256':digest({'contract_revision':contract.revision,'event_field_id':contract.event_time_field_id,
                                'entity_field_ids':contract.entity_field_ids,'records':records,'missing':missing,
                                'duplicates':duplicate,'min_gap_seconds':mn,'max_gap_seconds':mx})}
