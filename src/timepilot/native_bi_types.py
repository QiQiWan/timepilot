"""Conservative, auditable CSV/Excel schema inference for the native BI importer.

Inference inspects a bounded sample, but every non-text cast is *also* checked
against the full file by DuckDB before a typed Parquet snapshot is accepted.
Identifiers with leading zeroes and code-like field names stay VARCHAR.
"""
from __future__ import annotations

import re
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUM = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_ID_NAME = re.compile(r"(?:^id$|(?:^|_)(?:id|sku|code|key|no|serial)(?:$|_)|编号|编码|代码|条码|工号|料号|邮编|车型)", re.I)
_DATE_NAME = re.compile(r"(?:date|timestamp|datetime|event_time|created_at|日期|时间|时刻)", re.I)
_VALID_TYPES = frozenset({'VARCHAR', 'BIGINT', 'DOUBLE', 'DATE', 'TIMESTAMP', 'BOOLEAN'})


def valid_type(name: str) -> str:
    name = str(name).strip().upper()
    if name not in _VALID_TYPES:
        raise ValueError(f'不支持的字段类型：{name}')
    return name


def _iso(value: str) -> str | None:
    if not ("-" in value or "T" in value or "/" in value):
        return None
    try:
        d = date.fromisoformat(value)
        if d.isoformat() == value:
            return 'DATE'
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.time().isoformat() or 'T' in value or ' ' in value:
            return 'TIMESTAMP'
    except ValueError:
        return None
    return None


def suggest_column_type(name: str, sample: list[Any]) -> dict:
    """No aggressive coercion: every *nonempty* sample value must match."""
    values = [str(value).strip() for value in sample if value is not None and str(value).strip()]
    if not values:
        return {'physical_type': 'VARCHAR', 'semantic_type': 'empty', 'reason': '样本无有效值'}
    if _ID_NAME.search(name):
        return {'physical_type': 'VARCHAR', 'semantic_type': 'identifier', 'reason': '字段名包含业务编号语义'}
    if any(re.match(r'^[-+]?0\d+', value) for value in values):
        return {'physical_type': 'VARCHAR', 'semantic_type': 'identifier', 'reason': '存在带前导零的值'}
    if all(_INTEGER.fullmatch(value) for value in values):
        # A numeric identifier may silently lose precision if cast to float.
        try:
            if all(-(2**63) <= int(value) < 2**63 for value in values):
                return {'physical_type': 'BIGINT', 'semantic_type': 'numeric', 'reason': '全部样本为整数'}
            return {'physical_type': 'VARCHAR', 'semantic_type': 'identifier',
                    'reason': '整数超出 BIGINT 精确范围，保留原文防止精度丢失'}
        except ValueError:
            pass
    if all(_NUM.fullmatch(value) for value in values):
        try:
            numeric = [Decimal(value) for value in values]
            if all(d.is_finite() and math.isfinite(float(d)) and
                   (d.is_zero() or float(d) != 0) for d in numeric):
                return {'physical_type': 'DOUBLE', 'semantic_type': 'numeric', 'reason': '全部样本为数值'}
        except (InvalidOperation, OverflowError, ValueError):
            pass
    if _DATE_NAME.search(name):
        detected = [_iso(value) for value in values]
        if detected and all(kind == 'DATE' for kind in detected):
            return {'physical_type': 'DATE', 'semantic_type': 'time', 'reason': '时间字段使用标准日期格式'}
        if detected and all(kind in {'DATE', 'TIMESTAMP'} for kind in detected):
            return {'physical_type': 'TIMESTAMP', 'semantic_type': 'time', 'reason': '时间字段使用标准时间戳格式'}
    return {'physical_type': 'VARCHAR', 'semantic_type': 'category', 'reason': '保留原始文本，避免不安全类型推断'}


def infer_text_schema(columns: list[str], rows: list[tuple[Any, ...]]) -> dict[str, dict]:
    return {name: suggest_column_type(name, [row[i] for row in rows])
            for i, name in enumerate(columns)}
