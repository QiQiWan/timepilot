"""Read-only temporal lineage audit for existing FeatureSet v2 recipes.

A saved historical recipe may still be executable using __tp_rowid. That does
not establish a real time axis or causal correctness. Audits never rewrite an
immutable recipe and never imply a full-source numerical validation.
"""
from __future__ import annotations

from timepilot.feature_contracts import DataContract, FeatureSpecV2, digest
from timepilot.feature_topology import needs_row_order, infer_topology, _ENTITY_NAME


def temporal_recipe_audit(spec: FeatureSpecV2, contract: DataContract) -> dict:
    """Explain unsafe or ambiguous ordering using only signed schema evidence."""
    fields={f.field_id:f for f in contract.fields}
    topology=infer_topology(contract)
    issues=[]
    inspected=0
    for feature in spec.features:
        if not needs_row_order(feature.operator_id):
            continue
        inspected+=1
        params=feature.parameters or {}
        order_id=params.get('order_field_id')
        parts=params.get('partition_field_ids', contract.entity_field_ids)
        if order_id is None and spec.task and spec.task.mode=='supervised':
            order_id=contract.event_time_field_id  # Compiler's supervised fallback.
        if order_id is None:
            issues.append({'feature_id':feature.feature_id,'output_name':feature.output_name,
                'code':'implicit_import_row_order','severity':'high',
                'message':'窗口默认按导入行号排序；无法证明该顺序代表实际观测先后'})
        elif order_id not in fields or fields[order_id].confirmation!='confirmed':
            issues.append({'feature_id':feature.feature_id,'output_name':feature.output_name,
                'code':'unconfirmed_sort_key','severity':'high',
                'message':'排序键不存在或未经确认'})
        elif (fields[order_id].role=='order_index' and not str(fields[order_id].physical_type).upper().startswith(
                    ('INT','BIGINT','SMALLINT','TINYINT','DOUBLE','FLOAT','DECIMAL','NUMERIC','UINTEGER','UBIGINT'))):
            issues.append({'feature_id':feature.feature_id,'output_name':feature.output_name,
                'code':'invalid_sort_type','severity':'high','message':'记录顺序字段不是可用数值类型'})
        if not isinstance(parts,list) or set(parts)!=set(contract.entity_field_ids):
            issues.append({'feature_id':feature.feature_id,'output_name':feature.output_name,
                'code':'partition_mismatch','severity':'high',
                'message':'窗口分组键与已确认设备/批次实体键不一致'})
        if any(fields.get(x) is None or fields[x].confirmation!='confirmed' for x in (parts or [])):
            issues.append({'feature_id':feature.feature_id,'output_name':feature.output_name,
                'code':'unconfirmed_partition','severity':'high','message':'设备/批次分组字段未经确认'})
    if inspected and not contract.entity_field_ids and any(
        f.role in ('entity_key','batch_key') or bool(_ENTITY_NAME.search(f.source_name))
        for f in contract.fields):
        issues.append({'feature_id':None,'output_name':None,
            'code':'possible_entity_mixing','severity':'high',
            'message':'疑似多设备记录，但数据契约没有已确认分组键'})
    risk=('high' if issues else 'not_proven' if inspected else 'no_temporal_features')
    return {'schema_version':1,'risk':risk,'source_sha256':contract.source_sha256,
        'contract_revision':contract.revision,'spec_sha256':digest(spec),
        'window_feature_count':inspected,'issues':issues,
        'can_publish':risk!='high',
        'validation_scope':'schema_only_no_source_rows_verified',
        'topology_hint':{'order_field_id':topology['order_field_id'],
                         'partition_field_ids':topology['partition_field_ids']},
        'next_action':('复制为新草稿，在字段契约中确认顺序与实体键，重新计算并验证全量数据。'
                      if issues else '正式物化前仍需完成全量键和数据可用性检查。')}
