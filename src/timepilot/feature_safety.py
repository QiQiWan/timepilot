"""Fail-closed contract and point-in-time admissibility for supervised FeatureLab."""
from __future__ import annotations
import json
from timepilot.feature_contracts import DataContract,TaskSpecV2,FeatureSpecV2,digest


def ensure_supervised_contract(contract:DataContract,task:TaskSpecV2):
    if task.mode!='supervised':return
    if contract.state!='confirmed':
        raise ValueError('监督评价要求已确认的数据契约')
    fields={f.field_id:f for f in contract.fields}
    if task.split_kind in ('kfold','stratified','group'):
        label=fields.get(task.label_field_id)
        if label is None or label.confirmation!='confirmed' or label.role!='label' or label.semantic_type!='numeric':
            raise ValueError('静态监督目标必须是经过确认的数值标签字段')
        if task.task_type=='regression' and task.split_kind=='stratified':
            raise ValueError('连续回归目标不适用未分箱的分层 K 折；请选择普通 K 折或分组 K 折')
        if not task.static_independence_confirmed:
            raise ValueError('缺少静态样本独立性与决策时可用性确认')
        if set(task.entity_field_ids)!=set(contract.entity_field_ids):
            raise ValueError('静态监督任务实体范围必须与已确认的数据契约完全一致，不能绕过分组隔离')
        for fid in task.group_field_ids:
            field=fields.get(fid)
            if field is None or field.confirmation!='confirmed' or field.role not in ('entity_key','batch_key'):
                raise ValueError('静态分组字段必须是已确认的设备或批次键')
        return
    if not contract.timezone:
        raise ValueError('时间监督评价要求已确认的时区')
    if not contract.entity_field_ids or not contract.event_time_field_id or not contract.available_at_field_id:
        raise ValueError('时间监督评价必须确认实体、事件时间和业务可用时间')
    for key,fid in [('事件时间',contract.event_time_field_id),('业务可用时间',contract.available_at_field_id),
                    ('预测时刻',task.prediction_time_field_id),('标签可用时间',task.label_available_at_field_id)]:
        f=fields.get(fid)
        if f is None or f.semantic_type!='datetime' or f.confirmation!='confirmed' or not any(
            t in f.physical_type.upper() for t in ('TIMESTAMP','DATE')):
            raise ValueError(f'{key}必须指向已确认的真实日期时间字段：{fid}')
        if f.timezone and f.timezone!=contract.timezone:
            raise ValueError(f'{key}时区冲突：{fid}')
    if task.label_field_id not in fields:
        raise ValueError('监督任务标签字段不存在')
    if task.label_field_id in {contract.event_time_field_id,contract.available_at_field_id,
                               task.prediction_time_field_id,task.label_available_at_field_id}:
        raise ValueError('时间或数据可用时间字段不能作为预测标签')
    if set(task.entity_field_ids)!=set(contract.entity_field_ids):
        raise ValueError('监督任务实体范围与数据契约不一致')
    if task.label_time_field_id:
        f=fields.get(task.label_time_field_id)
        if f is None or f.semantic_type!='datetime' or f.confirmation!='confirmed':
            raise ValueError('标签发生时间尚未确认')


def ensure_pit_evidence(spec:FeatureSpecV2,task:TaskSpecV2,artifact_metadata:dict,contract:DataContract):
    """An exploration artifact can never be relabeled as supervised evidence."""
    ensure_supervised_contract(contract,task)
    if spec.task is None or spec.task.mode!='supervised':
        raise ValueError('探索性特征产物没有监督 PIT 证据；请绑定当前任务并重新计算')
    if digest(spec.task)!=digest(task):
        raise ValueError('特征产物与监督评价的任务/决策时点不一致，必须重新计算')
    pit=artifact_metadata.get('pit') or {}
    if not isinstance(pit,dict) or pit.get('status')!='verified' or pit.get('scope')!='full':
        raise ValueError('物化结果缺少全量监督 PIT 验证证据')
    if pit.get('task_sha256')!=digest(task) or pit.get('contract_revision')!=spec.contract_revision:
        raise ValueError('物化结果的任务安全证据与当前任务不一致')
    if task.split_kind in ('kfold','stratified','group'):
        if pit.get('validation_kind')!='static_row_local' or pit.get('point_in_time_applicable') is not False:
            raise ValueError('静态监督产物缺少逐行计算与非时间任务的独立核验证据')
    elif pit.get('validation_kind') not in (None,'temporal_point_in_time'):
        raise ValueError('时间监督产物的 PIT 核验类型不匹配')
    if artifact_metadata.get('source_sha256')!=spec.source.source_sha256:
        raise ValueError('物化结果与源资产指纹不一致')
    return pit
