"""Authoritative overview metrics contract. Generated JSON Schema is shipped for clients."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DashboardMetricScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field_ids: list[str]
    rows: int = Field(ge=0)
    denominator_cells: int = Field(ge=0)
    population: Literal['filtered_version_rows'] = 'filtered_version_rows'
    missing_policy: Literal['sql_null_plus_non_finite_plus_conversion_failed'] = 'sql_null_plus_non_finite_plus_conversion_failed'
    field_limit: int = Field(ge=1)


class DashboardMetrics(BaseModel):
    model_config = ConfigDict(extra='forbid')
    records: int = Field(ge=0)
    source_records: int = Field(ge=0)
    columns: int = Field(ge=0)
    numeric_fields: int = Field(ge=0)
    selected_fields: int = Field(ge=0)
    missing_cells: int = Field(ge=0)
    invalid_numeric_cells: int = Field(ge=0)
    non_finite_numeric_cells: int = Field(ge=0)
    conversion_failed_cells: int = Field(ge=0)
    missing_percent: float | None = Field(default=None, ge=0, le=100)
    coverage_percent: float | None = Field(default=None, ge=0, le=100)
    confirmed_time: bool
    metric_scope: DashboardMetricScope

    @model_validator(mode='after')
    def validate_scope(self):
        scope=self.metric_scope
        if self.selected_fields != len(scope.field_ids) or scope.rows != self.records:
            raise ValueError('统计范围与已选字段或行数不一致')
        if scope.denominator_cells != self.records * self.selected_fields:
            raise ValueError('缺失率分母与统计范围不一致')
        if self.invalid_numeric_cells != self.non_finite_numeric_cells + self.conversion_failed_cells:
            raise ValueError('无效数值的分类计数不一致')
        if self.missing_cells + self.invalid_numeric_cells > scope.denominator_cells:
            raise ValueError('缺失值计数超过已选统计单元格')
        if (scope.denominator_cells == 0) != (self.missing_percent is None):
            raise ValueError('没有样本或字段时缺失率必须为 null')
        if self.records > self.source_records:
            raise ValueError('筛选记录数超过全量记录数')
        return self


def normalized_metrics(payload: dict) -> dict:
    return DashboardMetrics.model_validate(payload).model_dump(mode='json')
