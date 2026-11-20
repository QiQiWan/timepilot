"""Evidence-backed temporal topology diagnostics, including numeric record order.

The order key and entity tuple are validated against the *entire* source.
Import row position is only an audit field and never resolves duplicate keys.
"""
from __future__ import annotations
from timepilot.native_bi import quote_identifier
from timepilot.feature_contracts import digest


def temporal_diagnostics(con, contract, view='__tp_effective'):
    from timepilot.feature_topology import infer_topology
    topo=infer_topology(contract)
    if not topo['order_field_id']:
        return {'status':'needs_review','scope':'full',
                'reason':'; '.join(topo['reasons']),'safe_for_windows':False}
    by_id={f.field_id:f for f in contract.fields}
    field=by_id[topo['order_field_id']]
    order=quote_identifier(field.source_name)
    parts=[quote_identifier(by_id[fid].source_name) for fid in topo['partition_field_ids']]
    groups=', '.join(parts)
    clause='PARTITION BY '+groups+' ' if parts else ''
    calendar=field.semantic_type=='datetime'
    order_window=f'{clause}ORDER BY {order}, "__tp_rowid"'
    source_window=f'{clause}ORDER BY "__tp_rowid"'
    rows=f'''SELECT {order} AS order_value,
      LAG({order}) OVER ({order_window}) AS previous_order,
      LAG({order}) OVER ({source_window}) AS previous_source_order
      FROM {view}'''
    data=con.execute(f'''WITH t AS ({rows}) SELECT COUNT(*),
      COUNT(*) FILTER (WHERE order_value IS NULL),
      COUNT(*) FILTER (WHERE previous_order IS NOT NULL AND order_value=previous_order),
      COUNT(*) FILTER (WHERE previous_source_order IS NOT NULL AND order_value<previous_source_order)
      FROM t''').fetchone()
    total,missing,duplicates,out_of_order=map(int,data)
    missing_entities=0
    if parts:
        predicate=' OR '.join(part+' IS NULL' for part in parts)
        missing_entities=int(con.execute(f'SELECT COUNT(*) FROM {view} WHERE {predicate}').fetchone()[0])
    entity_count=int(con.execute(f'SELECT COUNT(*) FROM (SELECT DISTINCT {groups} FROM {view})').fetchone()[0]) if parts else None
    min_gap=max_gap=None
    if calendar:
        min_gap,max_gap=con.execute(f'''WITH t AS ({rows}) SELECT
            MIN(date_diff('second',previous_order,order_value)),
            MAX(date_diff('second',previous_order,order_value)) FROM t''').fetchone()
    reasons=list(topo['reasons'])
    if missing:reasons.append('排序键包含空值')
    if duplicates:reasons.append('同一实体出现重复排序键')
    if missing_entities:reasons.append('设备分组字段包含空值')
    return {'status':'verified','scope':'full','record_count':total,
            'order_kind':'calendar_time' if calendar else 'record_sequence',
            'order_field_id':topo['order_field_id'],
            'partition_field_ids':topo['partition_field_ids'],
            'missing_order_rows':missing,'duplicate_entity_order_rows':duplicates,
            'missing_entity_rows':missing_entities,'out_of_order_source_rows':out_of_order,
            'missing_event_time_rows':missing if calendar else None,
            'duplicate_entity_timestamp_rows':duplicates if calendar else None,
            'min_gap_seconds':min_gap,'max_gap_seconds':max_gap,
            'entity_count':entity_count,'timezone':contract.timezone if calendar else None,
            'safe_for_windows':not reasons,'reasons':reasons,
            'evidence_sha256':digest({'revision':contract.revision,'order':topo['order_field_id'],
              'partitions':topo['partition_field_ids'],'total':total,
              'missing':missing,'duplicates':duplicates,'missing_entity':missing_entities})}
