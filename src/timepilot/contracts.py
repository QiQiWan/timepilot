"""Versioned public data contracts. Changes require contract tests and migration policy."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "1.0.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)

    @field_validator("schema_version", check_fields=False)
    @classmethod
    def enforce_contract_major(cls, value: str) -> str:
        if not value.startswith("1."):
            raise ValueError("Unsupported contract major version; migration required")
        return value


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["blocker", "warning", "info"]
    message: str
    column: str | None = None
    row: int | None = None
    suggested_action: str | None = None


class FieldSpec(StrictModel):
    name: str
    role: Literal["entity", "timestamp", "target", "available_at", "observed", "known_future"]
    dtype: str = "string"
    unit: str | None = None
    description: str | None = None


class DatasetManifest(StrictModel):
    schema_version: str = CONTRACT_VERSION
    dataset_id: str
    name: str
    source_name: str
    source_sha256: str
    snapshot_sha256: str
    created_at: datetime
    frequency: str = "MS"
    source_timezone: str = "UTC"
    timestamp_semantics: Literal["period_start", "instant"] = "period_start"
    unit: str = "unit"
    record_count: int = Field(ge=0)
    series_count: int = Field(ge=0)
    columns: list[FieldSpec]
    task_kind: Literal["continuous_forecast", "classification"] = "continuous_forecast"
    feature_columns: list[str] = Field(default_factory=list)
    modeling_contract: dict = Field(default_factory=dict)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class EvaluationSpec(StrictModel):
    protocol_id: str = "rolling_holdout_v1"
    validation_windows: int = Field(default=3, ge=1, le=24)
    test_horizon: int = Field(default=1, ge=1, le=24)
    primary_metric: Literal["WAPE", "MAE"] = "WAPE"
    min_history: int = Field(default=12, ge=2, le=10000)


class TaskSpec(StrictModel):
    schema_version: str = CONTRACT_VERSION
    task_id: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    task_family: Literal["demand_forecast", "equipment_state"] = "demand_forecast"
    task_kind: Literal["continuous_forecast", "classification"] = "continuous_forecast"
    feature_columns: list[str] = Field(default_factory=list)
    modeling_contract: dict = Field(default_factory=dict)
    classification_min_recall: float = Field(default=0.9, gt=0, le=1)
    classification_val_fraction: float = Field(default=0.2, ge=0.1, le=0.35)
    classification_test_fraction: float = Field(default=0.2, ge=0.1, le=0.35)
    classification_min_positive: tuple[int,int,int] = (5,2,2)
    target: str = "target"
    prediction_origin: datetime | None = None
    mechanism_refs: list[str] = Field(default_factory=list)
    entity_keys: list[str] = Field(default_factory=lambda: ["series_id"])
    frequency: Literal["MS", "D", "h"] = "MS"
    forecast_horizon: int = Field(default=1, ge=1, le=24)
    seasonal_period: int | None = Field(default=None, ge=1, le=365)
    model_candidates: list[str] = Field(default_factory=lambda: ["seasonal_naive", "moving_average"])
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    training_budget_seconds: int = Field(default=180, ge=10, le=86400)
    approved: bool = True

    @field_validator("model_candidates")
    @classmethod
    def candidates_non_empty(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("model_candidates must be nonempty and unique")
        return value

    @model_validator(mode="after")
    def check_horizon(self) -> TaskSpec:
        if self.task_kind == "classification" and self.classification_val_fraction + self.classification_test_fraction >= .6:
            raise ValueError("Classification validation/test fractions must total less than 0.6")
        if self.evaluation.test_horizon != self.forecast_horizon:
            raise ValueError("evaluation.test_horizon must match forecast_horizon in v0.1")
        return self


class ForecastRecord(StrictModel):
    schema_version: str = CONTRACT_VERSION
    run_id: str
    model_id: str
    series_id: str
    prediction_origin: datetime
    target_timestamp: datetime
    horizon_step: int = Field(ge=1)
    mean: float
    mase_scale: float | None = None
    actual: float | None = None
    unit: str = "unit"
    split: Literal["validation", "test", "future"]


class QualityManifest(StrictModel):
    schema_version: str = CONTRACT_VERSION
    run_id: str
    dataset_id: str
    protocol_id: str
    model_id: str
    status: Literal["passed", "review_required", "failed", "insufficient_evidence"]
    metrics: dict[str, float | None]
    validation_metrics: dict[str, float | None]
    slice_metrics: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunManifest(StrictModel):
    schema_version: str = CONTRACT_VERSION
    run_id: str
    task_id: str
    dataset_id: str
    dataset_snapshot_sha256: str
    task_snapshot_sha256: str
    created_at: datetime
    python_version: str
    platform: str
    package_versions: dict[str, str] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)


class ModelManifest(StrictModel):
    schema_version: str = CONTRACT_VERSION
    run_id: str
    series_id: str
    plugin_id: str
    plugin_version: str
    relative_artifact_path: str
    relative_history_path: str
    history_sha256: str


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
