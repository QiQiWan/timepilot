"""Validated preview analytics from a fully computed DuckDB feature view.

All SQL identifiers originate in a checked FeatureSpec; caller supplies a trusted
compiler-created relation. Sample rows and complete-scope aggregates have
separate provenance and MUST NOT be presented as predictive importance.
"""
from __future__ import annotations

from timepilot.native_bi import quote_identifier, safe_json


def summarize_preview(con, table: str, columns: list[str], *, sample_count: int) -> list[dict]:
    if not columns:
        return []
    expressions = ['COUNT(*) AS "__total"']
    for i, name in enumerate(columns):
        col = f'TRY_CAST({quote_identifier(name)} AS DOUBLE)'
        # DuckDB accepts NaN and +/-Infinity: exclude them from finite summary.
        finite = f'(CASE WHEN ISFINITE({col}) THEN {col} ELSE NULL END)'
        expressions.extend([
            f'COUNT({finite}) AS "v{i}_valid"',
            f'MIN({finite}) AS "v{i}_min"',
            f'MAX({finite}) AS "v{i}_max"',
            f'AVG({finite}) AS "v{i}_avg"',
            f'STDDEV_SAMP({finite}) AS "v{i}_std"',
            f'APPROX_QUANTILE({finite}, 0.1) AS "v{i}_p10"',
            f'APPROX_QUANTILE({finite}, 0.5) AS "v{i}_p50"',
            f'APPROX_QUANTILE({finite}, 0.9) AS "v{i}_p90"',
        ])
    statement=f'SELECT {", ".join(expressions)} FROM {table}'
    cur=con.execute(statement)
    keys=[entry[0] for entry in cur.description]
    result=dict(zip(keys,safe_json(cur.fetchone())))
    total=int(result['__total'])
    output=[]
    for i,name in enumerate(columns):
        valid=int(result[f'v{i}_valid'])
        low=result[f'v{i}_min'];high=result[f'v{i}_max']
        output.append({
            'feature_name':name,
            'scope':'full_source_up_to_preview_scan_budget',
            'rows_scanned':total,'sample_rows':sample_count,
            'finite_count':valid,'missing_or_nonfinite_count':total-valid,
            'null_rate':round((total-valid)/total, 6) if total else None,
            'constant_on_full_data':valid==0 or low==high,
            'min':low,'max':high,'mean':result[f'v{i}_avg'],
            'std':result[f'v{i}_std'], 'p10':result[f'v{i}_p10'],
            'median_approx':result[f'v{i}_p50'], 'p90':result[f'v{i}_p90'],
            'predictive_value_evaluated':False,
        })
    return output
