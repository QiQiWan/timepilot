"""Evidence-only Chinese chart explanations for FeatureLab (v0.35.3).

Pure functions: all numeric observations come from an already verified DuckDB
chart payload; no language model, fabricated anomaly cause or mutable data.
"""
from __future__ import annotations

import math


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _fmt(value):
    n = _number(value)
    return format(n, ',.3g') if n is not None else '未知'


def _obs(text, code, evidence=()):
    return {'code': code, 'text': text, 'evidence': list(evidence)}


def explain_chart(chart: dict, *, unit: str | None = None, original_unit: str | None = None,
                  axis_compatible: bool | None = None) -> dict:
    """Return traceable observations, scope and axes from a numeric chart payload.

    'observation' indicates a descriptive fact only. Outliers, root causes and
    predictive gains are intentionally never inferred from chart appearance.
    """
    mode = chart.get('mode') or 'distribution'
    rows = chart.get('points') or []
    feature = str(chart.get('feature_name') or chart.get('field_name') or '当前字段')
    population = chart.get('population_count', chart.get('population_rows'))
    n = sum(int(_number(r.get('count')) or 0) for r in rows)
    selected = chart.get('entity_value')
    group = str(selected) if selected is not None else '未分组'
    observations = []
    cautions = []
    axis = {'x': '数值区间', 'y': '记录数', 'x_semantics': 'numeric_bins', 'unit': unit}
    scope = {'records': population if population is not None else n,
             'bins': len(rows), 'entity_label': selected,
             'start': (chart.get('time_range') or {}).get('start'),
             'end': (chart.get('time_range') or {}).get('end')}
    if mode == 'series':
        axis.update(x=str(chart.get('order_field') or '已确认时间/顺序'),
                    y=feature+(f'（{unit}）' if unit else ''),
                    x_semantics='confirmed_event_time')
        if selected is None:
            cautions.append('未按设备隔离；如果包含多台设备，不能将曲线解释为单台设备连续变化。')
        values = [(i, _number(r.get('value'))) for i, r in enumerate(rows)]
        values = [(i, v) for i, v in values if v is not None]
        if values:
            hi = max(values, key=lambda x: x[1])
            lo = min(values, key=lambda x: x[1])
            observations.append(_obs(
                f'当前范围的桶均值约在 {_fmt(lo[1])}—{_fmt(hi[1])} 之间，最高桶位于 {str(rows[hi[0]].get("x"))[:60]}。',
                'observed_bucket_means', [f'bucket_{lo[0]}', f'bucket_{hi[0]}']))
        extremes = [(i, _number(r.get('min')), _number(r.get('max'))) for i, r in enumerate(rows)]
        extremes = [(i,a,b) for i,a,b in extremes if a is not None and b is not None]
        if extremes:
            biggest = max(extremes, key=lambda x: x[2]-x[1])
            observations.append(_obs(
                f'最大桶内极差约 {_fmt(biggest[2]-biggest[1])}，对应 {str(rows[biggest[0]].get("x"))[:60]}；建议查看该区段的原始记录。',
                'largest_within_bucket_range', [f'bucket_{biggest[0]}']))
        if axis_compatible is False:
            cautions.append('原始字段与新特征量纲不同或未知，采用分开展示，不能共享纵轴直接比较幅度。')
        what = (f'横轴是 {axis["x"]}，纵轴为 {feature} 的分桶均值；橙色竖线给出新特征在每个桶内的最小值和最大值。'
                + (f' 已选择设备 {group}。' if selected is not None else ' 当前未指定设备。'))
        action = {'type': 'select_entity' if selected is None else 'inspect_range',
                  'label': '按设备查看' if selected is None else '检查波动区段'}
    elif mode == 'category':
        axis.update(x='类别', x_semantics='categories')
        what = f'横轴是 {feature} 的不同类别，纵轴表示每类的有效记录数；仅显示排序靠前的类别。'
        if rows:
            top = max(enumerate(rows), key=lambda item: _number(item[1].get('count')) or 0)
            observations.append(_obs(f'当前展示中记录数最多的类别为 {str(top[1].get("label"))[:60]}，共 {_fmt(top[1].get("count"))} 条。',
                                     'largest_category', [f'category_{top[0]}']))
        cautions.append('超过展示上限的类别未绘制；图表不等于所有类别的完整清单。')
        action = {'type': 'open_bi', 'label': '查看完整类别'}
    elif mode == 'group':
        axis.update(x='设备或分组', y='分组均值', x_semantics='groups')
        what = f'按已确认的设备或分组统计 {feature}；每根柱为该组的均值，不能直接推断时间走势。'
        action = {'type':'inspect_group','label':'比较设备差异'}
    else:
        what = f'横轴为 {feature} 的数值分箱，纵轴为每个区间的有限值记录数。'
        counts = [(i, _number(r.get('count'))) for i, r in enumerate(rows)]
        counts = [(i, v) for i, v in counts if v is not None]
        if counts:
            peak = max(counts, key=lambda x: x[1])
            observations.append(_obs(
                f'记录最多的区间为 {str(rows[peak[0]].get("label"))[:80]}，约 {_fmt(peak[1])} 条。',
                'modal_bin', [f'bin_{peak[0]}']))
        action={'type':'inspect_distribution','label':'查看字段分布'}
    if mode in ('distribution','series') and not observations:
        observations.append(_obs('当前统计范围内没有足够的有效数值支撑趋势判断。','insufficient_data'))
    return {'schema_version':1, 'chart_id':str(chart.get('candidate_id') or chart.get('field_id') or ''),
            'title':feature, 'what_is_shown':what,
            'scope':scope, 'axes':axis,
            'observations':observations[:2], 'cautions':cautions,
            'recommended_action':action,
            'evidence_refs':[str(v) for v in (chart.get('source_sha256'), chart.get('candidate_fingerprint')) if v],
            'original_unit':original_unit, 'axis_compatible':axis_compatible}


# These transformations preserve the physical dimension of the source signal.
_SAME_UNIT = frozenset({'lag.rows', 'difference.rows', 'rolling.mean', 'rolling.median',
                        'rolling.p90', 'rolling.std', 'rolling.min', 'rolling.max',
                        'rolling.range', 'rolling.time_mean', 'rolling.time_min',
                        'rolling.time_max', 'industrial.reset_mean'})
_DIMENSIONLESS = frozenset({'relative.change', 'rolling.zscore', 'rolling.cv',
                            'category.is_value'})


def feature_unit(operator_id: str, input_unit: str | None):
    """Conservative unit inference; unknown transformations are never overlaid."""
    if operator_id in _SAME_UNIT:
        return input_unit, True
    if operator_id in _DIMENSIONLESS:
        return '1', input_unit in (None, '', '1')
    if operator_id == 'transform.abs':
        return input_unit, True
    return None, False
