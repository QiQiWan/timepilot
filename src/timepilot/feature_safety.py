"""Fail-closed contract and point-in-time admissibility for supervised FeatureLab."""
from __future__ import annotations
import json
from timepilot.feature_contracts import DataContract,TaskSpecV2,FeatureSpecV2,digest


def ensure_supervised_contract(contract:DataContract,task:TaskSpecV2):
    if task.mode!='supervised':return
    if contract.state!='confirmed' or not contract.timezone:
        raise ValueError('监督评价要求已确认、带时区的时间契约')
    if not contract.entity_field_ids or not contract.event_time_field_id or not contract.available_at_field_id:
        raise ValueError('监督评价必须确认实体、事件时间和业务可用时间')
    fields={f.field_id:f for f in contract.fields}
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
        raise ValueError('物化结果的时间安全证据与当前任务不一致')
    if artifact_metadata.get('source_sha256')!=spec.source.source_sha256:
        raise ValueError('物化结果与源资产指纹不一致')
    return pit
