"""Predeclared held-out-device / held-out-regime evaluation (v0.35.2).

Only explicitly nominated group values are used as the final validation
partition. Selection, imputation and fitting see training rows exclusively.
This is an *independent group* check, optionally also a chronological check;
results must never feed back into candidate ranking or parameter tuning.
"""
from __future__ import annotations

from typing import Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator
import numpy as np
import pandas as pd



class IndustrialTransferPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    kind: Literal['unseen_device','unseen_regime']
    group_field_id: str = Field(min_length=5)
    holdout_values: list[str] = Field(min_length=1,max_length=6)
    # A real dated cutoff is required for time-task transfer; without it the
    # report only supports group-disjoint generalization, not future accuracy.
    time_cutoff: str | None = None
    min_train_rows: int = Field(default=40,ge=40,le=100000)
    min_holdout_rows: int = Field(default=20,ge=15,le=100000)

    @model_validator(mode='after')
    def verify(self):
        if any(not isinstance(x,str) or not x.strip() or len(x)>120 for x in self.holdout_values):
            raise ValueError('留出设备/工况值不能为空或过长')
        if len(self.holdout_values)!=len(set(self.holdout_values)):
            raise ValueError('留出组重复')
        if self.time_cutoff is not None:
            try:
                stamp=datetime.fromisoformat(self.time_cutoff.replace('Z','+00:00'))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError('missing timezone')
            except (TypeError,ValueError) as exc:
                raise ValueError('留出时间截断必须包含明确时区，例如 2026-01-01T00:00:00Z') from exc
        return self


def transfer_partition(data, contract, task, policy):
    """Predeclare the holdout WITHOUT examining labels or feature scores.

    The same immutable partition is used for the outer CV *and* final test.
    This prevents held-out devices from influencing feature selection via CV.
    """
    fields={f.field_id:f for f in contract.fields}
    target=fields.get(policy.group_field_id)
    role='regime' if policy.kind=='unseen_regime' else 'entity_key'
    if (target is None or target.confirmation!='confirmed' or target.role!=role
        or (policy.kind=='unseen_regime' and target.semantic_type!='category')
        or target.classification=='restricted'):
        raise ValueError('留出字段未确认、受限，或与设备/工况泛化类型不匹配')
    if policy.kind=='unseen_device' and policy.group_field_id not in contract.entity_field_ids:
        raise ValueError('未见设备验证必须使用已确认的实体分组键')
    group_col=target.source_name
    if group_col not in data:
        raise ValueError('留出分组字段在实际物化数据中不存在')
    groups=data[group_col].astype('string')
    if groups.isna().any():raise ValueError('留出分组包含缺失，无法验证训练/测试隔离')
    is_holdout=groups.isin(policy.holdout_values).to_numpy(dtype=bool)
    if not is_holdout.any() or is_holdout.all():
        raise ValueError('留出组不存在，或没有其他可训练组')
    static=task.split_kind in ('kfold','stratified','group')
    if not static and not policy.time_cutoff:
        raise ValueError('时序任务的独立设备/工况验证必须锁定真实时间截断')
    train_mask=~is_holdout
    test_mask=is_holdout.copy()
    cutoff=None
    if policy.time_cutoff:
        if not task.prediction_time_field_id:
            raise ValueError('静态样本缺少可信预测时间，不能声称按未来日期外推')
        time_name=fields[task.prediction_time_field_id].source_name
        times=pd.to_datetime(data[time_name],utc=True,errors='coerce')
        if times.isna().any():raise ValueError('预测时点存在非法日期')
        cutoff=pd.to_datetime(policy.time_cutoff,utc=True)
        train_mask=train_mask & (times<cutoff).to_numpy(dtype=bool)
        test_mask=test_mask & (times>=cutoff).to_numpy(dtype=bool)
        if task.label_available_at_field_id:
            available_name=fields[task.label_available_at_field_id].source_name
            available=pd.to_datetime(data[available_name],utc=True,errors='coerce')
            if available.isna().any():raise ValueError('训练标签可用时间不完整')
            train_mask=train_mask & (available<=cutoff).to_numpy(dtype=bool)
    train_idx=np.flatnonzero(train_mask)
    test_idx=np.flatnonzero(test_mask)
    if len(train_idx)<policy.min_train_rows or len(test_idx)<policy.min_holdout_rows:
        raise ValueError('独立留出设备/工况数据不足，不能报告泛化收益')
    if set(train_idx) & set(test_idx):raise ValueError('独立留出行与训练行重叠')
    if any(group in set(groups.iloc[train_idx]) for group in policy.holdout_values):
        raise ValueError('被留出的设备/工况进入训练集')
    return train_idx,test_idx,cutoff


def evaluate_industrial_transfer(data, matrix, labels, base, outputs, contract,
                                 task, protocol, *, source_sha256, spec_sha256):
    from timepilot.feature_contracts import digest
    policy=protocol.industrial_transfer
    if policy is None:return None
    from timepilot.feature_evaluation import _fit_predict,_metrics
    from timepilot.feature_selection import select_training_features
    train_idx,test_idx,cutoff=transfer_partition(data,contract,task,policy)
    if task.task_type=='binary_classification' and len(np.unique(labels[train_idx]))<2:
        raise ValueError('训练组缺少二分类的全部类别')
    nbase=len(base)
    selection=select_training_features(matrix[train_idx],labels[train_idx],base,outputs,
        protocol.selection,task_type=task.task_type) if protocol.selection.enabled else {
            'scope':'locked_group_training_only','selected_indices':list(range(nbase,matrix.shape[1])),
            'selected_features':list(outputs)}
    chosen=[*range(nbase),*selection['selected_indices']]
    variants={'raw_only':list(range(nbase)), 'with_features':list(range(matrix.shape[1])),
              'selected_features':chosen}
    by_model={}
    for family in protocol.models:
        metrics={}
        for variant,columns in variants.items():
            pred=_fit_predict(matrix[train_idx][:,columns],labels[train_idx],
                              matrix[test_idx][:,columns],task.task_type,family,seed=protocol.seed)
            metrics[variant]=_metrics(labels[test_idx],pred,task.task_type)
        by_model[family]=metrics
    return {
        'status':'measured','kind':policy.kind,'scope':('group_and_future_time' if cutoff is not None else 'group_disjoint_only'),
        'group_field_id':policy.group_field_id,'holdout_values':policy.holdout_values,
        'time_cutoff':cutoff.isoformat() if cutoff is not None else None,
        'train_rows':int(len(train_idx)),'holdout_rows':int(len(test_idx)),
        'train_rows_sha256':digest(train_idx.tolist()),'holdout_rows_sha256':digest(test_idx.tolist()),
        'manifest_sha256':digest({'source_sha256':source_sha256,'spec_sha256':spec_sha256,
            'task':task,'policy':policy,'train':train_idx.tolist(),'holdout':test_idx.tolist()}),
        'model_metrics':by_model,'selected_features':selection['selected_features'],
        'training_selection_only':True,'train_holdout_disjoint':True,
        'upstream_candidate_scope':'not_proven_train_only',
        'evidence_level':'locked_holdout_after_predeclared_feature_spec',
        'interpretation':('事先指定设备/工况和时间截断的独立留出对照。不得根据留出指标重新调整候选、阈值或算法。'
            if cutoff is not None else '只验证未见设备/工况的组隔离，不能解释为未来时间外推。'),
        'limitations':['特征配方可能已使用全数据的无监督画像，不等于完全盲法的最终测试；须在候选生成前冻结留出分组',
                       '若留出组是看过结果后挑选的，此测试不具备独立最终验证的统计意义',
                       '模型效果不代表机理因果关系；上线前仍需工艺工程师核验']}
