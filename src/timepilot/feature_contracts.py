"""FeatureLab 2.0 versioned, typed contracts. Source bytes never change.

Schema constraints are structural. Authorization, point-in-time admissibility,
operator capabilities and published source fingerprints are checked by services.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_iana_timezone(value: str | None) -> str | None:
    if value is None:return None
    if not value or (value not in ('UTC','Etc/UTC') and '/' not in value):
        raise ValueError('时区须为明确的 IANA 名称，如 Asia/Shanghai 或 UTC')
    try:ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError,KeyError) as exc:
        raise ValueError('无效的 IANA 时区：'+value) from exc
    return value


def canonical(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def stable_field_id(asset_id: str, version_id: str, index: int, source_name: str) -> str:
    raw = f'{asset_id}\x00{version_id}\x00{index}\x00{source_name}'
    return 'fld_' + hashlib.sha256(raw.encode()).hexdigest()[:24]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class FieldContract(StrictModel):
    field_id: str = Field(min_length=5, max_length=100)
    source_name: str
    source_column_index: int = Field(ge=0)
    physical_type: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=160)
    semantic_type: Literal['unknown','numeric','string','datetime','category','id','boolean','event','target'] = 'unknown'
    role: Literal['unspecified','entity_key','event_time','available_at','ingested_at','prediction_time','value','regime','label','batch_key','record_id'] = 'unspecified'
    unit: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=80)
    source_location: str | None = Field(default=None, max_length=240)
    classification: Literal['public','internal','restricted'] = 'internal'
    confirmation: Literal['unconfirmed','proposed','confirmed'] = 'unconfirmed'
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def ensure_datetime_type(self):
        validate_iana_timezone(self.timezone)
        if self.semantic_type == 'datetime' and not any(x in self.physical_type.upper() for x in ('DATE','TIMESTAMP')):
            raise ValueError('字段实际类型尚未转换成日期时间，不能人为标记为真实时间')
        if self.role in ('event_time','available_at','prediction_time') and self.semantic_type!='datetime':
            raise ValueError('时间角色必须绑定日期时间类型字段')
        return self


class DataContract(StrictModel):
    schema_version: Literal[1] = 1
    asset_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    source_sha256: str = Field(min_length=32, max_length=128)
    revision: int = Field(default=0, ge=0)
    state: Literal['detected','needs_review','confirmed','stale'] = 'detected'
    fields: list[FieldContract] = Field(min_length=1)
    entity_field_ids: list[str] = Field(default_factory=list)
    event_time_field_id: str | None = None
    available_at_field_id: str | None = None
    timezone: str | None = None
    duplicate_policy: Literal['reject','keep_all_by_rowid','aggregate','latest'] = 'reject'

    @model_validator(mode='after')
    def validate_links(self):
        validate_iana_timezone(self.timezone)
        ids = [f.field_id for f in self.fields]
        positions = [f.source_column_index for f in self.fields]
        if len(ids) != len(set(ids)) or len(positions) != len(set(positions)):
            raise ValueError('字段 ID 或原始列序号重复')
        for ref in [*self.entity_field_ids, self.event_time_field_id, self.available_at_field_id]:
            if ref is not None and ref not in ids:
                raise ValueError('数据契约引用不存在的字段 ID')
        by_id = {f.field_id: f for f in self.fields}
        for role, fid in [('event_time', self.event_time_field_id),
                          ('available_at', self.available_at_field_id)]:
            if fid is None:
                continue
            field = by_id[fid]
            if (field.semantic_type != 'datetime'
                or not any(t in field.physical_type.upper() for t in ('TIMESTAMP', 'DATE'))
                or field.confirmation != 'confirmed'):
                raise ValueError(f'{role} 必须绑定已确认的真实日期时间字段：{fid}')
            if not self.timezone:
                raise ValueError(f'{role} 需要确认数据时区：{fid}')
            if field.timezone and field.timezone != self.timezone:
                raise ValueError(f'{role} 字段时区与契约时区不一致：{fid}')
        for fid in self.entity_field_ids:
            if by_id[fid].semantic_type == 'datetime':
                raise ValueError(f'实体键不得指向时间字段：{fid}')
        return self


class SourceRef(StrictModel):
    asset_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    source_sha256: str = Field(min_length=32, max_length=128)


class TaskSpecV2(StrictModel):
    task_spec_version: Literal[1] = 1
    task_id: str = Field(min_length=1, max_length=120)
    mode: Literal['exploration','supervised'] = 'exploration'
    task_type: Literal['profile','regression','binary_classification'] = 'profile'
    entity_field_ids: list[str] = Field(default_factory=list)
    prediction_time_field_id: str | None = None
    label_field_id: str | None = None
    label_time_field_id: str | None = None
    label_available_at_field_id: str | None = None
    split_kind: Literal['time','grouped_time'] = 'time'
    group_field_ids: list[str] = Field(default_factory=list)
    n_splits: int = Field(default=3, ge=2, le=10)
    purge_rows: int = Field(default=0, ge=0, le=100000)
    metrics: list[str] = Field(default_factory=lambda: ['recall','precision','pr_auc'])

    @model_validator(mode='after')
    def time_guard(self):
        if self.mode == 'supervised':
            if not self.prediction_time_field_id or not self.label_field_id or not self.label_available_at_field_id:
                raise ValueError('监督任务必须具有决策时点、标签列及标签可用时间')
            if self.task_type == 'profile':
                raise ValueError('监督任务必须指定预测或分类目标')
            if self.split_kind == 'grouped_time' and not self.group_field_ids:
                raise ValueError('分组时间切分要求实体或批次分组字段')
        return self


OUTPUT_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')

class FeatureDefinition(StrictModel):
    feature_id: str = Field(min_length=1, max_length=100)
    output_name: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=160)
    operator_id: str = Field(min_length=3, max_length=80)
    operator_version: str = Field(default='2.0', max_length=20)
    input_field_ids: list[str] = Field(min_length=1, max_length=8)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def output_safe(self):
        if not OUTPUT_NAME.fullmatch(self.output_name) or self.output_name == '__tp_rowid':
            raise ValueError('输出特征技术名非法')
        return self


class FeatureSpecV2(StrictModel):
    schema_version: Literal[2] = 2
    name: str = Field(min_length=1, max_length=120)
    source: SourceRef
    contract_revision: int = Field(ge=0)
    task: TaskSpecV2 | None = None
    features: list[FeatureDefinition] = Field(min_length=1, max_length=100)
    selection_policy: Literal['full','preview_only'] = 'full'

    @model_validator(mode='after')
    def identifiers(self):
        ids=[f.feature_id for f in self.features]
        names=[f.output_name for f in self.features]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError('特征 ID 或输出列名重复')
        return self


class QualitySelectionPolicy(StrictModel):
    enabled: bool = True
    max_missing_fraction: float = Field(default=0.60, ge=0.0, le=0.99)
    min_std: float = Field(default=1e-10, ge=0.0, le=1.0)
    max_abs_correlation: float = Field(default=0.985, ge=0.50, le=1.0)
    max_selected: int = Field(default=24, ge=1, le=100)
    min_pair_observations: int = Field(default=16, ge=3, le=1000)
    rank_method: Literal['univariate','quality_only'] = 'univariate'


class AutoSearchPolicy(StrictModel):
    enabled: bool = False
    max_candidates: int = Field(default=32, ge=1, le=96)
    max_model_fits: int = Field(default=8, ge=1, le=24)
    max_seconds_per_fold: float = Field(default=25.0, ge=2.0, le=120.0)
    max_winners: int = Field(default=2, ge=1, le=8)
    min_gain: float = Field(default=0.0, ge=0, le=1.0)


class SlicePolicy(StrictModel):
    enabled: bool = True
    field_ids: list[str] = Field(default_factory=list, max_length=3)
    min_rows: int = Field(default=20, ge=10, le=1000)
    min_positives: int = Field(default=3, ge=1, le=100)
    max_groups: int = Field(default=12, ge=2, le=25)


class EvaluationProtocol(StrictModel):
    task: TaskSpecV2
    set_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    baseline: Literal['raw_numeric'] = 'raw_numeric'
    seed: int = 42
    max_rows: int = Field(default=500000, ge=100, le=2000000)
    min_relative_improvement: float = Field(default=0.01, ge=0.0, le=0.25)
    selection: QualitySelectionPolicy = Field(default_factory=QualitySelectionPolicy)
    search: AutoSearchPolicy = Field(default_factory=AutoSearchPolicy)
    slices: SlicePolicy = Field(default_factory=SlicePolicy)
    models: list[Literal['linear','forest']] = Field(default_factory=lambda: ['linear'], min_length=1, max_length=2)

    @model_validator(mode='after')
    def validate_settings(self):
        if len(self.models) != len(set(self.models)):
            raise ValueError('评价模型不能重复')
        if self.task.mode != 'supervised' and (self.search.enabled or 'forest' in self.models):
            raise ValueError('无标签探索任务不支持有监督自动搜索或模型拟合')
        if self.search.enabled and self.task.split_kind == 'grouped_time':
            raise ValueError('分组时间切分暂不支持自动搜索：内层验证必须另行实施独立设备/批次分组以防止泄漏')
        return self


class PlanRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    asset_id: str = Field(min_length=1)
    version_id: str = 'base'
    request_id: str = Field(min_length=1, max_length=120)
    task: TaskSpecV2 | None = None


class FeatureRunRecord(StrictModel):
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    set_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    source_sha256: str = Field(min_length=32)
    spec_sha256: str = Field(min_length=32)
    status: Literal['queued','running','cancel_requested','cancelled',
        'succeeded','failed_retryable','failed_terminal']
    artifact_id: str | None = None
    progress: float = Field(default=0.0,ge=0,le=1)


class PublishedFeatureManifest(StrictModel):
    manifest_version: Literal[1] = 1
    feature_set_id: str = Field(min_length=1)
    set_revision: int = Field(ge=1)
    source_sha256: str = Field(min_length=32)
    feature_spec_sha256: str = Field(min_length=32)
    artifact_sha256: str = Field(min_length=32)
    approved_by: str = Field(min_length=1)
    approved_at: str
    feature_ids: list[str] = Field(min_length=1)
    model_references: list[str] = Field(default_factory=list)
