"""Evidence-based modeling task and field-role recommendations (v0.38.9).

The output is a non-authoritative UI proposal. Business meaning, label timing,
independence and feature availability remain user-confirmed model contracts.
No labels, correlation scores, or future records are used for feature ranking.
"""
from __future__ import annotations

import re
from typing import Any

VERSION = '0.38.9'
TARGET_WORDS = ('销量', '需求', '销售额', '温度', '压力', '振动', '能耗', '用电量',
                '产量', '价格', '库存', '交付量', '功率', '湿度', '扭矩', '寿命', '良率',
                'sales', 'demand', 'temperature', 'target', 'revenue', 'price', 'power')
LABEL_WORDS = ('不良', '是否合格', '合格判定', '故障标签', '缺陷', '标签', '质量结果',
               'is_defect', 'quality_result', 'target_label', 'label', 'outcome')
RESULT_WORDS = ('未来', '事后', '最终结果', '事后确认', '报工', '终检', '后工序',
                'after_', 'future_', 'final_', 'actual_outcome', 'post_')
ID_WORDS = ('编号', '编码', '序列号', '流水号', '订单号', '工单号', '批次号',
            '_id', 'id_', 'uuid', 'guid', 'serial', 'barcode', 'record_id')
TIME_WORDS = ('时间', '日期', '时刻', 'timestamp', 'datetime', 'date', '_at')
# Short, generic verbs don't discriminate targets. Limit lexical matching to
# an explicit user objective; never infer causal importance from correlation.
STOP_WORDS = ('预测', '模型', '分析', '数据', '质量', '字段', '未来', '结果', '多少', '是否')


def _text(value: Any) -> str:
    return str(value or '').strip().lower()


def _contains(name: str, hints: tuple[str, ...]) -> bool:
    return any(word in name for word in hints)


def _matches_goal(name: str, goal: str) -> bool:
    if not goal:
        return False
    # Only concrete entity keywords or a complete column name are accepted.
    if len(name) >= 3 and name in goal:
        return True
    return any(word in name and word in goal and word not in STOP_WORDS
               for word in TARGET_WORDS + LABEL_WORDS)


def describe_fields(profile: dict, goal: str = '') -> dict:
    """Return explainable X/Y, time and leakage proposals for the current source.

    A number-like source field is not automatically useful as an independent
    predictor. Candidate flags are only suitability hints; a supervised task
    still requires an explicit target, features and valid evaluation contract.
    """
    goal = str(goal or '').strip()[:180]
    goal_text = _text(goal)
    fields = profile.get('fields') or []
    roles = []
    for field in fields:
        name = str(field['name'])
        lower = _text(name)
        proposal_roles = {r.get('role') for r in field.get('suggested_roles') or []}
        numeric = float(field.get('sample_numeric_rate') or 0) >= 0.95
        date = ('time' in proposal_roles or float(field.get('sample_datetime_rate') or 0) >= 0.95)
        identifier = (lower in {'id', 'uuid', 'guid', 'row_id', 'sample_id'} or
                      _contains(lower, ID_WORDS) or 'entity' in proposal_roles)
        available = 'available_at' in proposal_roles
        label = _contains(lower, LABEL_WORDS) or 'target_label' in proposal_roles
        future = _contains(lower, RESULT_WORDS)
        target_named = _contains(lower, TARGET_WORDS)
        objective = _matches_goal(lower, goal_text)
        reasons = []
        warnings = []
        if date:
            category = 'time'; reasons.append('日期样本或字段名称符合时间候选')
        elif available:
            category = 'available_at'; reasons.append('字段名称暗示数据可用时刻')
        elif identifier:
            category = 'entity'; reasons.append('字段可能是设备、工单或样本标识')
        elif label:
            category = 'label'; reasons.append('字段名称暗示分类标签，需确认标签取值与生成时刻')
        elif numeric:
            category = 'numeric'; reasons.append('抽样中至少 95% 的非空值可转为数值')
        else:
            category = 'category'; reasons.append('可能需要类别编码，当前建模接口不能直接用原始文本')
        if future:
            warnings.append('名称暗示事后结果；预测时可能尚不可用，需确认产生时刻')
        if identifier:
            warnings.append('标识符不能直接作为数值自变量')
        if date or available:
            warnings.append('时间字段用于时序或可用时间约束，不自动进入数值 X')
        if label:
            warnings.append('可能为监督标签；不可同时用作 X')
        # Only safe numeric candidates can be surfaced as initial X proposals.
        allowed = bool(numeric and not (date or available or identifier or label or future))
        target_score = 0
        if numeric and not (date or available or identifier or future):
            target_score = 0.55 + (0.20 if target_named else 0) + (0.20 if objective else 0)
        if label:
            target_score = 0.65 + (0.20 if objective else 0)
        target_score = round(min(0.95, target_score), 2)
        roles.append({'name': name, 'role': category, 'numeric': bool(numeric),
                      'target_candidate': bool(target_score), 'target_confidence': target_score,
                      'suggest_x': allowed, 'confirmed': bool(field.get('confirmed_role')),
                      'reasons': reasons, 'warnings': warnings,
                      'matches_goal': bool(objective)})
    def rank(candidates):
        return sorted(candidates, key=lambda f: (-f['target_confidence'], f['name']))
    numeric_targets = rank([f for f in roles if f['target_candidate'] and f['numeric']
                            and f['role'] not in ('time','available_at','entity','label')])
    label_targets = rank([f for f in roles if f['role']=='label'])
    numeric_best = numeric_targets[0] if numeric_targets else None
    # A 0.75 lexical match is a good suggestion, never an implicit approval.
    preferred_numeric = (numeric_best['name'] if numeric_best and
                         numeric_best['target_confidence'] >= 0.75 and
                         (len(numeric_targets)==1 or numeric_best['target_confidence'] > numeric_targets[1]['target_confidence'])
                         else None)
    preferred_label = (label_targets[0]['name'] if len(label_targets)==1 else None)
    mapping = profile.get('recommendation') or {}
    has_clock = bool(mapping.get('time'))
    tasks = [
        {'id':'continuous_forecast','title':'时间序列预测','subtitle':'预测未来若干小时 / 天 / 月',
         'route':'modeling','runnable':has_clock and bool(numeric_targets),
         'target':preferred_numeric,'explanation':'需确认时间、预测跨度与特征可用时刻'},
        {'id':'tabular_regression','title':'一般数值预测','subtitle':'每行一个样本，例如成本、价格、工时',
         'route':'featurelab','runnable':bool(numeric_targets),
         'target':preferred_numeric,'explanation':'使用 FeatureLab 已有静态回归评价；需核实样本独立性'},
        {'id':'classification','title':'按时间进行质量分类','subtitle':'在指定工序节点提前识别不良',
         'route':'modeling','runnable':has_clock and bool(label_targets),
         'target':preferred_label,'explanation':'需确认正负标签及标签成熟时点'},
        {'id':'tabular_classification','title':'一般分类','subtitle':'独立样本的合格 / 不合格识别',
         'route':'featurelab','runnable':bool(label_targets),
         'target':preferred_label,'explanation':'使用 FeatureLab 已有静态分类评价；需核实独立性和标签含义'},
    ]
    best = preferred_label if _contains(goal_text, LABEL_WORDS) else preferred_numeric
    return {'version':VERSION, 'business_goal':goal, 'role_candidates':roles,
            'targets':{'numeric':[f['name'] for f in numeric_targets],
                       'classification':[f['name'] for f in label_targets],
                       'preferred_numeric':preferred_numeric, 'preferred_label':preferred_label},
            'suggested_features':[f['name'] for f in roles if f['suggest_x'] and f['name']!=best],
            'tasks':tasks,
            'notes':['业务目标仅参与列名匹配；实际预测目标和 X 必须人工确认',
                     '抽样画像不证明变量有效性；需在同一训练折内检验特征价值',
                     '不能将事后结果、未来数据或标签直接作为预测输入']}
