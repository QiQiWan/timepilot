"""Optional, bounded advanced feature generation (v0.30).

Only verified project-native assets are read. Calculators never receive labels,
Python source, SQL or filesystem paths from an HTTP request. Both event time and
*availability time* are enforced at each prediction cutoff. Outputs are drafts,
not approved feature sets.
"""
from __future__ import annotations
import importlib.util
import math
import time
import hashlib
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.native_bi import quote_identifier


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AdvancedLimits(Strict):
    max_rows: int = Field(default=30_000, ge=100, le=200_000)
    max_pairs: int = Field(default=50_000, ge=100, le=200_000)
    max_features: int = Field(default=80, ge=1, le=256)
    max_seconds: int = Field(default=90, ge=10, le=240)
    max_memory_mb: int = Field(default=512, ge=128, le=2048)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=4)


class TSFreshRecipe(Strict):
    engine: Literal['tsfresh'] = 'tsfresh'
    asset_id: str = Field(min_length=1, max_length=128)
    version_id: str = 'base'
    sample_id_field_id: str
    event_time_field_id: str
    available_at_field_id: str
    prediction_time_field_id: str
    value_field_ids: list[str] = Field(min_length=1, max_length=8)
    preset: Literal['minimal', 'efficient'] = 'minimal'
    min_observations: int = Field(default=2, ge=2, le=200)
    min_distinct_timestamps: int = Field(default=2, ge=2, le=200)
    min_time_span_seconds: float = Field(default=1, gt=0, le=604800)
    min_valid_ratio: float = Field(default=0.70, gt=0, le=1)
    limits: AdvancedLimits = Field(default_factory=AdvancedLimits)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.value_field_ids)) != len(self.value_field_ids):
            raise ValueError('传感器字段不可重复')
        if set(self.value_field_ids) & {self.sample_id_field_id,self.event_time_field_id,
                                       self.available_at_field_id,self.prediction_time_field_id}:
            raise ValueError('传感器列不能包含标识、时间或预测时点')
        return self


class FeaturetoolsRecipe(Strict):
    engine: Literal['featuretools'] = 'featuretools'
    decisions_asset_id: str = Field(min_length=1, max_length=128)
    decisions_version_id: str = 'base'
    events_asset_id: str = Field(min_length=1, max_length=128)
    events_version_id: str = 'base'
    decision_id_field_id: str
    decision_entity_field_id: str
    prediction_time_field_id: str
    event_entity_field_id: str
    event_time_field_id: str
    event_available_at_field_id: str
    event_value_field_ids: list[str] = Field(min_length=1, max_length=8)
    primitives: list[Literal['mean','max','min','std','count','sum']] = Field(default_factory=lambda:['mean','max','min','count'],min_length=1,max_length=6)
    limits: AdvancedLimits = Field(default_factory=AdvancedLimits)

    @model_validator(mode='after')
    def unique(self):
        if self.decisions_asset_id==self.events_asset_id and self.decisions_version_id==self.events_version_id:
            raise ValueError('多表算法至少要求两个不同来源的数据资产')
        if len(set(self.event_value_field_ids))!=len(self.event_value_field_ids):
            raise ValueError('事件数值字段不能重复')
        if len(set(self.primitives))!=len(self.primitives):
            raise ValueError('聚合函数不能重复')
        if set(self.event_value_field_ids)&{self.event_entity_field_id,self.event_time_field_id,
                                           self.event_available_at_field_id}:
            raise ValueError('多表传感器值不能包含事件键或时间列')
        return self


from typing import Union
AdvancedRecipe = Annotated[Union[TSFreshRecipe, FeaturetoolsRecipe], Field(discriminator='engine')]


def capabilities() -> dict:
    names=('tsfresh','featuretools','duckdb','pyarrow','perspective')
    return {'engines':{n:bool(importlib.util.find_spec(n)) for n in names},
            'tsfresh_presets':['minimal','efficient'],
            'featuretools_primitives':['mean','max','min','std','count','sum'],
            'execution':'durable_project_job',
            'protection':['confirmed_roles','source_sha256','as_of_availability',
                          'bounded_rows','bounded_pairs','no_label_inputs','review_before_use']}


def _column(contract, fid: str, role: set[str], *, numeric=False, temporal=False) -> str:
    column=next((f for f in contract.fields if f.field_id==fid),None)
    if column is None:
        raise ValueError('引用了来源契约中不存在的字段：'+str(fid))
    if column.confirmation!='confirmed' or column.role not in role:
        raise ValueError(f'字段 {column.source_name} 的角色未确认或不适用于此操作；要求 {sorted(role)}')
    if column.classification=='restricted':
        raise ValueError('高级自动特征搜索禁止读取 restricted 字段：'+column.source_name)
    if numeric and (column.semantic_type!='numeric' or not any(x in column.physical_type.upper() for x in
            ('DOUBLE','FLOAT','REAL','INT','DECIMAL','HUGEINT'))):
        raise ValueError('算法仅接受经确认的物理数值传感器字段：'+column.source_name)
    if temporal and (column.semantic_type!='datetime' or
                     not any(x in column.physical_type.upper() for x in ('DATE','TIMESTAMP'))):
        raise ValueError('时间字段必须确认并转换为真实 TIMESTAMP：'+column.source_name)
    return column.source_name


def resolve_recipe(recipe, contracts: dict) -> dict:
    """Turn immutable field IDs into bounded SQL projections, never accepting column names from an LLM."""
    if isinstance(recipe,TSFreshRecipe):
        c=contracts['primary']
        return {'engine':'tsfresh','asset_id':recipe.asset_id,'version_id':recipe.version_id,
            'sample':_column(c,recipe.sample_id_field_id,{'entity_key','batch_key','record_id'}),
            'event_time':_column(c,recipe.event_time_field_id,{'event_time'},temporal=True),
            'available_at':_column(c,recipe.available_at_field_id,{'available_at'},temporal=True),
            'cutoff':_column(c,recipe.prediction_time_field_id,{'prediction_time'},temporal=True),
            'values':[_column(c,fid,{'value'},numeric=True) for fid in recipe.value_field_ids],
            'preset':recipe.preset,'timezone':c.timezone,'limits':recipe.limits.model_dump(),
            'min_observations':recipe.min_observations,
            'min_distinct_timestamps':recipe.min_distinct_timestamps,
            'min_time_span_seconds':recipe.min_time_span_seconds,
            'min_valid_ratio':recipe.min_valid_ratio}
    d,e=contracts['primary'],contracts['secondary']
    return {'engine':'featuretools','decisions_asset_id':recipe.decisions_asset_id,
        'decisions_version_id':recipe.decisions_version_id,'events_asset_id':recipe.events_asset_id,
        'events_version_id':recipe.events_version_id,
        'decision_id':_column(d,recipe.decision_id_field_id,{'record_id','batch_key'}),
        'decision_entity':_column(d,recipe.decision_entity_field_id,{'entity_key'}),
        'cutoff':_column(d,recipe.prediction_time_field_id,{'prediction_time'},temporal=True),
        'event_entity':_column(e,recipe.event_entity_field_id,{'entity_key'}),
        'event_time':_column(e,recipe.event_time_field_id,{'event_time'},temporal=True),
        'available_at':_column(e,recipe.event_available_at_field_id,{'available_at'},temporal=True),
        'values':[_column(e,fid,{'value'},numeric=True) for fid in recipe.event_value_field_ids],
        'primitives':recipe.primitives,'decision_timezone':d.timezone,
        'event_timezone':e.timezone,'limits':recipe.limits.model_dump()}


def _parse_utc(column, name: str, timezone: str='UTC'):
    # DuckDB may return naive timestamps. Localize with the *confirmed source*
    # timezone before converting to UTC. utc=True would silently interpret
    # naive Asia/Shanghai measurements as UTC and corrupt cross-asset PIT cuts.
    from zoneinfo import ZoneInfo
    try:
        result=pd.to_datetime(column,errors='coerce')
        if result.isna().any():raise ValueError('空值或无法解析的时间')
        if not isinstance(result.dtype,pd.DatetimeTZDtype):
            result=result.dt.tz_localize(ZoneInfo(timezone or 'UTC'),ambiguous='raise',nonexistent='raise')
        return result.dt.tz_convert('UTC')
    except Exception as exc:
        raise ValueError('无效时间、时区不明确或存在夏令时歧义：'+name) from exc


def _numeric(frame, names):
    for name in names:
        values=pd.to_numeric(frame[name],errors='coerce')
        frame[name]=values.replace([np.inf,-np.inf],np.nan).astype(float)
    return frame


def _check_runtime(start, limit):
    if time.monotonic()-start>limit:
        raise TimeoutError('高级算法超过执行时间预算；未接受部分结果')


def prepare_tsfresh(frame: pd.DataFrame, spec:dict):
    """Exact per-group PIT check; never combine sensors after their decision time."""
    needed=[spec['sample'],spec['event_time'],spec['available_at'],spec['cutoff'],*spec['values']]
    if not set(needed).issubset(frame):raise ValueError('数据源缺少已确认字段')
    if len(frame)>spec['limits']['max_rows']:raise ValueError('原始行数超过当前高级算子预算')
    data=frame[needed].copy()
    for field in (spec['event_time'],spec['available_at'],spec['cutoff']):
        data[field]=_parse_utc(data[field],field,spec.get('timezone','UTC'))
    if data[spec['sample']].isna().any():raise ValueError('样本/设备键有空值')
    cutoffs=data.groupby(spec['sample'])[spec['cutoff']].nunique(dropna=False)
    if (cutoffs!=1).any():raise ValueError('同一样本具有多个预测时点，必须拆分样本键')
    invalid=(data[spec['event_time']]>data[spec['cutoff']])|(data[spec['available_at']]>data[spec['cutoff']])
    # Refuse unsafe records instead of silently leaking them into a feature.
    if invalid.any():raise ValueError(f'检测到 {int(invalid.sum())} 条预测时点之后的事件/晚到记录，请先建立 PIT 过滤视图')
    _numeric(data,spec['values'])
    grouped=data.groupby(spec['sample'],sort=False,dropna=False)
    counts=grouped.size()
    uniques=grouped[spec['event_time']].nunique()
    spans=grouped[spec['event_time']].agg(lambda dates: (dates.max()-dates.min()).total_seconds())
    bad=(counts<int(spec.get('min_observations',2))) | (uniques<int(spec.get('min_distinct_timestamps',2))) | (spans<float(spec.get('min_time_span_seconds',1)))
    ratios=grouped[spec['values']].count().div(counts,axis=0)
    bad=bad | ratios.lt(float(spec.get('min_valid_ratio',.70))).any(axis=1)
    if bad.any():
        examples=[str(x)[:60] for x in bad.index[bad].tolist()[:5]]
        raise ValueError(f'{int(bad.sum())} 个样本未满足最少观测数/独立时间戳/时间跨度/有效值覆盖率：{examples}；请检查采样契约')
    data=data.sort_values([spec['sample'],spec['event_time']],kind='stable')
    return data


def prepare_asof_pairs(decisions:pd.DataFrame, events:pd.DataFrame, spec:dict):
    """PIT-safe two-table as-of join with explicit availability and pair budgets."""
    dcols=[spec['decision_id'],spec['decision_entity'],spec['cutoff']]
    ecols=[spec['event_entity'],spec['event_time'],spec['available_at'],*spec['values']]
    if not set(dcols).issubset(decisions) or not set(ecols).issubset(events):
        raise ValueError('关联表缺少字段契约声明的主键、时间或传感器列')
    d=decisions[dcols].copy();v=events[ecols].copy()
    if len(d)>spec['limits']['max_rows'] or len(v)>spec['limits']['max_rows']:
        raise ValueError('多表输入超过行数预算')
    if d[spec['decision_id']].isna().any() or d[spec['decision_id']].duplicated().any():
        raise ValueError('预测实例主键存在空值或重复')
    if d[spec['decision_entity']].isna().any() or v[spec['event_entity']].isna().any():
        raise ValueError('设备/实体键存在空值，禁止笛卡尔关联')
    d['__cutoff']=_parse_utc(d[spec['cutoff']],spec['cutoff'],spec.get('decision_timezone','UTC'))
    v['__event_time']=_parse_utc(v[spec['event_time']],spec['event_time'],spec.get('event_timezone','UTC'))
    v['__available_at']=_parse_utc(v[spec['available_at']],spec['available_at'],spec.get('event_timezone','UTC'))
    # Only aggregate measurement fields declared as physically numeric. Never
    # pass through target labels, partition markers or unconfirmed categories.
    _numeric(v,spec['values'])
    d['__join_entity']=d[spec['decision_entity']].astype(str)
    v['__join_entity']=v[spec['event_entity']].astype(str)
    # Preflight exact fanout before materializing a potentially explosive join.
    left_counts=d['__join_entity'].value_counts()
    right_counts=v['__join_entity'].value_counts()
    overlap=left_counts.index.intersection(right_counts.index)
    possible_pairs=sum(int(left_counts[k])*int(right_counts[k]) for k in overlap)
    if possible_pairs>spec['limits']['max_pairs']:
        raise ValueError('关联预计产生 '+str(possible_pairs)+' 条候选对，超过预算，已在执行 JOIN 前阻断')
    if d[spec['decision_id']].astype(str).duplicated().any():
        raise ValueError('预测实例主键在字符串规范化后重复，请核实编码与类型')
    d=d.rename(columns={spec['decision_id']:'__decision_id'})
    joined=d[['__decision_id','__join_entity','__cutoff']].merge(
        v[['__join_entity','__event_time','__available_at',*spec['values']]],
        on='__join_entity',how='inner',sort=False)
    if len(joined)>spec['limits']['max_pairs']:
        raise ValueError('关联候选对数超过预算，请先按时间/设备缩小范围')
    ready=joined[(joined['__event_time']<=joined['__cutoff'])&
                 (joined['__available_at']<=joined['__cutoff'])].copy()
    if (ready['__event_time']>ready['__cutoff']).any() or (ready['__available_at']>ready['__cutoff']).any():
        raise AssertionError('PIT 关联过滤失效')
    ready=ready.reset_index(drop=True)
    ready['__event_row_id']=np.arange(len(ready),dtype=np.int64)
    parent=d[['__decision_id','__cutoff']].copy()
    parent['__decision_id']=parent['__decision_id'].astype(str)
    ready['__decision_id']=ready['__decision_id'].astype(str)
    # Featuretools numeric aggregations should receive only numeric event values.
    child=ready[['__event_row_id','__decision_id','__event_time',*spec['values']]]
    parent=parent.rename(columns={'__cutoff':'__decision_time'})
    return parent,child,{'candidate_pairs':len(joined),'eligible_pairs':len(ready),
                         'late_or_future_excluded':len(joined)-len(ready)}


def tsfresh_calculator_count(preset:str, value_count:int):
    """Exact count from the installed tsfresh FCParameters, or unknown (None)."""
    try:
        from tsfresh.feature_extraction import MinimalFCParameters, EfficientFCParameters
    except (ImportError, ModuleNotFoundError):
        return None
    settings = MinimalFCParameters() if preset=='minimal' else EfficientFCParameters()
    return int(value_count*sum(len(params) if params is not None else 1
                       for params in settings.values()))


def advanced_feature_budget(engine:str, spec:dict):
    """Only exact tsfresh count is labeled exact; DFS count is an upper bound."""
    if engine=='tsfresh':
        count=tsfresh_calculator_count(spec['preset'],len(spec['values']))
        return {'estimated_max_features':count,'estimate_kind':'exact' if count is not None else 'requires_installed_tsfresh'}
    # One-level time-safe history aggregations: count may be emitted once per
    # relationship, rather than once for each numeric column.
    numeric_agg=sum(p!='count' for p in spec['primitives'])
    upper=len(spec['values'])*numeric_agg+int('count' in spec['primitives'])
    return {'estimated_max_features':upper,'estimate_kind':'conservative_upper_bound_validate_actual_dfs'}


def _clean_output(frame:pd.DataFrame,max_features:int):
    frame=frame.reset_index(drop=True)
    frame.columns=[str(x) for x in frame.columns]
    if len([x for x in frame.columns if x not in ('__sample_id','__decision_id','__candidate_cutoff_utc')])>max_features:
        raise ValueError('生成列数超过预算：请使用更小的算子集合')
    for col in frame.select_dtypes(include='number').columns:
        frame[col]=frame[col].replace([np.inf,-np.inf],np.nan)
    return frame


def run_tsfresh(frame, spec):
    from tsfresh import extract_features
    from tsfresh.feature_extraction import MinimalFCParameters,EfficientFCParameters
    start=time.monotonic()
    data=prepare_tsfresh(frame,spec)
    values=spec['values'];key=spec['sample']
    renamed={name:f'sensor_{i}' for i,name in enumerate(values)}
    data=data[[key,spec['event_time'],*values]].rename(columns=renamed)
    settings=MinimalFCParameters() if spec['preset']=='minimal' else EfficientFCParameters()
    # Fail fast on wide/numerous configurations; no unlimited default extraction.
    count=tsfresh_calculator_count(spec['preset'],len(values))
    if count>spec['limits']['max_features']:
        raise ValueError('该预设预计超过最大列数，请减少传感器字段或选择 minimal')
    output=extract_features(data,column_id=key,column_sort=spec['event_time'],
        default_fc_parameters=settings,n_jobs=1,disable_progressbar=True,
        show_warnings=False,impute_function=None)
    _check_runtime(start,spec['limits']['max_seconds'])
    output=output.reset_index().rename(columns={key:'__sample_id'})
    # The exact decision cutoff is part of the artifact provenance, never a model feature.
    cutoff_map=prepare_tsfresh(frame,spec).groupby(key,sort=False)[spec['cutoff']].first().reset_index()
    cutoff_map=cutoff_map.rename(columns={key:'__sample_id',spec['cutoff']:'__candidate_cutoff_utc'})
    output=output.merge(cutoff_map,on='__sample_id',how='left',validate='one_to_one')
    return _clean_output(output,spec['limits']['max_features']),{'input_rows':len(data),
        'sample_count':len(output),'fields':len(output.columns)-1,'pit':'verified_per_sample'}


def run_featuretools(decisions,events,spec):
    import featuretools as ft
    start=time.monotonic()
    parent,child,pit=prepare_asof_pairs(decisions,events,spec)
    if child.empty:raise ValueError('PIT 过滤后没有任何可用事件')
    es=ft.EntitySet(id='timepilot_advanced')
    es=es.add_dataframe(dataframe_name='decisions',dataframe=parent,index='__decision_id',
                        time_index='__decision_time')
    es=es.add_dataframe(dataframe_name='events',dataframe=child,index='__event_row_id',
                        time_index='__event_time')
    es=es.add_relationship('decisions','__decision_id','events','__decision_id')
    cutoffs=parent.rename(columns={'__decision_id':'instance_id','__decision_time':'time'})
    matrix,defs=ft.dfs(entityset=es,target_dataframe_name='decisions',
         cutoff_time=cutoffs,agg_primitives=spec['primitives'],trans_primitives=[],
         max_depth=2,n_jobs=1,cutoff_time_in_index=False,
         verbose=False)
    _check_runtime(start,spec['limits']['max_seconds'])
    available=[col for col in matrix.columns if col not in ('__decision_time',)]
    # Preserve only generated aggregations: identity columns can carry no predictive information.
    available=[c for c in available if '(' in str(c) and ')' in str(c)]
    if len(available)>spec['limits']['max_features']:
        raise ValueError('实际生成列数超过预算')
    output=matrix[available].reset_index().rename(columns={'__decision_id':'__decision_id'})
    output=parent[['__decision_id','__decision_time']].merge(output,on='__decision_id',how='left',validate='one_to_one')
    output=output.rename(columns={'__decision_time':'__candidate_cutoff_utc'})
    return _clean_output(output,spec['limits']['max_features']),{
       **pit,'decision_rows':len(parent),'feature_count':len(available),
       'pit':'verified_per_decision_event_and_availability'}
