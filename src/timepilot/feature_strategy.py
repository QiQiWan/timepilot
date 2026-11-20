"""v0.34.1: bounded, deterministic feature-family planning from source-only evidence.

Plans contain only existing allowlisted operators. A plan is a suggestion, never a
publish decision, and supervised evaluation still has to fit transforms per fold.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from timepilot.feature_contracts import digest
from timepilot.feature_operator_registry import SUPPORTED, compatible_fields
from timepilot.feature_topology import infer_topology

FAMILY_LABELS = {
    'transform': '基础变换', 'interaction': '变量交互', 'history': '历史变化',
    'statistics': '窗口统计', 'anomaly': '异常与波动',
    'category': '类别指标', 'event': '维护与事件', 'mechanism':'机理与维护重置',
}
FAMILY_COST = {'transform': 'low', 'interaction': 'low', 'history': 'medium',
               'statistics': 'medium', 'anomaly': 'medium',
               'category': 'low', 'event': 'medium', 'mechanism':'medium'}
ROW_ORDER_OPS = {'lag.rows', 'difference.rows', 'relative.change', 'rolling.mean',
    'rolling.std', 'rolling.min', 'rolling.max', 'rolling.sum', 'rolling.median',
    'rolling.p90', 'rolling.range', 'rolling.cv', 'rolling.zscore',
    'rolling.duration.mean', 'rolling.duration.min', 'rolling.duration.max', 'event.since',
    'industrial.reset_mean','industrial.reset_sum'}


def _num(value, default=None):
    try:
        number=float(value)
        return number if -float('inf')<number<float('inf') else default
    except (ValueError, TypeError, OverflowError):
        return default


def plan_candidates(contract, *, profile=None, objective='explore', budget=16,
                    selected_fields=None, topology=None, category_values=None,
                    task_mode='exploration'):
    """Return balanced candidate instructions and explicit disabled reasons.

    Profile statistics are UNSUPERVISED descriptive evidence only. Never reads a
    label, target correlation, or future outcome to rank candidate columns.
    """
    if not 1 <= int(budget) <= 64:
        raise ValueError('候选数量须为 1—64')
    if task_mode not in ('exploration', 'supervised'):
        raise ValueError('未知特征任务类型')
    by_id={f.field_id:f for f in contract.fields}
    usable={fid for fid in compatible_fields(contract,'numeric')
            if by_id[fid].classification!='restricted'}
    if selected_fields is not None:
        unknown=set(selected_fields)-set(by_id)
        if unknown:raise ValueError('候选字段不存在于当前数据契约')
        usable.intersection_update(selected_fields)
    # For supervised plans, unconfirmed fields cannot become model inputs.
    if task_mode=='supervised':
        usable={fid for fid in usable if by_id[fid].confirmation=='confirmed'
                and by_id[fid].classification!='restricted'
                and by_id[fid].role in ('value', 'unspecified')}
    stats={x.get('field_id'):x for x in (profile or {}).get('fields', [])}
    def priority(fid):
        row=stats.get(fid) or {}
        missing=_num(row.get('null_rate'),0.2)
        nuniq=_num(row.get('distinct_count'),5)
        variance=_num(row.get('std'),1)
        # Poor coverage and constant fields are deprioritized, not silently used
        # as supervised evidence. Tie break by immutable field id, never order.
        return (missing >= .6 or (nuniq is not None and nuniq<2) or variance==0,
                -max(0,1-missing), -min(1000,nuniq), fid)
    numbers=sorted(usable,key=priority)
    topo=topology or (profile or {}).get('topology') or infer_topology(contract)
    has_order=bool(topo.get('can_propose_windows'))
    time_ok=bool(contract.state=='confirmed' and contract.event_time_field_id
        and contract.timezone and contract.event_time_field_id in by_id
        and by_id[contract.event_time_field_id].confirmation=='confirmed')
    categories=[f for f in contract.fields if f.field_id in compatible_fields(contract,'category')]
    events=[f for f in contract.fields if f.field_id in compatible_fields(contract,'boolean')
            and f.classification!='restricted']
    pools=defaultdict(list)
    seen=set()
    def add(family,op,fields,params,reason):
        if op not in SUPPORTED:return
        if op in ROW_ORDER_OPS and not has_order:return
        if op.startswith('rolling.duration.') and not time_ok:return
        key=digest([op,list(fields),params])
        if key in seen:return
        seen.add(key)
        pools[family].append({'operator_id':op,'input_field_ids':list(fields),
           'parameters':params,'family':family,'family_label':FAMILY_LABELS[family],
           'cost_tier':FAMILY_COST[family],'reason':reason,'fingerprint':key})
    for fid in numbers:
        f=by_id[fid]
        row=stats.get(fid) or {}
        low=_num(row.get('min'));high=_num(row.get('max'))
        zeros=_num(row.get('zero_rate'),0)
        add('transform','transform.abs',[fid],{},'逐行绝对值，检查变量幅值，不依赖记录顺序')
        if low is not None and low>=0:
            add('transform','transform.sqrt',[fid],{},'非负数值的平方根变换；用于检查偏态')
        if low is not None and low>=-1:
            add('transform','transform.log1p',[fid],{},'对数型非线性变换；计算前检查数值域')
        if not has_order:continue
        add('history','difference.rows',[fid],{'lag':1},'相邻历史变化，使用已确认的排序和设备分组')
        add('history','lag.rows',[fid],{'lag':1},'上一观测值；需要已确认的预测时点')
        add('history','relative.change',[fid],{'lag':1},'相邻记录的相对变化，除零结果置空')
        add('statistics','rolling.mean',[fid],{'window':5,'min_periods':2},'最近 5 条历史记录均值')
        add('statistics','rolling.median',[fid],{'window':7,'min_periods':3},'对尖峰更稳健的历史窗口中位数')
        add('statistics','rolling.p90',[fid],{'window':7,'min_periods':3},'历史窗口的高分位数')
        add('anomaly','rolling.std',[fid],{'window':7,'min_periods':3},'近期波动强度')
        add('anomaly','rolling.range',[fid],{'window':7,'min_periods':3},'历史极差，辅助识别局部尖峰')
        add('anomaly','rolling.zscore',[fid],{'window':7,'min_periods':3},'相对近期历史的标准化偏离')
        if zeros is not None and zeros>.35:
            add('anomaly','rolling.max',[fid],{'window':5,'min_periods':2},'零值较多时跟踪历史非零峰值')
        if time_ok:
            add('statistics','rolling.duration.mean',[fid],{'window_seconds':1800,'min_periods':2},'按真实时间计算过去 30 分钟均值')
    for i,a in enumerate(numbers):
        # Only nearby *eligible* fields; no combinatorial explosion, no
        # arbitrary mixing of incompatible declared engineering units.
        for b in numbers[i+1:i+3]:
            f1,f2=by_id[a],by_id[b]
            if f1.unit and f2.unit and f1.unit!=f2.unit:continue
            add('interaction','arithmetic.binary',[a,b],{'operator':'-'},
                '同量纲字段差值；须核查两个变量的工程含义')
            if f1.unit and f2.unit and f1.unit==f2.unit:
                add('interaction','arithmetic.binary',[a,b],{'operator':'/'},
                    '同量纲字段比值；分母为零时返回空值')
    values=category_values or {}
    for field in categories:
        selected=list(values.get(field.field_id) or
                      [x['value'] for x in (stats.get(field.field_id) or {}).get('top_categories',[])])
        for val in selected[:2]:
            if isinstance(val,(str,int,float,bool)) and len(str(val))<=120:
                add('category','category.is_value',[field.field_id],{'category_value':val},
                    '来自全量频数画像的已确认类别，逐行计算')
    if time_ok and contract.entity_field_ids and has_order:
        from timepilot.feature_industrial import eligible_reset_features
        for fid,params in eligible_reset_features(contract):
            if fid not in usable:continue
            add('mechanism','industrial.reset_mean',[fid],params,
                '人工确认的维护事件与工况转换边界内计算均值；无重置历史返回空值')
        for field in events:
            add('event','event.since',[field.field_id],{},'按真实时间计算距最近维护事件的时长')
    if objective in ('anomaly', 'quality'):
        order=['anomaly','statistics','history','interaction','transform','category','mechanism','event']
    elif objective in ('forecast','regression'):
        order=['history','statistics','interaction','anomaly','transform','category','mechanism','event']
    elif objective=='classification':
        order=['interaction','statistics','anomaly','category','history','transform','mechanism','event']
    else:
        order=['transform','statistics','history','anomaly','interaction','category','mechanism','event']
    if not has_order:
        order=['transform','interaction','category']
    result=[];index={name:0 for name in order}
    # A fair operator-family round robin. Within each family, fields are
    # interleaved rather than exhausting one field before sampling the next.
    order_index={fid:i for i,fid in enumerate(numbers)}
    count=max(1,len(numbers))
    for group_index,family in enumerate(order):
        # Each family starts with a *different* field; then moves across
        # fields before repeating another parameter variant on the same field.
        # This prevents wide-table A1/A2 from consuming the complete budget.
        offset=(2*group_index)%count
        # Interleave fields *within* each operator family. Sorting by field
        # first would still select mean/p90/median for A1 before considering A2.
        buckets=defaultdict(list)
        for item in pools[family]:
            rank=min((order_index[fid] for fid in item['input_field_ids']
                      if fid in order_index),default=0)
            buckets[rank].append(item)
        for variants in buckets.values():
            variants.sort(key=lambda x:(x['operator_id'],x['fingerprint']))
        ranks=sorted(buckets,key=lambda k:((k-offset)%count,k))
        # Rotate the initial variant as fields advance. A small budget should
        # cover multiple operators as well as multiple inputs, rather than
        # taking the same operator from every field.
        for position,rank in enumerate(ranks):
            variants=buckets[rank]
            if variants:
                shift=(position+group_index)%len(variants)
                buckets[rank]=variants[shift:]+variants[:shift]
        balanced=[]
        while True:
            moved=False
            for rank in ranks:
                if buckets[rank]:
                    balanced.append(buckets[rank].pop(0));moved=True
            if not moved:break
        pools[family]=balanced
    while len(result)<budget:
        moved=False
        for family in order:
            if index[family]<len(pools[family]):
                result.append(pools[family][index[family]])
                index[family]+=1;moved=True
                if len(result)>=budget:break
        if not moved:break
    blocked=[]
    if not has_order:blocked.append({'family':'history/statistics/anomaly/event',
           'reason':'未确认安全的记录顺序及设备分组，已关闭全部窗口/历史算子'})
    if not time_ok:blocked.append({'family':'duration',
           'reason':'没有已确认的真实事件时间和时区，无法使用按秒计算的窗口'})
    if not categories:blocked.append({'family':'category','reason':'没有已确认且允许使用的类别字段'})
    if not numbers:blocked.append({'family':'numeric','reason':'缺少符合角色和物理类型约束的数值字段'})
    for n,item in enumerate(result):
        item['search_round']=n//16+1
    counts=dict(sorted(Counter(x['family'] for x in result).items()))
    return {'schema_version':1,'strategy':'adaptive','objective':objective,
        'budget':budget,'proposals':result,'family_counts':counts,
        'family_labels':FAMILY_LABELS,'blocked':blocked,'eligible_numeric_fields':len(numbers),
        'search_plan':{'stage':'planned_source_only','max_candidates':int(budget),
                       'rounds':max(1,(len(result)+15)//16),'round_size':16,
                       'evaluation_scope':'quality_only_until_supervised_validation',
                       'stop_reason':'source_safe_proposal_budget_reached' if len(result)>=budget else 'eligible_proposals_exhausted'},
        'topology_safe':has_order,'source_only':True,'requires_approval_for_publish':True,
        'planner_sha256':digest({'objective':objective,'budget':budget,
            'proposals':result,'topology':topo,'profile_sha256':(profile or {}).get('source_sha256')})}
