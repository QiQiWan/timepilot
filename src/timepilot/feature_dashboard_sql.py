"""Shared, audited query templates for the two FeatureLab chart APIs.

Only trusted source-view names are used; column identifiers must have been
resolved from the authorized contract before calling this module. Untrusted
filters are parameterized by the caller and never interpolated into SQL.
"""
from __future__ import annotations

from timepilot.native_bi import quote_identifier

CATEGORY_TEMPLATE_ID = 'tp.category.counts.v2'


def category_sql(column_name: str, *, base: str, limit: int) -> str:
    """Return a common, DuckDB-safe aggregation template for both chart APIs.

    base is a server-generated ``FROM ... WHERE ...`` clause; ``limit`` is
    validated by the caller. The ``label`` identifier is intentionally avoided:
    DuckDB reserves it in some supported versions.
    """
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError('类别数量必须位于 1–50 之间')
    if not base.strip().startswith('FROM __tp_effective WHERE '):
        raise ValueError('只允许使用授权的版本数据视图')
    col = quote_identifier(column_name)
    return (f'SELECT CAST({col} AS VARCHAR) AS category_value, '
            f'COUNT(*) AS category_count {base} AND {col} IS NOT NULL '
            f'GROUP BY 1 ORDER BY category_count DESC, category_value LIMIT {limit}')
