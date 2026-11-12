"""FeatureLab constrained operators and machine-readable applicability rules."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Operator:
    operator_id: str
    version: str
    title: str
    family: str
    input_count: int
    type: str
    time_required: bool = False
    example: str = ''


OPERATORS = {op.operator_id: op for op in (
    Operator('arithmetic.binary','2.0','两列计算','basic',2,'numeric',example='A8 + A10'),
    Operator('lag.rows','2.0','历史滞后','temporal',1,'numeric'),
    Operator('difference.rows','2.0','历史差值','temporal',1,'numeric'),
    Operator('rolling.mean','2.0','移动平均','temporal',1,'numeric'),
    Operator('rolling.std','2.0','滚动标准差','temporal',1,'numeric'),
    Operator('rolling.min','2.0','窗口最小值','temporal',1,'numeric'),
    Operator('rolling.max','2.0','窗口最大值','temporal',1,'numeric'),
    Operator('rolling.duration.mean','2.0','按真实时长移动平均','temporal',1,'numeric',time_required=True),
    Operator('rolling.duration.min','2.0','按真实时长取最小值','temporal',1,'numeric',time_required=True),
    Operator('rolling.duration.max','2.0','按真实时长取最大值','temporal',1,'numeric',time_required=True),
    Operator('ewma','2.0','指数加权平均','research',1,'numeric'),
    Operator('event.since','2.0','距最近维护时间','temporal',1,'boolean',time_required=True),
    Operator('unit.convert','2.0','单位转换','research',1,'numeric'),
    Operator('rolling.sum','2.8','滑动求和','advanced',1,'numeric'),
    Operator('rolling.median','2.8','滚动中位数','advanced',1,'numeric'),
    Operator('rolling.p90','2.8','滚动90%分位数','advanced',1,'numeric'),
    Operator('rolling.range','2.8','滚动极差','advanced',1,'numeric'),
    Operator('rolling.cv','2.8','滚动变异系数','advanced',1,'numeric'),
    Operator('rolling.zscore','2.8','滚动标准化偏离','advanced',1,'numeric'),
    Operator('relative.change','2.8','历史相对变化率','advanced',1,'numeric'),
    Operator('transform.log1p','2.8','对数变换 log(1+x)','advanced',1,'numeric'),
    Operator('transform.abs','2.8','绝对值变换','advanced',1,'numeric'),
    Operator('transform.sqrt','2.8','平方根变换','advanced',1,'numeric'),
    Operator('expression.safe','2.8','自定义公式','custom',-1,'numeric'),
)}

# Compatibility: legacy expressions preserve their original ordering/semantics.
LEGACY = {'arithmetic':'arithmetic.binary','rolling_mean':'rolling.mean',
          'rolling_std':'rolling.std','lag':'lag.rows','difference':'difference.rows'}
SUPPORTED = {'arithmetic.binary','lag.rows','difference.rows','rolling.mean',
             'rolling.std','rolling.min','rolling.max',
             'rolling.duration.mean','rolling.duration.min','rolling.duration.max','event.since',
             'rolling.sum','rolling.median','rolling.p90','rolling.range','rolling.cv','rolling.zscore',
             'relative.change','transform.log1p','transform.abs','transform.sqrt','expression.safe'}



PARAMETER_CONTROLS = {
    'arithmetic.binary': ['first', 'second', 'sign'],
    'lag.rows': ['first', 'window'],
    'difference.rows': ['first', 'window'],
    'rolling.mean': ['first', 'window', 'min_periods'],
    'rolling.std': ['first', 'window', 'min_periods'],
    'rolling.min': ['first', 'window', 'min_periods'],
    'rolling.max': ['first', 'window', 'min_periods'],
    'rolling.duration.mean': ['first', 'window_seconds', 'min_periods'],
    'rolling.duration.min': ['first', 'window_seconds', 'min_periods'],
    'rolling.duration.max': ['first', 'window_seconds', 'min_periods'],
    'event.since': ['event'],
    'rolling.sum': ['first', 'window', 'min_periods'],
    'rolling.median': ['first', 'window', 'min_periods'],
    'rolling.p90': ['first', 'window', 'min_periods'],
    'rolling.range': ['first', 'window', 'min_periods'],
    'rolling.cv': ['first', 'window', 'min_periods'],
    'rolling.zscore': ['first', 'window', 'min_periods'],
    'relative.change': ['first', 'window'],
    'transform.log1p': ['first'],
    'transform.abs': ['first'],
    'transform.sqrt': ['first'],
    'expression.safe': ['formula'],
}


def compatible_fields(contract, kind='numeric'):
    """Use physical AND semantic type; no silent numerical Time/ID/label features."""
    if contract is None:return []
    from timepilot.feature_compiler import NUMERIC
    import re
    ids=[]
    for f in contract.fields:
        if kind=='boolean':
            ok=(str(f.physical_type).upper()=='BOOLEAN'
                and f.semantic_type in ('boolean','event') and f.confirmation=='confirmed')
        else:
            name=f.source_name.strip()
            suspicious=(bool(re.search(r'(^|[_\s-])(time|date|timestamp|datetime|year|month|day|id|uuid|serial|code|sn)([_\s-]|$)',name,re.I))
                        or any(part in name for part in ('时间','日期','编号','编码','序列号','流水号')))
            forbidden_role=f.role in ('entity_key','event_time','available_at','ingested_at',
                                      'prediction_time','label','batch_key','record_id')
            forbidden_semantic=f.semantic_type in ('datetime','id','event','boolean','category','string','target')
            ok=(bool(NUMERIC.match(str(f.physical_type))) and not forbidden_role
                and not forbidden_semantic and (not suspicious or
                   (f.confirmation=='confirmed' and f.semantic_type=='numeric' and f.role=='value')))
        if ok:ids.append(f.field_id)
    return ids


def discover(*, physical_types=None, has_time=False, contract=None):
    found=[]
    numeric=physical_types is None or any(('INT' in str(t).upper() or 'DOUBLE' in str(t).upper()
        or 'FLOAT' in str(t).upper() or 'DECIMAL' in str(t).upper()) for t in physical_types)
    has_boolean=physical_types is None or any(str(t).upper()=='BOOLEAN' for t in physical_types)
    if contract is not None:
        numeric=bool(compatible_fields(contract,'numeric'))
        has_boolean=bool(compatible_fields(contract,'boolean'))
        by_id={f.field_id:f for f in contract.fields}
        fid=contract.event_time_field_id
        has_time=bool(contract.state=='confirmed' and fid in by_id and contract.timezone
            and by_id[fid].confirmation=='confirmed' and by_id[fid].semantic_type=='datetime')
    for op in OPERATORS.values():
        why=None
        if op.operator_id not in SUPPORTED:why='当前版本尚无经过验证的计算执行器'
        elif op.type=='numeric' and not numeric:why='无适用数值字段，请检查时间/编号/标签语义'
        elif op.type=='boolean' and not has_boolean:why='没有经确认的 BOOLEAN 事件字段'
        elif contract is not None and (op.input_count if op.input_count >= 1 else 1)>len(compatible_fields(contract,op.type)):
            why='该算子需要至少两个适用字段，请补充数值列'
        elif op.time_required and not has_time:why='需要先确认真实事件时间和时区'
        found.append({'operator_id':op.operator_id,'version':op.version,'title':op.title,
                      'family':op.family,'input_count':op.input_count,
                      'available':why is None,'disabled_reason':why,'example':op.example,
                      'input_kind':op.type,
                      'controls':PARAMETER_CONTROLS.get(op.operator_id,[]),
                      'eligible_field_ids':compatible_fields(contract,op.type) if contract else None})
    return found

def validate_operator(definition, *, supervised=False):
    op=OPERATORS.get(definition.operator_id)
    if op is None:raise ValueError('未知特征算子：'+definition.operator_id)
    if op.operator_id not in SUPPORTED:raise ValueError('当前后端不支持算子：'+definition.operator_id)
    if definition.operator_version != op.version:raise ValueError('算子版本不兼容')
    if (op.operator_id == 'expression.safe' and not 1 <= len(definition.input_field_ids) <= 8) or (op.operator_id != 'expression.safe' and len(definition.input_field_ids) != op.input_count):
        raise ValueError('算子输入字段数量不正确')
    params=definition.parameters
    allowed={'arithmetic.binary':{'operator'},'lag.rows':{'lag','order_field_id','partition_field_ids'},
             'difference.rows':{'lag','order_field_id','partition_field_ids'},
             'rolling.mean':{'window','min_periods','order_field_id','partition_field_ids'},
             'rolling.std':{'window','min_periods','order_field_id','partition_field_ids'},
             'rolling.min':{'window','min_periods','order_field_id','partition_field_ids'},
             'rolling.max':{'window','min_periods','order_field_id','partition_field_ids'},
             'rolling.duration.mean':{'window_seconds','min_periods','order_field_id','partition_field_ids'},
             'rolling.duration.min':{'window_seconds','min_periods','order_field_id','partition_field_ids'},
             'rolling.duration.max':{'window_seconds','min_periods','order_field_id','partition_field_ids'},
             'event.since':{'order_field_id','partition_field_ids'},
             **{key:{'window','min_periods','order_field_id','partition_field_ids'} for key in (
                 'rolling.sum','rolling.median','rolling.p90','rolling.range','rolling.cv','rolling.zscore')},
             'relative.change':{'lag','order_field_id','partition_field_ids'},
             'transform.log1p':set(),'transform.abs':set(),'transform.sqrt':set(),
             'expression.safe':{'expression'}}[op.operator_id]
    if set(params)-allowed:raise ValueError('算子包含未知参数：'+','.join(sorted(set(params)-allowed)))
    if op.operator_id=='arithmetic.binary':
        if params.get('operator','+') not in ('+','-','*','/'):raise ValueError('不支持的数学运算符')
    elif op.operator_id=='expression.safe':
        from timepilot.feature_expression import validate_formula
        expression=params.get('expression')
        if not isinstance(expression,str):raise ValueError('自定义公式 expression 必填')
        validate_formula(expression,len(definition.input_field_ids))
        if len(set(definition.input_field_ids)) != len(definition.input_field_ids):
            raise ValueError('同一个输入字段不应重复绑定多个变量')
    elif op.operator_id.startswith('transform.'):
        if params:raise ValueError('标量变换算子不接受窗口参数')
    elif op.operator_id=='event.since':
        if not params.get('order_field_id'):
            raise ValueError('维护事件距今计算需要指定真实事件时间字段')
    elif op.operator_id.startswith('rolling.duration.'):
        seconds=params.get('window_seconds')
        if type(seconds) is not int or not 1<=seconds<=604800:
            raise ValueError('时长窗口必须明确 1 到 604800 秒，且使用真实时间字段')
        k=params.get('min_periods',1)
        if type(k) is not int or not 1<=k<=100000:
            raise ValueError('时长窗口最少有效观测数量非法')
        if not params.get('order_field_id'):
            raise ValueError('时长窗口必须指定已确认的时间字段')
    else:
        key='lag' if op.operator_id in ('lag.rows','difference.rows','relative.change') else 'window'
        value=params.get(key,1 if key=='lag' else 5)
        if type(value) is not int or not 1<=value<=240:raise ValueError('窗口/滞后必须是 1 到 240 的整数条数')
        if 'min_periods' in params:
            period=params['min_periods']
            if type(period) is not int or not 1<=period<=value:raise ValueError('有效观测数范围无效')
        if supervised and not params.get('order_field_id'):
            raise ValueError('监督时序特征必须明确真实事件时间字段，不接受源行号默认排序')
    return True
