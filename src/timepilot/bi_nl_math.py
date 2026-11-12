"""Deterministic, source-schema-bound arithmetic and causal window intents.

Natural-language parsing never evaluates a Python expression nor authorizes SQL.
If an operand is not an exact source field, no action is returned.
"""
from __future__ import annotations
import re
from timepilot.bi_tool_registry import BIAction

_NUMERIC=re.compile(r'^(?:TINYINT|SMALLINT|INTEGER|INT\d*|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|REAL|DECIMAL|NUMERIC)\b')

def names_mentioned(message:str,columns:list[str])->list[str]:
    matched=[]
    for n in columns:
        hit=re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])',message,re.I)
        if hit:matched.append((hit.start(),n))
    return [x[1] for x in sorted(matched,key=lambda x:x[0])]


def parse_math_request(message,columns):
    """Return (actions, explanation, clarification). No guessed business meaning.

    A missing explicit time axis is resolved to the preserved immutable row
    ordinal, clearly reported as record-order (not a calendar interval).
    """
    text=str(message).strip()
    fields=[x['name'] for x in columns]
    referenced=names_mentioned(text,fields)
    numeric={x['name'] for x in columns if bool(_NUMERIC.match(str(x.get('physical_type','')).upper()))}
    if any(k in text for k in ('移动平均','滑动平均','滚动平均','rolling mean','rolling average')):
        if not referenced:
            return [],None,'请指定需要计算移动平均的数值字段'
        missing=[name for name in referenced if name not in numeric]
        if missing:
            return [],None,'以下字段尚未确认是数值类型：'+', '.join(missing)
        length=re.search(r'(?:窗口|最近|近)\s*(\d{1,3})\s*(?:行|个|条|点|期)?',text)
        w=int(length.group(1)) if length else 5
        if not 2<=w<=240:return [],None,'移动平均窗口必须为 2–240 条观测'
        # explicit ordering has priority; otherwise use the pinned source row id
        order=next((name for name in fields if re.search(r'(?:按|根据|以)\s*'+re.escape(name)+r'\s*(?:排序|为序|顺序)',text)),None)
        order=order or '__tp_rowid'
        intent=BIAction(tool='bi.window.compute_many',arguments={
            'columns':referenced,'time_field':order,'window':w})
        explanation=(f'按{order}排序，对'+', '.join(referenced)+f'分别计算 {w} 条记录的因果移动平均；'
                     '每个字段生成独立虚拟列。未指定日历时间单位。'
                     +('默认按源记录顺序；可以明确指定排序字段。' if order=='__tp_rowid' else ''))
        return [intent],explanation,None
    paired=sum(bool(re.search(re.escape(referenced[i])+r'\s*(?:和|与|及|\+|\-|\*|/|加|减|乘|除)\s*'+re.escape(referenced[j]),text))
               for i in range(len(referenced)) for j in range(i+1,len(referenced))) if len(referenced)>=2 else 0
    arithmetic_cues=('相加','加起来','之和','相减','减去','差值','乘以','相乘','相除','除以','+','*','/')
    # A grouped SUM request mentions both the group key and a metric but must
    # not be mistaken for elementwise addition of these two fields.
    if len(referenced)>=2 and ((any(x in text for x in arithmetic_cues) and '分组' not in text)
                               or ('求和' in text and paired and '分组' not in text)):
        a,b=referenced[:2]
        if a not in numeric or b not in numeric:
            return [],None,'算术运算需要已校验的数值字段：'+a+'、'+b
        op=next((v for words,v in [(['相加','加起来','之和','求和','+'],'+'),(['相减','减去','差值'],'-'),(['相乘','乘以','*'],'*'),(['相除','除以','/'],'/')] if any(w in text for w in words)),'+')
        tags={'+':'plus','-':'minus','*':'times','/':'div'}
        alias=('computed_difference' if op=='-' and '计算' in text and '差值' in text
               else a+'_'+tags[op]+'_'+b)
        if len(alias)>64 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',alias):
            alias='calculated_'+tags[op]
        if alias in fields:return [],None,'计算列 '+alias+' 已存在，请明确新的列名或先取消计算列'
        # Keep exact token reference in server-validated AST. No expressions as strings.
        action=BIAction(tool='bi.expression.create',arguments={
            'name':alias,'expression':{'kind':'binary','op':op,
                    'left':{'kind':'column','value':a},'right':{'kind':'column','value':b}}})
        return [action],f'{a} {op} {b} → 虚拟新列 {alias}；原始源文件不变。',None
    return [],None,None
