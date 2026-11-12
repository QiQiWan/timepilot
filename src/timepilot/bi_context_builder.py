"""Retrieve only authorized, relevant schema fields and executable tools.

Wide industrial telemetry tables can contain hundreds of columns. This module
never truncates the first N columns blindly, and never includes source rows.
"""
from __future__ import annotations
import re
from typing import Iterable


def mentioned_fields(message:str, columns:list[dict])->list[dict]:
    ranked=[]
    for index,field in enumerate(columns):
        name=field.get('name','')
        if not name:continue
        match=re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',message,re.I)
        if match:
            ranked.append((match.start(),index,field))
    return [entry for _,_,entry in sorted(ranked)]


def select_fields(message:str,columns:list[dict],limit=80)->dict:
    direct=mentioned_fields(message,columns)
    if len(direct)>limit:
        return {'status':'needs_clarification','reason':f'一次涉及 {len(direct)} 个字段，超过单次规划预算 {limit}',
                'fields':[],'mentioned':[],'total':len(columns),'complete':False}
    matched={x['name'] for x in direct}
    remaining=[x for x in columns if x['name'] not in matched]
    # Keep distinct technical names with structural field types only; no rows.
    chosen=direct+remaining[:max(0,limit-len(direct))]
    return {'status':'ready','fields':[{'name':x['name'],'physical_type':x.get('physical_type')}
                                      for x in chosen],
            'mentioned':[x['name'] for x in direct],
            'total':len(columns),'complete':len(chosen)==len(columns),
            'omitted_count':len(columns)-len(chosen)}


def select_tools(message:str,tools:list[dict],limit=40)->dict:
    synonyms={
        'schema':('字段','列名','schema','类型','结构'),
        'rows':('前','记录','样本','明细','行'),
        'expression':('相加','求和','相减','减去','乘','除','计算列','新列','差值'),
        'window':('滚动','滑动','移动平均','窗口','滞后','lag'),
        'sort':('排序','升序','降序','top','排名'),
        'filter':('筛选','过滤','条件','大于','小于','等于'),
        'chart':('图','可视化','趋势','散点','柱状','热力','绘制'),
        'aggregate':('分组','汇总','聚合','平均','统计'),
        'panel':('多图','看板','面板','并排'),
        'pivot':('透视','行分组','列分组'),
        'join':('合并表','关联','join'),
    }
    priorities=[]
    for tool in tools:
        if not tool.get('available') or tool.get('effect_class') not in ('read','presentation'):
            continue
        name=tool.get('tool','')
        score=1 if name in ('bi.schema.inspect','bi.rows.peek','bi.asset.describe') else 0
        for key,terms in synonyms.items():
            if key in name and any(term in message.lower() for term in terms):score+=10
        priorities.append((-score,name,tool))
    result=[tool for _,_,tool in sorted(priorities)[:limit]]
    return {'tools':[{'name':x['tool'],'input_schema':x['input_schema']} for x in result],
            'selected_count':len(result),'total_available':len(priorities),
            'omitted_count':max(0,len(priorities)-len(result))}
