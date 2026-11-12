"""Stable acyclic feature dependencies and type-safe field resolution."""
from __future__ import annotations
from timepilot.feature_contracts import FeatureSpecV2, DataContract
from timepilot.feature_operator_registry import validate_operator


def ordered_features(spec:FeatureSpecV2,contract:DataContract):
    if spec.source.asset_id!=contract.asset_id or spec.source.version_id!=contract.version_id or spec.source.source_sha256!=contract.source_sha256:
        raise ValueError('特征源与数据契约指纹不一致')
    if spec.contract_revision!=contract.revision:raise ValueError('特征引用的数据契约已更新，请重新核验')
    lookup={f.field_id:f for f in contract.fields}
    feature_map={f.feature_id:f for f in spec.features}
    original_names={f.source_name for f in contract.fields}
    for feature in spec.features:
        if feature.output_name in original_names:raise ValueError('新特征与已有源字段重名：'+feature.output_name)
        validate_operator(feature,supervised=bool(spec.task and spec.task.mode=='supervised'))
        for key in feature.input_field_ids:
            if key not in lookup and key not in feature_map:raise ValueError('不存在的字段或特征 ID：'+key)
        for key in [feature.parameters.get('order_field_id'),*(feature.parameters.get('partition_field_ids') or [])]:
            if key and key not in lookup:raise ValueError('分区/排序字段必须引用源字段 ID')
    if spec.task and spec.task.mode=='supervised':
        field_ids=set(lookup)
        required=[spec.task.prediction_time_field_id,spec.task.label_field_id,
                  spec.task.label_available_at_field_id,*spec.task.entity_field_ids,*spec.task.group_field_ids]
        if any(f not in field_ids for f in required):raise ValueError('监督任务引用未授权或不存在的字段')
        if not contract.event_time_field_id or not contract.available_at_field_id:
            raise ValueError('监督特征需要已确认的事件与可用时间数据契约')
        # Decision time may be later than event time. The materialization
        # engine verifies event_time <= decision_time and available_at <=
        # decision_time over every row; historical windows remain ordered by
        # confirmed event time within entity boundaries.
    visited=set();active=set();output=[]
    def visit(key):
        if key in visited:return
        if key in active:raise ValueError('特征依赖图存在循环')
        active.add(key)
        feature=feature_map[key]
        for dep in feature.input_field_ids:
            if dep in feature_map:visit(dep)
        active.remove(key);visited.add(key);output.append(feature)
    for key in feature_map:visit(key)
    return output
