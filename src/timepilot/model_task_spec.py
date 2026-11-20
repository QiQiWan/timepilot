"""Immutable-source, four-family supervised modeling task contract.

A contract is a reviewable specification, not evidence of successful training.
The server exclusively binds source hashes and column schemas. All edits must
re-run preflight and invalidate prior approval. Validation is fail-closed.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

KINDS = ('tabular_regression', 'tabular_classification', 'continuous_forecast', 'classification')
TEMPORAL_KINDS = ('continuous_forecast', 'classification')
CLASSIFICATION_KINDS = ('tabular_classification', 'classification')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, allow_inf_nan=False)


class AssetRef(Strict):
    asset_id: str = Field(min_length=1, max_length=150)
    version_id: str = Field(default='base', min_length=1, max_length=150)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    native_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    columns_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class TargetRole(Strict):
    column: str = Field(min_length=1, max_length=200)
    horizon_steps: int = Field(default=1, ge=1, le=168)
    positive_values: list[str] = Field(default_factory=list, max_length=16)
    negative_values: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def label_values(self):
        if set(self.positive_values) & set(self.negative_values):
            raise ValueError('Positive and negative target labels must be disjoint')
        return self


class FeatureRole(Strict):
    column: str = Field(min_length=1, max_length=200)
    role: Literal['static', 'observed_past', 'known_future'] = 'static'
    available_at_column: str | None = Field(default=None, max_length=200)


class TemporalRole(Strict):
    event_time_column: str | None = Field(default=None, max_length=200)
    decision_time_column: str | None = Field(default=None, max_length=200)
    label_available_at_column: str | None = Field(default=None, max_length=200)
    label_maturity_lag_seconds: int | None = Field(default=None, ge=0, le=31536000)
    feature_available_at_column: str | None = Field(default=None, max_length=200)
    publication_lag_days: int | None = Field(default=None, ge=0, le=3650)
    entity_columns: list[str] = Field(default_factory=list, max_length=4)
    timezone: str = Field(default='Asia/Shanghai', min_length=2, max_length=64)
    frequency: str | None = Field(default=None, max_length=20)


class ValidationRole(Strict):
    kind: Literal['kfold', 'stratified_kfold', 'group_kfold', 'temporal_holdout',
                  'rolling_origin', 'grouped_temporal_holdout', 'leave_month_out', 'leave_device_out'] = 'kfold'
    folds: int = Field(default=5, ge=2, le=24)
    gap: int = Field(default=0, ge=0, le=100000)


class ReviewFlags(Strict):
    target_reviewed: bool = False
    feature_availability_reviewed: bool = False
    split_reviewed: bool = False


class ModelTaskInput(Strict):
    name: str = Field(min_length=2, max_length=120)
    asset_id: str = Field(min_length=1, max_length=150)
    version_id: str = Field(default='base', min_length=1, max_length=150)
    business_goal: str = Field(default='', max_length=180)
    task_kind: Literal['tabular_regression', 'tabular_classification',
                       'continuous_forecast', 'classification']
    target: TargetRole
    features: list[FeatureRole] = Field(default_factory=list, max_length=32)
    time: TemporalRole = Field(default_factory=TemporalRole)
    validation: ValidationRole = Field(default_factory=ValidationRole)
    confirmations: ReviewFlags = Field(default_factory=ReviewFlags)

    @model_validator(mode='after')
    def compatible(self):
        columns = [field.column for field in self.features]
        if not columns and self.task_kind != 'continuous_forecast':
            raise ValueError('This task family requires at least one independent X')
        if len(set(columns)) != len(columns):
            raise ValueError('Duplicate X columns are not allowed')
        if self.target.column in columns:
            raise ValueError('Y cannot also be an X column')
        if any(f.available_at_column == self.target.column for f in self.features):
            raise ValueError('Y cannot be used as any X publication clock')
        if any(f.available_at_column in columns for f in self.features):
            raise ValueError('An X measurement cannot be any X publication clock')
        time_cols = [self.time.event_time_column, self.time.decision_time_column,
                     self.time.label_available_at_column, self.time.feature_available_at_column]
        if any(x in set(time_cols + self.time.entity_columns) for x in columns):
            raise ValueError('X cannot be used as a time, label-maturity or entity field')
        if self.target.column in time_cols or self.target.column in self.time.entity_columns:
            raise ValueError('Y cannot also be the clock, availability or entity column')
        if len(set(self.time.entity_columns)) != len(self.time.entity_columns):
            raise ValueError('Duplicate entity columns')
        if self.task_kind == 'classification' and not (
                self.time.label_available_at_column or
                self.time.label_maturity_lag_seconds is not None):
            raise ValueError('Early classification needs a confirmed label maturity timestamp or lag')
        if self.task_kind in TEMPORAL_KINDS:
            if not self.time.event_time_column:
                raise ValueError('Temporal tasks require an event time field')
            permitted=('temporal_holdout','rolling_origin','grouped_temporal_holdout')
            if self.task_kind=='classification':
                permitted=(*permitted,'leave_device_out')
            if self.validation.kind not in permitted:
                raise ValueError('Temporal tasks require an available forward-only validation protocol')
        elif self.validation.kind in ('temporal_holdout','rolling_origin','grouped_temporal_holdout','leave_month_out'):
            if not self.time.event_time_column:
                raise ValueError('Temporal split requires a clock')
        if self.validation.kind in ('group_kfold','grouped_temporal_holdout','leave_device_out') and not self.time.entity_columns:
            raise ValueError('Grouped validation requires entity columns')
        if self.validation.gap and self.validation.kind in ('kfold','stratified_kfold','group_kfold'):
            raise ValueError('Embargo gap is only supported for forward chronological validation')
        if self.validation.kind == 'leave_device_out' and not self.time.event_time_column:
            raise ValueError('Device transfer holdout requires an event time')
        if self.task_kind in CLASSIFICATION_KINDS and self.target.horizon_steps != 1:
            raise ValueError('Classification does not support numeric forecast horizons')
        if self.task_kind in ('tabular_regression','tabular_classification') and any(
                feature.role != 'static' for feature in self.features):
            raise ValueError('Static models require static X roles')
        if self.task_kind in TEMPORAL_KINDS and any(
                feature.role == 'static' for feature in self.features):
            raise ValueError('Temporal X must be observed_past or known_future')
        return self


class ModelTaskSpec(ModelTaskInput):
    schema_version: Literal['1.0'] = '1.0'
    project_id: str = Field(min_length=1)
    asset: AssetRef

    @model_validator(mode='after')
    def same_asset(self):
        if self.asset_id != self.asset.asset_id or self.version_id != self.asset.version_id:
            raise ValueError('Source binding differs from task input')
        return self


class ReplaceModelTask(Strict):
    expected_revision: int = Field(ge=1)
    spec: ModelTaskInput


class ApproveModelTask(Strict):
    expected_revision: int = Field(ge=1)
    confirm_target: bool = False
    confirm_feature_availability: bool = False
    confirm_split: bool = False


def canonical_sha(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()
