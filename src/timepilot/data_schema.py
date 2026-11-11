"""Shared, evidence-based field profiling for arbitrary tabular data.

Column names are copied from source; this module never invents business meaning.
Inference is deliberately conservative: data types are observed properties, while
roles are proposals that require confirmation before model preparation.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

UNKNOWN_HEADER = re.compile(r'^(?:[A-Za-z]\d+|unnamed_\d+(?:__\d+)?|column[_ ]?\d+)$', re.I)
TIME_NAME = re.compile(r'(?:^time$|^date$|timestamp|datetime|date[_ ]?time|(?:采集|记录|测量|检测|监测)?时间|日期|时刻|时间戳)', re.I)
ID_NAME = re.compile(r'(?:^id$|[_ ]id$|^id[_ ]|编号|编码|序列号|批次号|设备号|物料号|条码|工单号|sku|sn$)', re.I)
EVENT_NAME = re.compile(r'(?:事件|event|维护|维修|换刀|保养|清洗|故障|告警|状态|status|报警)', re.I)
UNIT = re.compile(r'[\[(（]([^\])）]{1,25})[\])）]\s*$')


def _missing(x: Any) -> bool:
    return x is None or isinstance(x, str) and not x.strip()


def _is_num(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    try:
        num = Decimal(str(x).strip().replace(',', ''))
        return num.is_finite()
    except (InvalidOperation, ValueError, TypeError):
        return False


def _is_date(x: Any) -> bool:
    if isinstance(x, (date, datetime)):
        return True
    if not isinstance(x, str):
        return False
    v = x.strip()
    if not (re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', v) or
            re.match(r'^\d{4}-\d{2}-\d{2}T', v)):
        return False
    try:
        datetime.fromisoformat(v.replace('/', '-').replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def infer_field(name: str, values: list[Any], *, source_header_row: int | None = None) -> dict:
    """Return data-derived type and a *non-authoritative* semantic role suggestion."""
    observed = [x for x in values if not _missing(x)]
    n = len(observed)
    num = sum(_is_num(x) for x in observed)
    temporal = sum(_is_date(x) for x in observed)
    bools = sum(isinstance(x, bool) for x in observed)
    if not n:
        physical = 'unknown'
    elif bools == n:
        physical = 'boolean'
    elif num / n >= .95:
        physical = 'number'
    elif temporal / n >= .90:
        physical = 'datetime'
    else:
        physical = 'text'
    column_is_opaque = bool(UNKNOWN_HEADER.fullmatch(name.strip()))
    evidence = [f'抽样 {len(values)} 行，非空 {n} 项']
    confidence = 'low'
    role = 'unknown'
    if column_is_opaque:
        evidence.append('字段名称为代码或未命名列，无法确定业务含义')
    if not column_is_opaque and TIME_NAME.search(name) and (temporal >= 1 or physical in {'number','datetime','unknown'}):
        role = 'time'; confidence = 'high' if n and temporal / n >= .9 else 'review'
        evidence.append('数值时间轴需要核实单位与起点' if physical == 'number' else '字段名包含时间信息，样本日期格式需验证')
    elif not column_is_opaque and EVENT_NAME.search(name):
        role = 'event'; confidence = 'review'
        evidence.append('字段名称包含事件或设备状态词')
    elif not column_is_opaque and ID_NAME.search(name):
        role = 'identifier'; confidence = 'review'
        evidence.append('字段名符合标识字段命名习惯')
    elif physical == 'number':
        role = 'measure'; confidence = 'review'
        evidence.append('数值型指标候选：仅凭数据类型无法确定实际业务意义或单位')
    elif physical == 'boolean' and not column_is_opaque:
        role = 'flag'; confidence = 'review'
    elif physical == 'datetime':
        role = 'time'; confidence = 'review'
        evidence.append('样本为可识别的日期或时间值')
    elif physical == 'text' and not column_is_opaque:
        role = 'category' if len({str(x) for x in observed}) <= max(12, n // 2) else 'text'
        confidence = 'review'
    unit_match = UNIT.search(name)
    return {
        'name': name, 'physical_type': physical, 'suggested_role': role,
        'confidence': confidence, 'source_header_row': source_header_row,
        'unit_hint': unit_match.group(1) if unit_match else None,
        'source_label_verified': source_header_row is not None,
        'business_meaning_confirmed': False,
        'metric_candidate': role == 'measure',
        'evidence': evidence,
    }


def summarize_schema(columns: list[str], sample_rows: list[list[Any]], *, header_row: int | None) -> list[dict]:
    return [infer_field(col, [row[i] if i < len(row) else None for row in sample_rows],
                        source_header_row=header_row) for i, col in enumerate(columns)]
