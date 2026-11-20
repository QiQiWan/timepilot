"""Safe automatic feature topology: only confirmed temporal and entity roles.

Unconfirmed identifiers and ambiguous keys block automatic rolling statistics.
The profiler and compiler independently verify uniqueness on the actual source.
"""
from __future__ import annotations
import re
from timepilot.feature_compiler import NUMERIC

_ENTITY_NAME = re.compile(r'(^|[_\s-])(device|machine|equipment|asset|unit|sensor|batch|lot|entity|line|station|工位|设备|产线|批次)(?:id|sn|code|number|no|编号|编码)?(?=[_\s-]|$)', re.I)
_ORDER_NAME = re.compile(r'(^|[_\s-])(time|date|timestamp|seq|sequence|step|sample|order|index|时间|日期|序号)([_\s-]|$)', re.I)


def infer_topology(contract, diagnostics=None):
    """Conservative topology decision. A guessed time/ID must not enable windows.

    'safe_for_windows' is True only after *full-source* key validation. The
    decision is not dependent on import row ordering.
    """
    by_id={f.field_id:f for f in contract.fields}
    entries=[f for f in contract.fields if f.role=='order_index' and f.confirmation=='confirmed']
    time=by_id.get(contract.event_time_field_id) if contract.event_time_field_id else None
    if time and (time.confirmation!='confirmed' or time.semantic_type!='datetime' or not contract.timezone):
        time=None
    reasons=[]
    order=None
    if time:
        order=time
    elif len(entries)==1 and NUMERIC.match(entries[0].physical_type):
        order=entries[0]
    elif len(entries)>1:
        reasons.append('存在多个已确认的记录顺序字段，需要明确唯一排序键')
    elif entries:
        reasons.append('记录顺序字段须为实际数值类型')
    partition=[]
    for fid in contract.entity_field_ids:
        field=by_id.get(fid)
        if not field or field.confirmation!='confirmed' or field.role not in ('entity_key','batch_key'):
            reasons.append('设备或批次分组字段需要在数据契约中完成确认')
        else:partition.append(fid)
    # A confirmed entity field not linked to the DataContract is dangerous too.
    for field in contract.fields:
        if field.role in ('entity_key','batch_key') and field.confirmation=='confirmed' and field.field_id not in partition:
            reasons.append('已确认的实体/批次字段尚未加入分组键，不能混合计算')
        if field.field_id not in partition and field.field_id!=(order.field_id if order else None) and (
            field.role in ('entity_key','batch_key') or
            (field.confirmation!='confirmed' and _ENTITY_NAME.search(field.source_name))):
            reasons.append('存在疑似设备或批次键，先确认是否需要按实体分组')
    if not order:
        reasons.append('尚未确认唯一的记录顺序或真实事件时间字段')
    if order and order.role=='order_index' and not NUMERIC.match(order.physical_type):
        reasons.append('记录顺序字段不是数值类型')
    if diagnostics is not None and order:
        if diagnostics.get('status')!='verified':
            reasons.append('排序字段尚未完成全量唯一性检查')
        elif diagnostics.get('order_field_id')!=order.field_id:
            reasons.append('全量诊断使用了不同的排序字段')
        elif int(diagnostics.get('missing_order_rows') or 0)>0:
            reasons.append('排序键存在空值')
        elif int(diagnostics.get('duplicate_entity_order_rows') or 0)>0:
            reasons.append('同一设备内存在重复排序值')
        elif int(diagnostics.get('missing_entity_rows') or 0)>0:
            reasons.append('设备或批次分组键存在空值')
    safety=not reasons and (diagnostics is None or diagnostics.get('status')=='verified')
    return {'kind':('multi_entity_sequence' if partition and order else
                    'single_sequence' if order else 'independent_records'),
            'order_field_id':order.field_id if order else None,
            'partition_field_ids':partition, 'confirmed_order':bool(order),
            'can_propose_windows':not reasons,
            'safe_for_windows':safety and diagnostics is not None,
            'requires_full_validation':bool(order and diagnostics is None),
            'reasons':list(dict.fromkeys(reasons)),
            'source_order_is_authoritative':False}


def needs_row_order(operator_id):
    return (operator_id.startswith('rolling.') or operator_id in
            ('lag.rows','difference.rows','relative.change','event.since','industrial.reset_mean','industrial.reset_sum'))
