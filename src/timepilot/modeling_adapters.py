"""Auditable industrial table -> causal observations adapter (v0.18).

The adapter never invents values, future-known columns, or physical constraints.
Every derived time bucket records source counts, completeness, and availability.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.data import DataValidationError, normalize_frame


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, allow_inf_nan=False)


class ResamplingSpec(Strict):
    enabled: bool = False
    source_period_seconds: int = Field(default=60, ge=1, le=86400)
    aggregation: Literal['mean', 'last', 'min', 'max'] = 'mean'
    min_coverage: float = Field(default=1.0, ge=0.5, le=1.0)
    # No silent missing bucket interpolation or partial-interval extrapolation.
    require_complete_timeline: bool = True


class EventSpec(Strict):
    column: str | None = None
    truthy_values: list[str] = Field(default_factory=list, max_length=20)
    include_age: bool = True
    reset_policy: Literal['indicator_only', 'exclude_cross_event_windows'] = 'indicator_only'


class RegimeSpec(Strict):
    column: str | None = None
    max_categories: int = Field(default=8, ge=2, le=16)


class MechanismSpec(Strict):
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    target_min: float | None = None
    target_max: float | None = None
    max_step_change: float | None = Field(default=None, gt=0)
    enforcement: Literal['block', 'warn'] = 'block'

    @model_validator(mode='after')
    def check_bounds(self):
        if self.target_min is not None and self.target_max is not None and self.target_min >= self.target_max:
            raise ValueError('物理上下界必须严格递增')
        return self


class ClassificationSpec(Strict):
    positive_values: list[str] = Field(default_factory=lambda: ['1'], min_length=1, max_length=10)
    negative_values: list[str] = Field(default_factory=lambda: ['0'], min_length=1, max_length=10)
    label_available_at: str | None = None
    label_delay_seconds: int | None = Field(default=None, ge=0, le=31536000)
    decision_stage: str = Field(default='节点A', min_length=1, max_length=80)
    min_recall: float = Field(default=0.9, gt=0, le=1)
    test_fraction: float = Field(default=0.2, ge=0.1, le=0.35)
    validation_fraction: float = Field(default=0.2, ge=0.1, le=0.35)
    min_positive_train: int = Field(default=5, ge=2, le=1000)
    min_positive_validation: int = Field(default=2, ge=1, le=1000)
    min_positive_test: int = Field(default=2, ge=1, le=1000)

    @model_validator(mode='after')
    def check_labels(self):
        if set(self.positive_values) & set(self.negative_values):
            raise ValueError('正负类取值不能重叠')
        if self.test_fraction + self.validation_fraction >= 0.6:
            raise ValueError('验证集和测试集总比例必须小于 60%')
        if self.label_available_at is None and self.label_delay_seconds is None:
            # The evidence of label maturity is mandatory in classification.
            raise ValueError('分类必须明确标签可用时间列或标签延迟秒数')
        if self.label_available_at is not None and self.label_delay_seconds is not None:
            raise ValueError('标签可用时间只能采用一种来源')
        return self


def parse_timestamp(series: pd.Series, timezone: str) -> pd.Series:
    """Parse local/aware ISO timestamps without coercing numeric steps to dates."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try: ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc: raise ValueError('数据时区无效') from exc
    if pd.api.types.is_numeric_dtype(series):
        raise ValueError('整型步骤索引不能直接作为物理时间戳')
    dt = pd.to_datetime(series.astype('string'), errors='coerce', format='mixed', utc=False)
    if dt.dtype == 'object':
        raise ValueError('日期格式或 UTC 偏移混合，需先统一')
    if isinstance(dt.dtype, pd.DatetimeTZDtype):
        return dt.dt.tz_convert('UTC')
    return dt.dt.tz_localize(timezone, ambiguous='NaT', nonexistent='NaT').dt.tz_convert('UTC')


def _observed_mapping(features: list[str]) -> dict[str,str]:
    return {f'obs_{idx+1:02d}':col for idx,col in enumerate(features)}


def prepare_observations(frame: pd.DataFrame, *, mapping, plan) -> tuple[pd.DataFrame, dict]:
    """Convert a selected immutable raw table into verified causal modeling rows.

    Accepted `mapping` and `plan` are v0.18 strict Pydantic models. No hidden
    forward-filling. Feature and event availability follow the maximum source
    availability in a time bucket; labels are never used as predictors.
    """
    if frame.empty: raise ValueError('筛选后没有记录')
    if not mapping.time or not mapping.target: raise ValueError('缺少时间字段或目标字段')
    required=[mapping.time,mapping.target]+([mapping.entity] if mapping.entity else [])
    feature_map=_observed_mapping(mapping.observed_features)
    required+=list(mapping.observed_features)
    if mapping.event.column: required.append(mapping.event.column)
    if mapping.regime.column: required.append(mapping.regime.column)
    if mapping.availability_policy=='source_column': required.append(mapping.available_at)
    if plan.task_kind=='classification':
        if plan.classification.label_available_at:required.append(plan.classification.label_available_at)
    missing=[col for col in required if col not in frame.columns]
    if missing: raise ValueError('缺少已指定的源字段：'+','.join(missing))
    if len(set(required))!=len(required):
        # In particular no label may also be a known predictor feature.
        raise ValueError('字段角色存在重复引用：标签、时间、特征必须不同')
    tmp=pd.DataFrame(index=frame.index)
    tmp['series_id']=(frame[mapping.entity].astype('string') if mapping.entity else pd.Series('series_1',index=frame.index,dtype='string'))
    if tmp['series_id'].isna().any() or tmp['series_id'].str.strip().eq('').any():
        raise ValueError('实体标识不可为空')
    tmp['timestamp']=parse_timestamp(frame[mapping.time],mapping.timezone)
    if tmp['timestamp'].isna().any(): raise ValueError('时间字段包含不合法、夏令时歧义或缺失值')
    if mapping.availability_policy=='source_column':
        tmp['available_at']=parse_timestamp(frame[mapping.available_at],mapping.timezone)
    elif mapping.availability_policy=='declared_lag':
        if mapping.publication_lag_days is None:raise ValueError('未明确声明数据可用时间延迟')
        tmp['available_at']=tmp['timestamp']+pd.to_timedelta(mapping.publication_lag_days,unit='D')
    else:
        raise ValueError('数据可用时间未确认')
    if tmp['available_at'].isna().any():raise ValueError('数据可用时间存在无效值')
    for feature,clock_column in getattr(mapping,'feature_available_at',{}).items():
        if feature not in mapping.observed_features:raise ValueError('特征时间声明不对应任何输入特征')
        if clock_column not in frame.columns:raise ValueError('特征时间字段不存在：'+clock_column)
        feature_times=parse_timestamp(frame[clock_column],mapping.timezone)
        if feature_times.isna().any():raise ValueError('特征可用时间存在无效值：'+feature)
        # A shared row availability is promoted to the latest declared input time.
        tmp['available_at']=pd.concat([tmp['available_at'],feature_times],axis=1).max(axis=1)
    kind=plan.task_kind
    if kind=='classification':
        import re
        high_risk=('标签','结果','最终','不良','判定','label','outcome','future','after')
        for feature in mapping.observed_features:
            if any(word in feature.lower() for word in high_risk) and feature not in mapping.feature_available_at:
                raise ValueError('疑似未来质量结果列须明确提供该特征可用时间：'+feature)
        spec=plan.classification
        if spec is None: raise ValueError('未设置分类标签契约')
        value=frame[mapping.target].astype('string').str.strip()
        classes={str(x):1 for x in spec.positive_values}
        classes.update({str(x):0 for x in spec.negative_values})
        tmp['target']=pd.to_numeric(value.map(classes),errors='coerce')
        if tmp['target'].isna().any(): raise ValueError('分类标签出现未确认取值、缺失值或空值')
        if spec.label_available_at:
            tmp['label_available_at']=parse_timestamp(frame[spec.label_available_at],mapping.timezone)
        else:
            tmp['label_available_at']=tmp['timestamp']+pd.to_timedelta(spec.label_delay_seconds,unit='s')
        if tmp['label_available_at'].isna().any(): raise ValueError('标签可用时间包含无效值')
        if (tmp['label_available_at']<tmp['timestamp']).any():
            raise ValueError('分类标签不能在判断时刻之前已经确定；需核实标签时间语义')
        if (tmp['available_at']>tmp['timestamp']).any():
            raise ValueError('分类任务中至少一条特征在判断时刻后才可用，禁止使用未来传感器')
    else:
        tmp['target']=pd.to_numeric(frame[mapping.target],errors='coerce')
    if tmp['target'].isna().any() or not np.isfinite(tmp['target'].to_numpy(dtype=float)).all():
        raise ValueError('目标字段包含缺失或非有限值')
    for code,src in feature_map.items():
        tmp[code]=pd.to_numeric(frame[src],errors='coerce')
        if tmp[code].isna().any() or not np.isfinite(tmp[code].to_numpy(dtype=float)).all():
            raise ValueError('历史特征包含缺失或非数值：'+src)
    if getattr(mapping,'batch',None):
        tmp['batch_key']=frame[mapping.batch].astype('string')
        if tmp['batch_key'].isna().any() or tmp['batch_key'].str.strip().eq('').any():
            raise ValueError('批次标识存在空值')
    if mapping.event.column:
        raw_event=frame[mapping.event.column].astype('string').fillna('').str.strip()
        truth=set(mapping.event.truthy_values)
        if not truth:raise ValueError('维护事件必须确认触发取值')
        unknown=raw_event[~raw_event.isin(truth)&raw_event.ne('')]
        if not unknown.empty:raise ValueError('事件列包含未确认的事件类型')
        tmp['event_flag']=raw_event.isin(truth).astype(int)
    if mapping.regime.column:
        tmp['_regime_raw']=frame[mapping.regime.column].astype('string').fillna('').str.strip()
        if tmp['_regime_raw'].eq('').any():raise ValueError('工况字段存在空值')
    if plan.resampling.enabled and kind=='classification':
        raise ValueError('逐件/逐节点分类不得把标签聚合到时间桶')
    if plan.resampling.enabled:
        tmp,audit=_resample(tmp,plan.resampling,plan.frequency,mapping.timezone)
    else:
        duplicates=tmp.duplicated(['series_id','timestamp'])
        if kind!='classification' and duplicates.any():
            raise ValueError(f'原始记录存在 {int(duplicates.sum())} 个重复实体-时间戳，需明确去重规则')
        audit={'enabled':False,'raw_rows':len(tmp),'output_rows':len(tmp),'buckets':[]}
    if '_regime_raw' in tmp:
        types=sorted(tmp['_regime_raw'].unique().tolist())
        if len(types)>mapping.regime.max_categories:raise ValueError('工况类别超过明确配置的上限')
        for i,key in enumerate(types):tmp[f'regime_{i+1:02d}']=(tmp['_regime_raw']==key).astype(int)
        audit['regime_dictionary']={f'regime_{i+1:02d}':str(key) for i,key in enumerate(types)}
        tmp=tmp.drop(columns=['_regime_raw'])
    tmp=tmp.sort_values(['series_id','timestamp'],kind='stable').reset_index(drop=True)
    if 'event_flag' in tmp and mapping.event.include_age:
        ages=[]
        for _,group in tmp.groupby('series_id',sort=False):
            last=None
            for idx,flag in enumerate(group['event_flag']):
                ages.append(-1 if last is None else idx-last)
                if flag:last=idx
        tmp['event_age_steps']=ages
    if plan.mechanism.target_min is not None:
        if (tmp['target']<plan.mechanism.target_min).any():
            if plan.mechanism.enforcement=='block':raise ValueError('目标观测低于已确认的机理下界')
            audit['mechanism_warning_min']=int((tmp['target']<plan.mechanism.target_min).sum())
    if plan.mechanism.target_max is not None:
        if (tmp['target']>plan.mechanism.target_max).any():
            if plan.mechanism.enforcement=='block':raise ValueError('目标观测高于已确认的机理上界')
            audit['mechanism_warning_max']=int((tmp['target']>plan.mechanism.target_max).sum())
    if plan.mechanism.max_step_change is not None and kind!='classification':
        jumps=tmp.groupby('series_id')['target'].diff().abs()
        if 'event_flag' in tmp:
            jumps=jumps.mask(tmp['event_flag'].eq(1))
        count=int((jumps>plan.mechanism.max_step_change).sum())
        if count and plan.mechanism.enforcement=='block':raise ValueError(f'目标有 {count} 个时间步超出机理变化率上界')
        audit['step_change_violations']=count
    # All derived columns are immutable and explicitly declared in the manifest.
    numeric=[x for x in tmp.columns if x.startswith(('obs_','regime_','event_'))]
    audit.update({'feature_columns':numeric,'feature_source_map':feature_map,
                  'event_column':mapping.event.column,'regime_column':mapping.regime.column,
                  'mechanism':plan.mechanism.model_dump(mode='json')})
    for col in ('timestamp','available_at','label_available_at'):
        if col in tmp:tmp[col]=tmp[col].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    return tmp,audit


def _resample(frame:pd.DataFrame,spec:ResamplingSpec,frequency:str,timezone:str) -> tuple[pd.DataFrame,dict]:
    if frequency not in ('h','D'):
        raise ValueError('可审计重采样当前只支持分钟/秒到小时或天')
    seconds={'h':3600,'D':86400}[frequency]
    if seconds%spec.source_period_seconds:
        raise ValueError('原始周期必须整除目标时间桶长度')
    expected=seconds//spec.source_period_seconds
    out=[];issues=[];audit_rows=[]
    columns=[col for col in frame.columns if col.startswith('obs_')]
    for entity,group in frame.groupby('series_id',sort=True):
        group=group.sort_values('timestamp')
        if group['timestamp'].duplicated().any():raise ValueError(f'{entity}: 原始时间戳重复，无法安全重采样')
        intervals=group['timestamp'].diff().dropna().dt.total_seconds()
        if (intervals < spec.source_period_seconds).any():
            raise ValueError(f'{entity}: 实际采样频率高于声明频率，存在潜在重复观测')
        # A missing source reading cannot be hidden by bucket aggregation.
        if frequency=='D':
            bucket=group['timestamp'].dt.tz_convert(timezone).dt.floor('D').dt.tz_convert('UTC')
        else:
            bucket=group['timestamp'].dt.floor(frequency)
        for t,part in group.groupby(bucket,sort=True):
            count=len(part)
            bucket_end=(t.tz_convert(timezone)+pd.DateOffset(days=1)).tz_convert('UTC') if frequency=='D' else t+pd.Timedelta(seconds=seconds)
            actual_seconds=int((bucket_end-t).total_seconds())
            if actual_seconds!=seconds:
                issues.append(f'{entity}/{t}: 夏令时产生非固定长度的日桶，现有引擎不支持自动对齐')
            coverage=count/expected
            interval_start=t
            positions=((part['timestamp']-interval_start).dt.total_seconds()/spec.source_period_seconds)
            if not np.allclose(positions,positions.round(),atol=1e-7):
                issues.append(f'{entity}/{t}: 原始采样点未对齐声明周期')
            if positions.round().duplicated().any():issues.append(f'{entity}/{t}: 原始采样周期重复')
            if coverage<spec.min_coverage or coverage>1:
                issues.append(f'{entity}/{t}: 桶覆盖率 {coverage:.1%}（{count}/{expected}）不达标')
            row={'series_id':entity,'timestamp':t,
                 'available_at':max(part['available_at'].max(),bucket_end)}
            for col in ['target',*columns]:
                if spec.aggregation=='last':val=part[col].iloc[-1]
                else:val=getattr(part[col],spec.aggregation)()
                row[col]=float(val)
            if 'event_flag' in part:row['event_flag']=int(part['event_flag'].max())
            if '_regime_raw' in part:
                values=part['_regime_raw'].unique()
                if len(values)!=1:issues.append(f'{entity}/{t}: 工况在桶内发生变化，不能假定整个时间桶工况一致')
                else:row['_regime_raw']=values[0]
            out.append(row)
            audit_rows.append({'series_id':str(entity),'bucket_start':t.isoformat(),
                               'input_rows':count,'expected_rows':expected,'coverage':round(coverage,6),
                               'bucket_end':bucket_end.isoformat(),
                               'available_at':row['available_at'].isoformat()})
        if spec.require_complete_timeline:
            actual=sorted(bucket.unique())
            needed=pd.date_range(actual[0],actual[-1],freq=frequency,tz='UTC')
            if len(actual)!=len(needed):issues.append(f'{entity}: 重采样后的时间桶存在缺口')
    if issues:raise ValueError('重采样拒绝：'+'；'.join(issues[:8]))
    audit={'enabled':True,'source_period_seconds':spec.source_period_seconds,
           'target_frequency':frequency,'bucket_timezone':timezone if frequency=='D' else 'UTC',
           'aggregation':spec.aggregation,'min_coverage':spec.min_coverage,
           'raw_rows':int(len(frame)),'output_rows':len(out),'buckets':audit_rows,
           'event_aggregation':'bucket_any' if 'event_flag' in frame else None,
           'regime_aggregation':'constant_or_reject' if '_regime_raw' in frame else None}
    return pd.DataFrame(out),audit
