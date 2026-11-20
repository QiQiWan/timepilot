"""Opt-in, provenance-bound industrial reset features (v0.35.2).

A reset event starts a new calculation epoch. A subsequent regime transition
starts another epoch, even if the regime later returns to its old value.  No
feature is emitted before the first observed, confirmed maintenance reset.
These are observation-count aggregates, not a claim of time integration or
physical causality.  SQL identifiers always come from the confirmed contract.
"""
from __future__ import annotations

import re
from timepilot.native_bi import quote_identifier
from timepilot.feature_compiler import NUMERIC

# Units here indicate the interpretation of a signal, not an implicit
# conversion. Unknown units fail closed for mechanism-aware operations.
_UNIT_DIMENSIONS = {
    'Pa':'pressure','kPa':'pressure','MPa':'pressure','bar':'pressure',
    'm':'length','cm':'length','mm':'length','um':'length',
    'm/s':'velocity','mm/s':'velocity','m/s2':'acceleration',
    's':'duration','min':'duration','h':'duration',
    'K':'temperature','°C':'temperature','Celsius':'temperature',
    'kg':'mass','g':'mass','N':'force','kN':'force',
    'W':'power','kW':'power','Wh':'energy','kWh':'energy',
    'V':'voltage','A':'current','Hz':'frequency','rpm':'rotational_speed',
    'm3':'volume','L':'volume','pcs':'count','count':'count','件':'count','个':'count',
    '%':'ratio','1':'ratio',
}
# Cumulative sums are only admissible for recorded *increments* whose
# addition can be meaningful. A temperature, pressure or instantaneous power
# series is not an amount; summing it is a sampling-rate artifact.
_ADDITIVE_DIMENSIONS = {'length','mass','energy','volume','count','duration'}
_RESET_OPERATORS = {'industrial.reset_mean','industrial.reset_sum'}


def unit_dimension(unit: str | None) -> str | None:
    return _UNIT_DIMENSIONS.get(unit or '')


def validate_mechanism(contract, feature):
    """Return trusted source fields; do not synthesize missing field roles."""
    if feature.operator_id not in _RESET_OPERATORS:
        raise ValueError('未知工业重置特征')
    if contract.state != 'confirmed' or not contract.timezone or not contract.event_time_field_id:
        raise ValueError('工业维护特征必须先确认真实时间、时区和字段契约')
    by_id = {field.field_id: field for field in contract.fields}
    event_time = by_id[contract.event_time_field_id]
    if event_time.confirmation != 'confirmed' or event_time.semantic_type != 'datetime' or not any(
        t in event_time.physical_type.upper() for t in ('TIMESTAMP','DATE')
    ):
        raise ValueError('工业特征要求已确认的真实事件时间字段')
    if not contract.entity_field_ids:
        raise ValueError('工业维护特征要求已确认的设备或批次标识')
    for fid in contract.entity_field_ids:
        entity = by_id[fid]
        if entity.confirmation != 'confirmed' or entity.role not in ('entity_key','batch_key'):
            raise ValueError('工业计算的实体键必须完成确认')
    fid=feature.input_field_ids[0]
    source=by_id.get(fid)
    if (source is None or source.confirmation!='confirmed' or source.role!='value'
        or source.semantic_type!='numeric' or source.classification=='restricted'
        or not NUMERIC.match(source.physical_type)):
        raise ValueError('工业累计输入必须是已确认且非受限的数值过程字段')
    dimension=unit_dimension(source.unit)
    if dimension is None:
        raise ValueError('工业特征要求明确可识别的工程单位，不支持无单位或未知单位')
    p=feature.parameters
    if p.get('order_field_id') != contract.event_time_field_id:
        raise ValueError('工业重置窗口只能按真实事件时间排序')
    partitions=p.get('partition_field_ids', contract.entity_field_ids)
    if not isinstance(partitions,list) or set(partitions)!=set(contract.entity_field_ids):
        raise ValueError('工业累计必须只按已确认的设备/批次边界计算')
    reset=by_id.get(p.get('reset_field_id'))
    if (reset is None or reset.confirmation!='confirmed' or reset.classification=='restricted'
        or reset.semantic_type!='event'
        or reset.physical_type.upper()!='BOOLEAN'
        or reset.role not in ('unspecified','value')):
        raise ValueError('重置标记必须是已确认的非受限 BOOLEAN 维护事件字段，不能用普通布尔值代替')
    regime=None
    if p.get('regime_field_id') is not None:
        regime=by_id.get(p['regime_field_id'])
        if (regime is None or regime.role!='regime' or regime.semantic_type!='category'
            or regime.confirmation!='confirmed' or regime.classification=='restricted'):
            raise ValueError('工况切换必须引用已确认、非受限的工况类别字段')
    reference=p.get('mechanism_reference')
    approved_references=[str(item).strip() for item in reset.evidence if str(item).strip()]
    if not approved_references:
        raise ValueError('维护事件缺少已确认的数据契约证据，禁止推断重置规则')
    if not isinstance(reference,str) or not 8<=len(reference.strip())<=240 or '\x00' in reference:
        raise ValueError('必须记录机理/维护重置规则的人工确认来源（8—240字）')
    if reference.strip() not in approved_references and reference.strip()!='; '.join(approved_references)[:240]:
        raise ValueError('机理依据与已确认的维护字段证据不一致，请先更新字段契约')
    if feature.operator_id=='industrial.reset_sum':
        if p.get('incremental_confirmed') is not True:
            raise ValueError('累计求和要求人工确认每条观测是可相加的增量值')
        if dimension not in _ADDITIVE_DIMENSIONS:
            raise ValueError('压力、温度、功率等瞬时读数不允许直接求累计和')
    return {'source':source,'reset':reset,'regime':regime,'event_time':event_time,
            'unit':source.unit,'dimension':dimension,'reference':reference.strip(),
            'entity_ids':list(contract.entity_field_ids)}


def eligible_reset_features(contract):
    """Suggest *mean* only when metadata already contains human provenance.

    Never infer a maintenance rule or the meaning of cumulative increments.
    """
    if contract.state!='confirmed' or not contract.event_time_field_id or not contract.timezone or not contract.entity_field_ids:
        return []
    by_id={f.field_id:f for f in contract.fields}
    events=[f for f in contract.fields if f.physical_type.upper()=='BOOLEAN'
            and f.confirmation=='confirmed' and f.semantic_type=='event'
            and f.role in ('unspecified','value') and f.classification!='restricted'
            and any(str(item).strip() for item in f.evidence)]
    regimes=[f for f in contract.fields if f.role=='regime' and f.semantic_type=='category'
             and f.confirmation=='confirmed' and f.classification!='restricted']
    values=[f for f in contract.fields if f.role=='value' and f.semantic_type=='numeric'
            and f.confirmation=='confirmed' and f.classification!='restricted'
            and NUMERIC.match(f.physical_type) and unit_dimension(f.unit)]
    result=[]
    for value in values:
        for event in events:
            params={'reset_field_id':event.field_id,
                    'mechanism_reference':'; '.join(str(x) for x in event.evidence)[:240],
                    'order_field_id':contract.event_time_field_id,
                    'partition_field_ids':list(contract.entity_field_ids)}
            if regimes:params['regime_field_id']=regimes[0].field_id
            result.append((value.field_id,params))
    return result


def create_reset_views(con, table, feature, contract, index):
    """Create epoch views without nested window expressions.

    The first view supplies the previous *adjacent* regime, rather than
    PARTITION BY regime which incorrectly merges noncontiguous A-B-A periods.
    The second computes the monotone epoch and whether any maintenance reset
    has ever been observed for that entity.
    """
    info=validate_mechanism(contract,feature)
    by_id={f.field_id:f for f in contract.fields}
    quote=lambda fid: quote_identifier(by_id[fid].source_name)
    group=', '.join(quote(fid) for fid in info['entity_ids'])
    order=quote(contract.event_time_field_id)
    partition=('PARTITION BY '+group+' ' if group else '')+'ORDER BY '+order+' ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW'
    reset_col=quote(info['reset'].field_id)
    null_keys=[reset_col]
    if info['regime'] is not None:null_keys.append(quote(info['regime'].field_id))
    missing=con.execute('SELECT COUNT(*) FROM '+table+' WHERE '+' OR '.join(x+' IS NULL' for x in null_keys)).fetchone()[0]
    if missing:raise ValueError(f'工业维护/工况字段有 {missing} 条空值，无法确认重置时机')
    prev_view=table
    regime_condition=''
    if info['regime'] is not None:
        prev_col=f'__tp_ind_prev_regime_{index}'
        prev_view=f'__tp_ind_regime_{index}'
        regime=quote(info['regime'].field_id)
        prev_win=('PARTITION BY '+group+' ' if group else '')+'ORDER BY '+order
        con.execute(f'CREATE TEMP VIEW {prev_view} AS SELECT *, '
                    f'LAG({regime}) OVER ({prev_win}) AS {quote_identifier(prev_col)} FROM {table}')
        regime_condition=f' OR ({quote_identifier(prev_col)} IS NOT NULL AND '
        regime_condition+=f'{regime} IS DISTINCT FROM {quote_identifier(prev_col)})'
    cycle_col=f'__tp_ind_cycle_{index}'
    seen_col=f'__tp_ind_seen_{index}'
    next_view=f'__tp_ind_epoch_{index}'
    changes=f'(CASE WHEN {reset_col} IS TRUE{regime_condition} THEN 1 ELSE 0 END)'
    con.execute(f'CREATE TEMP VIEW {next_view} AS SELECT *, '
                f'SUM({changes}) OVER ({partition}) AS {quote_identifier(cycle_col)}, '
                f'SUM(CASE WHEN {reset_col} IS TRUE THEN 1 ELSE 0 END) '
                f'OVER ({partition}) AS {quote_identifier(seen_col)} FROM {prev_view}')
    return next_view, cycle_col, seen_col


def compile_reset_expression(source, operator_id, window, seen_col):
    agg='AVG' if operator_id=='industrial.reset_mean' else 'SUM'
    return (f'(CASE WHEN {quote_identifier(seen_col)}>0 THEN '
            f'{agg}({source}) {window} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) '
            'ELSE NULL END)')
