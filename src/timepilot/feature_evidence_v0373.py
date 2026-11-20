"""v0.37.3 deterministic, auditable Chinese analytics explanations.

Pure and read-only: all metric references originate in a server-generated
source/contract/scope-pinned aggregate.  Explanations NEVER run a model, infer
root cause, assume an observed correlation is predictive, or invent a label.
The evidence digest is a content address of the *numerical* inputs and scope.
"""
from __future__ import annotations
import hashlib
import json
import math
from typing import Any


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number=float(value)
    return number if math.isfinite(number) else None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),
                         ensure_ascii=False,default=str,allow_nan=False).encode()).hexdigest()


def _n(value: Any) -> str:
    x=_finite(value)
    if x is None:return '无法判断'
    if x.is_integer():return f'{int(x):,}'
    return f'{x:,.4g}'


def _scope(data: dict) -> dict:
    return {key:data.get(key) for key in ('asset_id','version_id','source_sha256',
             'contract_revision','scope_sha256','candidate_id','candidate_fingerprint',
             'field_id','mode','entity_value','time_range') if data.get(key) is not None}


def make_claim(data:dict, code:str, title:str, text:str, inputs:list[tuple], *,
               action:str='review', level:str='information', caveat:str='') -> dict:
    """Make claim only from *finite, nonempty, structured* quantitative evidence.

    Each input is (JSON path, original numeric value[, unit]). A missing
    denominator is never manufactured. `code` is not itself evidence.
    """
    scope=_scope(data)
    if not isinstance(scope.get('source_sha256'),str) or len(scope['source_sha256'])!=64 or any(c not in '0123456789abcdef' for c in scope['source_sha256'].lower()):
        raise ValueError('missing source-pinned evidence identity')
    evidence=[]
    for item in inputs:
        path,value,*rest=item
        number=_finite(value)
        if not isinstance(path,str) or not path or number is None:
            raise ValueError('unsupported or missing numeric observation')
        evidence.append({'path':path,'value':value,'unit':rest[0] if rest else 'count'})
    if not evidence:raise ValueError('substantive claim requires a numeric observation')
    content={'code':code,'scope':scope,'evidence':evidence}
    return {'id':_digest(content),'code':code,'title':title,'observation':text,
            'claim_type':'descriptive_observation','status':'supported',
            'scope':scope,'evidence':evidence,'action':action,'level':level,
            'limitations':[caveat] if caveat else []}


def no_evidence(reason:str, data:dict|None=None) -> dict:
    return {'status':'insufficient_evidence','message':reason,'scope':_scope(data or {}),
            'claims':[], 'predictive_gain_evaluated':False}


def dashboard_explanations(report:dict) -> dict:
    """Explain an immutable DuckDB overview; reject inconsistent aggregates."""
    if not isinstance(report,dict):return no_evidence('缺少可核验的数据概况，无法判断。')
    scope=_scope(report)
    if not (isinstance(scope.get('source_sha256'),str) and len(scope['source_sha256'])==64 and all(c in '0123456789abcdef' for c in scope['source_sha256'].lower())
            and scope.get('scope_sha256') and scope.get('contract_revision') is not None):
        return no_evidence('缺少数据来源、筛选范围或字段契约指纹，无法判断。',report)
    rows=report.get('rows_filtered');total=report.get('rows_total');fields=report.get('fields') or []
    if not (type(rows) is int and type(total) is int and 0<=rows<=total
            and isinstance(fields,list)):
        return no_evidence('有效记录数与筛选范围不一致，无法判断。',report)
    metrics=report.get('metrics') or {}
    if metrics.get('records')!=rows or metrics.get('source_records')!=total:
        return no_evidence('统计卡片与源数据行数冲突，已禁止输出结论。',report)
    if any(not isinstance(f,dict) or type(f.get('null_count')) is not int or
           type(f.get('non_null')) is not int or f['null_count']+f['non_null']!=rows or
           f['null_count']<0 or f['non_null']<0 for f in fields):
        return no_evidence('字段缺失数与当前筛选记录数不一致，已禁止输出结论。',report)
    if rows and any(_finite(f.get('null_rate')) is not None and
                    abs(f['null_rate']-f['null_count']/rows)>1e-5 for f in fields):
        return no_evidence('字段缺失比例与缺失条数冲突，已禁止输出结论。',report)
    if any(isinstance(c,dict) and _finite(c.get('pearson')) is not None and
           abs(c['pearson'])>1.000001 for c in (report.get('correlations') or [])):
        return no_evidence('相关系数超出有效范围，无法判断。',report)
    missing=sum(f['null_count'] for f in fields)
    if metrics.get('missing_cells')!=missing:
        return no_evidence('字段缺失数合计与仪表盘指标不一致，已禁止输出结论。',report)
    metric_scope=metrics.get('metric_scope')
    if metric_scope is not None:
        if (not isinstance(metric_scope,dict) or
            metric_scope.get('field_ids') != [f.get('field_id') for f in fields] or
            metric_scope.get('rows') != rows or
            metric_scope.get('denominator_cells') != rows*len(fields) or
            metrics.get('selected_fields') != len(fields)):
            return no_evidence('报告统计范围、字段选择与实际聚合结果不一致，已禁止输出结论。',report)
        invalid=sum(f.get('invalid_numeric_count',0) for f in fields)
        non_finite=sum(f.get('non_finite_count',0) for f in fields)
        conversion_failed=sum(f.get('conversion_failed_count',0) for f in fields)
        if (any(type(f.get(k,0)) is not int or f.get(k,0)<0 for f in fields for k in
            ('invalid_numeric_count','non_finite_count','conversion_failed_count')) or
            metrics.get('invalid_numeric_cells') != invalid or
            metrics.get('non_finite_numeric_cells') != non_finite or
            metrics.get('conversion_failed_cells') != conversion_failed or
            invalid != non_finite+conversion_failed):
            return no_evidence('报告的非有限值或转换失败计数与字段明细不一致，已禁止输出结论。',report)
        denominator=metric_scope['denominator_cells']
        expected_percent=100*(missing+invalid)/denominator if denominator else None
        actual_percent=metrics.get('missing_percent')
        if (expected_percent is None and actual_percent is not None) or (expected_percent is not None and
            (_finite(actual_percent) is None or abs(actual_percent-expected_percent)>0.000051)):
            return no_evidence('报告的总体缺失比例与统计分母不一致，已禁止输出结论。',report)
    claims=[]
    if total and rows<total:
        claims.append(make_claim(report,'filtered-coverage','当前分析范围',
            f'当前筛选保留 {_n(rows)} / {_n(total)} 条记录（{rows/total*100:.1f}%），下列结论仅适用于这部分数据。',
            [('rows_filtered',rows),('rows_total',total)],action='filters'))
    if rows and fields:
        high=[(i,f) for i,f in enumerate(fields) if f['null_count']/rows>=0.1]
        if high:
            i,f=max(high,key=lambda r:r[1]['null_count'])
            rate=f['null_count']/rows*100
            claims.append(make_claim(report,'missing-top','优先核对缺失字段',
                 f'{f.get("name") or f.get("field_id")} 在当前 {_n(rows)} 条记录中缺失 {_n(f["null_count"])} 条（{rate:.1f}%）。可查看缺失记录的设备、时间和工况分布。',
                 [(f'fields[{i}].null_count',f['null_count']),('rows_filtered',rows)],action='quality',level='review',
                 caveat='缺失比例只描述当前筛选数据，无法判断设备故障或缺失原因。'))
    corr=report.get('correlations') or []
    strong=[(i,c) for i,c in enumerate(corr) if isinstance(c,dict) and
            _finite(c.get('pearson')) is not None and
            abs(float(c['pearson']))>=.8 and type(c.get('pair_count')) is int and
            c['pair_count']>=20 and c['pair_count']<=rows]
    if strong:
        idx,c=max(strong,key=lambda p:abs(p[1]['pearson']))
        labels={f.get('field_id'):f.get('name') for f in fields}
        a=labels.get(c.get('x_field_id'),c.get('x_field_id','字段 A'))
        b=labels.get(c.get('y_field_id'),c.get('y_field_id','字段 B'))
        claims.append(make_claim(report,'strong-linear-pair','发现较强线性关联',
            f'{a} 与 {b} 在 {_n(c["pair_count"])} 组有效记录上的 Pearson 相关系数为 {_n(c["pearson"])}；可进一步检查字段定义、共线性和设备分层差异。',
            [(f'correlations[{idx}].pearson',c['pearson'],'coefficient'),
             (f'correlations[{idx}].pair_count',c['pair_count'])],action='correlation',
            caveat='相关性不能说明因果关系，也不能证明该字段能提高预测精度。'))
    spreads=[(i,f) for i,f in enumerate(fields) if f.get('numeric') and
            _finite(f.get('median')) is not None and _finite(f.get('p90')) is not None]
    if spreads:
        i,f=max(spreads,key=lambda p:abs(p[1]['p90']-p[1]['median']))
        claims.append(make_claim(report,'observed-quantiles','分位数显示高值范围',
            f'{f.get("name") or f.get("field_id")} 的中位数为 {_n(f["median"])}，90% 分位数为 {_n(f["p90"])}；高值记录的业务含义需要结合采集单位与工况确认。',
            [(f'fields[{i}].median',f['median']), (f'fields[{i}].p90',f['p90'])],action='statistics'))
    if not claims:
        return no_evidence('当前范围没有达到预设证据门槛的统计发现；可查看字段概况，暂无法判断异常或收益。',report)
    return {'schema_version':1,'status':'supported','scope':scope,'claims':claims[:5],
            'predictive_gain_evaluated':False,
            'limitations':['这些观察仅适用于当前源数据版本与筛选范围；无监督统计不能评估预测收益。']}


def chart_explanations(chart:dict) -> dict:
    """Template conclusions from exactly the aggregated bins/series points."""
    if not isinstance(chart,dict):return no_evidence('尚未取得图表计算结果。')
    source=chart.get('source_sha256')
    if not isinstance(source,str) or len(source)!=64 or any(c not in '0123456789abcdef' for c in source.lower()):
        return no_evidence('图表尚无可核验的来源指纹，无法判断。',chart)
    rows=chart.get('points') or []
    mode=chart.get('mode') or 'distribution';claims=[]
    label=str(chart.get('feature_name') or chart.get('field_name') or '当前字段')[:100]
    if mode=='distribution':
        values=[(i,p,int(p['count'])) for i,p in enumerate(rows) if
                isinstance(p,dict) and type(p.get('count')) is int and p['count']>=0]
        finite=(chart.get('quality') or {}).get('finite_count')
        observed=sum(v for _,_,v in values)
        if type(finite) is not int or observed!=finite or observed<=0:
            return no_evidence('直方图分桶与有效记录总数不一致，无法判断集中比例。',chart)
        i,p,count=max(values,key=lambda item:item[2])
        if count>0:
            claims.append(make_claim(chart,'dominant-bin','数值集中区间',
              f'{label} 在 {p.get("label","当前区间")} 内有 {_n(count)} / {_n(finite)} 条有效记录（{count/finite*100:.1f}%）；请检查这一范围与业务工况是否一致。',
              [(f'points[{i}].count',count),('quality.finite_count',finite)],action='inspect_distribution'))
    elif mode=='series':
        candidates=[]
        for i,p in enumerate(rows):
            lo=_finite(p.get('min'));hi=_finite(p.get('max'))
            if lo is not None and hi is not None and hi>=lo:candidates.append((hi-lo,i,lo,hi))
        if candidates:
            span,i,lo,hi=max(candidates)
            p=rows[i]
            claims.append(make_claim(chart,'largest-bucket-range','最大区间波动',
                f'{label} 在 {str(p.get("x","该分桶"))[:70]} 对应区间内的最大值为 {_n(hi)}、最小值为 {_n(lo)}，极差 {_n(span)}。该区间需要结合原始数据和采样时序核对。',
                [(f'points[{i}].min',lo,chart.get('feature_unit') or 'value'),(f'points[{i}].max',hi,chart.get('feature_unit') or 'value')],action='inspect_range',
                caveat='区间极差是描述统计；没有时间顺序核验或设备隔离时不能推断异常原因。'))
        # Like-for-like bucket means are a descriptive comparison; never
        # compare different physical units or pretend bucket means are rows.
        if chart.get('axis_compatible') is True:
            pairs=[]
            for i,p in enumerate(rows):
                derived=_finite(p.get('value'))
                original=_finite(p.get('original'))
                if derived is not None and original is not None:
                    pairs.append((i,derived,original))
            if len(pairs)>=3:
                avg_delta=sum(abs(a-b) for _,a,b in pairs)/len(pairs)
                claims.append(make_claim(chart,'bucket-mean-difference',
                    '与原始字段的区间均值比较',
                    f'{label} 和原始字段在 {_n(len(pairs))} 个相同分桶上均有均值，分桶均值的平均绝对差为 {_n(avg_delta)}。该差值只描述两列的数值变化，不能用作预测误差。',
                    [(f'points[{i}].value',a,chart.get('feature_unit') or 'value') for i,a,_ in pairs]+
                    [(f'points[{i}].original',b,chart.get('source_unit') or 'value') for i,_,b in pairs],
                    action='compare_original',
                    caveat='分桶均值不能替代逐记录对齐比较；该统计与监督模型收益无关。'))
    elif mode in ('category','group'):
        candidates=[(i,p,int(p['count'])) for i,p in enumerate(rows) if
                    isinstance(p,dict) and type(p.get('count')) is int and p['count']>=0]
        if mode=='group':
            means=[(i,_finite(p.get('mean')),_finite(p.get('count'))) for i,p in enumerate(rows)]
            means=[(i,v,n) for i,v,n in means if v is not None and n is not None and n>=3]
            if len(means)>=2:
                low=min(means,key=lambda r:r[1]); high=max(means,key=lambda r:r[1])
                if low[0]!=high[0]:
                    claims.append(make_claim(chart,'group-mean-spread',
                      '各设备或分组的均值差异',
                      f'{label} 在当前展示的有效分组中，均值最小为 {_n(low[1])}、最大为 {_n(high[1])}，差值 {_n(high[1]-low[1])}；建议先核对设备工况与采样覆盖。',
                      [(f'points[{low[0]}].mean',low[1],chart.get('feature_unit') or 'value'),
                       (f'points[{high[0]}].mean',high[1],chart.get('feature_unit') or 'value'),
                       (f'points[{low[0]}].count',low[2]),(f'points[{high[0]}].count',high[2])],
                      action='inspect_group',
                      caveat='分组差异不能解释原因；当前图可能只展示前 12 个组。'))
        if candidates:
            i,p,n=max(candidates,key=lambda x:x[2])
            claims.append(make_claim(chart,'largest-group','分组记录数量',
                f'{str(p.get("label","当前分组"))[:80]} 在当前展示的分组中记录最多，为 {_n(n)} 条。图表可能只展示部分分组，不能据此推断全量占比。',
                [(f'points[{i}].count',n)],action='inspect_group'))
    if not claims:return no_evidence('当前图表缺少完整、相互一致的数值证据，无法判断。',chart)
    return {'schema_version':1,'status':'supported','scope':_scope(chart),'claims':claims[:3],
            'predictive_gain_evaluated':False,'limitations':['此结论仅描述图表统计，不包含预测收益判断。']}


def dashboard_markdown(report:dict) -> dict:
    """Read-only export of the same verified server claims as the dashboard UI."""
    out=dashboard_explanations(report)
    if out['status']=='insufficient_evidence':
        raise ValueError(out['message'])
    scope=out['scope']
    lines=['# 数据一览与证据报告','',
           '- 数据资产：`'+str(scope.get('asset_id') or '')+'`',
           '- 数据版本：`'+str(scope.get('version_id') or '')+'`',
           '- 来源 SHA256：`'+str(scope['source_sha256'])+'`',
           '- 筛选指纹 SHA256：`'+str(scope['scope_sha256'])+'`',
           '- 字段契约：'+str(scope['contract_revision']),
           '- 筛选后记录：'+_n(report['rows_filtered'])+' / '+_n(report['rows_total']),
           '']
    metric_scope=(report.get('metrics') or {}).get('metric_scope')
    if isinstance(metric_scope,dict):
        metrics=report['metrics']
        lines.extend(['## 统计口径', '',
            '- 本次参与统计的字段 ID：'+', '.join('`'+str(x)+'`' for x in metric_scope['field_ids']),
            '- 字段数量：'+_n(metrics['selected_fields'])+' / '+_n(metrics['columns']),
            '- 本次统计单元格：'+_n(metric_scope['denominator_cells'])+'（筛选行数 × 已选字段数）',
            '- SQL NULL：'+_n(metrics['missing_cells']),
            '- 非有限数值（NaN/Inf）：'+_n(metrics['non_finite_numeric_cells']),
            '- 数值转换失败：'+_n(metrics['conversion_failed_cells']),
            '- 综合缺失率：'+(_n(metrics['missing_percent'])+'%' if metrics['missing_percent'] is not None else '无有效分母'),
            '', '上述统计仅覆盖本次已选择的字段；业务阈值异常另行判定。',''])
    lines.extend(['## 可核验的观察',''])
    for claim in out['claims']:
        lines.append('### '+claim['title'])
        lines.append(claim['observation'])
        lines.append('')
        lines.append('证据 ID：`'+claim['id']+'`；'+ '；'.join(
            f'`{e["path"]}` = {_n(e["value"])}' for e in claim['evidence']))
        if claim['limitations']:lines.append('限制：'+ '；'.join(claim['limitations']))
        lines.append('')
    lines.extend(['## 结论边界','',
        '当前数据没有经过受控监督模型对照，此报告不包含预测收益、因果关系或故障原因判断。',''])
    markdown='\n'.join(lines)
    return {'markdown':markdown,'report_sha256':hashlib.sha256(markdown.encode()).hexdigest(),
            'source_sha256':scope['source_sha256'],'scope_sha256':scope['scope_sha256'],
            'claim_ids':[c['id'] for c in out['claims']]}
