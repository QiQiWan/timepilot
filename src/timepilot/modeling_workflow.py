"""Project-owned, version-pinned general-table -> forecast engine bridge.

No raw SQL, direct filesystem paths, arbitrary code or opaque LLM instructions are
accepted from callers. Saved visual/pivot/sample state is *not* a training rowset.
The existing immutable general CSV is the canonical input for a base revision;
non-base native BI revisions are read through the versioned DuckDB overlay.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.contracts import EvaluationSpec, TaskSpec
from timepilot.data import DataValidationError, create_dataset, create_modeling_dataset, get_dataset, normalize_frame
from timepilot.modeling_adapters import (ResamplingSpec, EventSpec, RegimeSpec, MechanismSpec,
                                          ClassificationSpec, prepare_observations)
from timepilot.industrial_plugins import temporal_classification_split
from timepilot.generic_assets import GenericAssetStore
from timepilot.data_platform import DataPlatform
from timepilot.native_bi import FilterSpec, NativeBIError, QuerySpec, compile_query
from timepilot.projects import ProjectError, now
from timepilot.storage import atomic_json, sha256_file, sha256_bytes, load_json

MODEL_MAX_ROWS = 200_000
PROFILE_SAMPLE_ROWS = 2400
PROFILE_VERSION = 'general-profile-v018-1'
ROLE_HINTS = {
    'time': ('timestamp', 'datetime', 'date', '采集时间', '记录时间', '日期', '时刻', '时间戳'),
    'entity': ('device', 'machine', 'equipment', '设备编号', '设备号', '产线编号', '车型', '序列号'),
    'target': ('temperature', 'temp', '温度', '销量', '用电量', '压力', '振动', '负压'),
    'available_at': ('available_at', 'published_at', '数据可用', '发布时间', '入库时间'),
    'event': ('event', 'maintenance', '维修', '保养', '事件', '故障'),
    'label': ('label', 'is_defect', 'quality_result', '不良', '是否合格', '缺陷标签'),
}
SUPPORTED = {'MS', 'D', 'h'}


class ModelingError(ProjectError):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True, allow_inf_nan=False)


class FieldMapping(Strict):
    time: str | None = None
    entity: str | None = None
    target: str | None = None
    target_unit: str | None = Field(default=None, max_length=32)
    available_at: str | None = None
    availability_policy: Literal['source_column', 'declared_lag', 'unconfirmed'] = 'unconfirmed'
    publication_lag_days: int | None = Field(default=None, ge=0, le=3650)
    timezone: str = Field(default='Asia/Shanghai', min_length=2, max_length=64)
    observed_features: list[str] = Field(default_factory=list, max_length=32)
    event: EventSpec = Field(default_factory=EventSpec)
    regime: RegimeSpec = Field(default_factory=RegimeSpec)
    batch: str | None = None
    feature_available_at: dict[str,str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def compatible(self):
        if self.availability_policy == 'source_column' and not self.available_at:
            raise ValueError('选择源列可用时间时必须指定 available_at 字段')
        if self.availability_policy == 'declared_lag' and self.publication_lag_days is None:
            raise ValueError('推断可用时间必须明确确认发布延迟，不能隐式设为零')
        if len(set(self.observed_features)) != len(self.observed_features):
            raise ValueError('输入特征不能重复')
        return self


class ForecastPlan(Strict):
    task_kind: Literal['continuous_forecast', 'classification', 'anomaly_detection', 'mechanism_learning'] = 'continuous_forecast'
    task_family: Literal['demand_forecast', 'equipment_state'] = 'demand_forecast'
    frequency: str = Field(default='h', min_length=1, max_length=32)
    horizon_steps: int = Field(default=3, ge=1, le=24)
    validation_windows: int = Field(default=3, ge=1, le=24)
    min_history: int = Field(default=24, ge=2, le=10000)
    primary_metric: Literal['MAE', 'WAPE'] = 'MAE'
    model_candidates: list[str] = Field(default_factory=lambda: ['last_value', 'moving_average'], min_length=1, max_length=8)
    duplicate_policy: Literal['reject'] = 'reject'
    gap_policy: Literal['reject'] = 'reject'
    feature_policy: Literal['target_history_only','historical_observed','snapshot_at_decision'] = 'target_history_only'
    resampling: ResamplingSpec = Field(default_factory=ResamplingSpec)
    mechanism: MechanismSpec = Field(default_factory=MechanismSpec)
    classification: ClassificationSpec | None = None

    @model_validator(mode='after')
    def candidates(self):
        if self.task_kind == 'classification' and self.classification is None:
            raise ValueError('分类任务必须设置正负标签、标签成熟时间和评估协议')
        if self.task_kind == 'classification' and self.feature_policy != 'snapshot_at_decision':
            raise ValueError('分类任务使用决策时刻已知特征的快照')
        if self.task_kind == 'continuous_forecast' and self.feature_policy == 'snapshot_at_decision':
            raise ValueError('连续预测不能使用分类的特征策略')
        if len(set(self.model_candidates)) != len(self.model_candidates):
            raise ValueError('模型插件不能重复')
        return self


class CreateModelingDraft(Strict):
    asset_id: str
    version_id: str = 'base'
    view_id: str | None = None
    selection_id: str | None = None
    filters: list[FilterSpec] | None = Field(default=None, max_length=12)
    name: str = Field(min_length=2, max_length=120)
    mapping: FieldMapping = Field(default_factory=FieldMapping)
    plan: ForecastPlan = Field(default_factory=ForecastPlan)
    confirm_scope: bool = False


class EditModelingDraft(Strict):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    mapping: FieldMapping | None = None
    plan: ForecastPlan | None = None


class ApproveModelingDraft(Strict):
    expected_revision: int = Field(ge=1)
    confirm: bool = False
    acknowledge_single_target: bool = False
    acknowledge_availability: bool = False


class OnlineRecord(Strict):
    values: dict[str, float] = Field(min_length=1, max_length=32)
    decision_time: datetime
    features_available_at: datetime
    entity_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode='after')
    def causal(self):
        if self.decision_time.tzinfo is None or self.features_available_at.tzinfo is None:
            raise ValueError('在线预测决策与特征时间必须含时区')
        if self.features_available_at > self.decision_time:
            raise ValueError('特征在判断时刻后才可用，拒绝分类推断')
        return self


class ClassificationScoreRequest(Strict):
    confirm_feature_availability: bool = False
    records: list[OnlineRecord] = Field(min_length=1, max_length=200)


class ModelingWorkbench:
    def __init__(self, catalog, native, *, generic=None):
        self.catalog = catalog
        self.native = native
        self.generic = generic or GenericAssetStore(catalog.imports)
        self.platform=DataPlatform(catalog,native,self.generic)
        self.home = catalog.root / 'modeling'
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._setup()

    @staticmethod
    def _uuid(value: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ModelingError('建模记录编号无效') from None

    @staticmethod
    def digest(value: dict) -> str:
        return sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode('utf-8'))

    def _setup(self):
        with self.catalog._lock, self.catalog._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS project_model_drafts(
                    draft_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    asset_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    view_id TEXT, selection_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, status TEXT NOT NULL,
                    document TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_model_drafts_project ON project_model_drafts(project_id,updated_at);
                CREATE TABLE IF NOT EXISTS project_model_run_sources(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    run_id TEXT NOT NULL, draft_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL, dataset_id TEXT NOT NULL,
                    task_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,run_id));
            ''')

    def _source(self, pid: str, aid: str, version: str = 'base') -> dict:
        try:
            link = self.catalog.require_generic(pid, aid)
        except KeyError:
            # Reviewed materialized BI derivations are first-class model sources.
            if getattr(self, 'derived', None) is None:
                raise
            return self.derived.model_source(pid,aid,version)
        meta = self.generic.version(link['import_id'], link['candidate_id'], link['version_id'])
        path = self.generic.version_file(link['import_id'], link['candidate_id'], link['version_id'])
        native = self.native.asset(aid)
        lineage = native.get('lineage') or {}
        if (lineage.get('project_id') != pid or lineage.get('import_id') != link['import_id']
                or lineage.get('candidate_id') != link['candidate_id']
                or lineage.get('version_id') != link['version_id']
                or lineage.get('data_sha256') != meta['data_sha256']):
            raise ModelingError('BI 和标准数据资产的来源不一致；已阻止生成模型')
        if not self.native.verify(aid).get('valid'):
            raise ModelingError('BI 数据快照完整性校验失败')
        self.native._version(aid, version)
        columns = [col['name'] for col in native['columns']]
        if columns != (meta.get('internal_columns') or meta['source_columns']):
            raise ModelingError('BI 字段与原始发布字段不一致，请重新发布数据资产')
        return {'project_id':pid,'asset_id':aid,'version_id':version,
                'generic_version_id':meta['version_id'], 'original_data_sha256':meta['data_sha256'],
                'native_sha256':native['sha256'], 'source_columns':columns,
                'row_count':meta['row_count'],'path':path,'confirmed_roles':meta.get('confirmed_roles',{}),
                'name':meta['name']}

    def _profile_report(self, source: dict) -> dict:
        # Deterministic uniform reservoir across the *entire* published table.
        # Sample-based role and frequency suggestions are labelled as such.
        rng = random.Random(137)
        sample = []
        count = 0
        with source['path'].open('r', encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != source['source_columns']:
                raise ModelingError('发布版本表头与资产元数据不一致')
            for record in reader:
                count += 1
                if count <= PROFILE_SAMPLE_ROWS:
                    sample.append(record)
                else:
                    j = rng.randrange(count)
                    if j < PROFILE_SAMPLE_ROWS:
                        sample[j] = record
        if count != source['row_count']:
            raise ModelingError('原始行数与发布清单不一致')
        fields = []
        for col in source['source_columns']:
            values = [x.get(col) for x in sample]
            nonempty = [str(v).strip() for v in values if v is not None and str(v).strip()]
            numeric = pd.to_numeric(pd.Series(nonempty, dtype='string'), errors='coerce') if nonempty else pd.Series(dtype='float64')
            numeric_rate = (float(numeric.notna().mean()) if len(numeric) else 0.0)
            date_like = sum(bool(re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?$',v)) for v in nonempty)
            datetime_rate = date_like / len(nonempty) if nonempty else 0.0
            uniq = len(set(nonempty))
            ratio = uniq / max(1,len(nonempty))
            lower = col.lower().replace('_','').replace(' ','')
            hinted = lambda role: any(term.replace('_','') in lower for term in ROLE_HINTS[role])
            roles = []
            if datetime_rate >= .95:
                roles.append({'role':'time','confidence':0.96 if hinted('time') else 0.83,
                              'reason':f'{datetime_rate:.0%} 非空样本具有日历日期形式'})
            elif (hinted('time') or lower in {'time','step','index','序号','步骤'}) and numeric_rate>=.97:
                roles.append({'role':'sequence_step','confidence':0.94,
                              'reason':'数值顺序字段缺少时间单位，不能当作日历时间'})
            if hinted('available_at') and datetime_rate >= .95:
                roles.append({'role':'available_at','confidence':0.90,'reason':'字段名和日期内容均符合可用时间候选'})
            if hinted('entity') and 1 < uniq < len(nonempty) and ratio < .75:
                roles.append({'role':'entity','confidence':.88,'reason':f'重复类别 {uniq} 种，可形成分组观测'})
            if hinted('event'):
                roles.append({'role':'event','confidence':.75,'reason':'名称含维护或事件语义，仍需核实发生时点'})
            if hinted('label'):
                roles.append({'role':'target_label','confidence':.75,'reason':'字段名暗示标签，模型插件需单独检查'})
            if numeric_rate >= .95 and not any(r['role']=='sequence_step' for r in roles):
                roles.append({'role':'target_numeric','confidence':.91 if hinted('target') else .68,
                              'reason':f'{numeric_rate:.0%} 非空样本可解析为数值；业务含义仍需确认'})
            fields.append({'name':col,'sample_nonempty':len(nonempty),'sample_distinct':uniq,
                           'sample_numeric_rate':round(numeric_rate,4),
                           'sample_datetime_rate':round(datetime_rate,4),
                           'suggested_roles':sorted(roles,key=lambda r:-r['confidence']),
                           'confirmed_role':next((role for role,name in source['confirmed_roles'].items() if name==col),None),
                           'needs_confirmation':True})
        time = self._pick(fields,'time')
        entity = self._pick(fields,'entity')
        target = self._pick(fields,'target_numeric')
        available = self._pick(fields,'available_at')
        freq = self._suggest_frequency(sample, time, entity)
        return {'asset_id':source['asset_id'],'version_id':source['version_id'],
                'source_sha256':source['original_data_sha256'],
                'profile_version':PROFILE_VERSION,'scope':'full_row_count_with_reservoir_sample',
                'source_rows':count,'sample_rows':len(sample), 'fields':fields,
                'recommendation':{'time':time,'entity':entity,'target':target,'available_at':available,
                                  'frequency':freq},
                'notes':['字段角色为统计候选，原始列名及单位必须由用户确认',
                         '多变量历史特征与分类需明确字段可用时间；所有建模输入可追溯']}

    @staticmethod
    def _pick(fields: list[dict], role: str) -> str | None:
        candidates = sorted(((r['confidence'], f['name']) for f in fields
                             for r in f['suggested_roles'] if r['role']==role), reverse=True)
        # Bare A1, A2 columns have low confidence and cannot choose a business target.
        if not candidates or candidates[0][0]<.8 or (len(candidates)>1 and
            candidates[0][0]==candidates[1][0]):
            return None
        return candidates[0][1]

    @staticmethod
    def _suggest_frequency(sample: list[dict], time: str | None, entity: str | None) -> dict:
        if not time:
            return {'kind':'unknown','frequency':None,'confidence':0,'reason':'缺少明确日历时间列'}
        frame = pd.DataFrame(sample)
        dt = pd.to_datetime(frame[time], errors='coerce', format='mixed', utc=True)
        if dt.notna().mean()<.95:
            return {'kind':'unknown','frequency':None,'confidence':0,'reason':'时间戳解析成功率不足'}
        frame['_tp_time'] = dt
        gaps = []
        for _,group in frame.groupby(entity,dropna=False) if entity else [(None,frame)]:
            times = group['_tp_time'].dropna().drop_duplicates().sort_values()
            gaps.extend((times.diff().dt.total_seconds().dropna().round().astype('int64')).tolist())
        if not gaps:
            return {'kind':'unknown','frequency':None,'confidence':0,'reason':'有效时间点不足'}
        from collections import Counter
        dominant, count = Counter(gaps).most_common(1)[0]
        share = count/len(gaps)
        freq = {3600:'h',86400:'D',60:'min',300:'5min',1:'s'}.get(dominant)
        if not freq and 2419200 <= dominant <= 2678400:
            freq='MS'
        # Reservoir sampling is sparse for large inputs. Never claim a proven
        # regular frequency, or make the proposal runnable without full preflight.
        return {'kind':'sampled_interval','frequency':freq, 'confidence':round(share*.7,3),
                'dominant_gap_seconds':int(dominant),'sample_gap_share':round(share,3),
                'reason':'频率来自抽样间隔，仅供预填；运行前将逐实体核对完整时间轴'}

    def profile(self, pid: str, asset_id: str, version: str='base') -> dict:
        source = self._source(pid, asset_id, version)
        cache_key = self.digest({'source':source['original_data_sha256'],'native':source['native_sha256'],
                                'version':version,'profiler':PROFILE_VERSION})
        target = self.home / self.catalog._id(pid) / 'profiles' / f'{cache_key}.json'
        with self._lock:
            if target.exists():
                result=json.loads(target.read_text(encoding='utf-8'))
            else:
                if version != 'base':
                    raise ModelingError('修改后的 BI 版本需要先发布新的不可变通用数据资产再重新画像')
                result=self._profile_report(source)
                atomic_json(target,result)
        return result

    def suggestions(self,pid,asset_id,version='base') -> dict:
        profile=self.profile(pid,asset_id,version)
        mapping=profile['recommendation']
        proposals=[]
        if any(r['role']=='target_numeric' for f in profile['fields'] for r in f['suggested_roles']):
            state=('review_required' if mapping['time'] else 'adapter_required')
            if mapping['frequency'].get('frequency') not in SUPPORTED:
                state='aggregation_review' if mapping['frequency'].get('frequency') in ('min','5min','s') else 'adapter_required'
            proposals.append({'task_kind':'continuous_forecast', 'title':'数值指标趋势预测',
                'status':state,'target':mapping['target'],'time':mapping['time'],
                'entity':mapping['entity'],'frequency':mapping['frequency']['frequency'],
                'reason':'存在数值指标候选；需确认目标、时间、实体和数据可用时间',
                'runnable_after_preflight':mapping['frequency']['frequency'] in SUPPORTED,
                'resampling_available':mapping['frequency']['frequency'] in ('min','5min','s')})
        if any(r['role']=='target_label' for f in profile['fields'] for r in f['suggested_roles']):
            proposals.append({'task_kind':'classification','title':'生产质量风险分类',
                'status':'review_required','reason':'已安装二分类插件，需核实正负标签、标签成熟时间、决策时刻特征及时间留出分组',
                'plugin_id':'binary_logistic','runnable_after_preflight':True})
        if mapping['time']:
            proposals.append({'task_kind':'anomaly_detection','title':'工况异常探索',
                'status':'plugin_missing','reason':'保留异常检测研究任务契约；尚无可运行插件，不能直接提交'})
        return {'asset_id':asset_id,'version_id':version,'profile':profile,
                'proposals':proposals[:3]}

    def _selection(self,pid,request:CreateModelingDraft) -> tuple[dict,dict]:
        source=self._source(pid,request.asset_id,request.version_id)
        warnings=[]
        filters=[]
        view_hash=None
        if request.view_id:
            view=self.native.get_view(self._uuid(request.view_id))
            if view['asset_id'] != request.asset_id:
                raise ModelingError('视图关联的数据资产不一致')
            config=view.get('config') or {}
            view_hash=self.digest(config)
            if config.get('format')!='timepilot-perspective-v2':
                raise ModelingError('仅支持已固定数据版本的 Perspective 视图；请重新保存正式视图')
            saved_source=config.get('source') or {}
            if saved_source.get('version_id')!=request.version_id:
                raise ModelingError('BI 保存视图的数据版本与建模版本不一致')
            if saved_source.get('kind')=='query':
                query=QuerySpec.model_validate(saved_source.get('query'))
                if query.asset_id!=request.asset_id or query.version_id!=request.version_id:
                    raise ModelingError('保存视图查询与当前数据源不一致')
                filters=query.filters
                if query.group_by or query.metrics:
                    warnings.append('只复用明细级业务筛选；图表分组及统计聚合不作为训练数据')
            else:
                warnings.append('图表样本条数、排序和透视布局不作为训练范围；当前使用完整源行')
            if request.filters is not None and [x.model_dump() for x in request.filters]!=[x.model_dump() for x in filters]:
                raise ModelingError('保存视图与另行提交的筛选不一致；请先保存新的 BI 视图')
        else:
            filters=request.filters or []
            warnings.append('已自动冻结当前行级筛选条件；未保存视图布局')
        frozen=None
        if request.selection_id:
            frozen=self.platform.get_selection(pid,request.selection_id)
            if (frozen['native_asset_id']!=request.asset_id or
                frozen['version_id']!=request.version_id or
                frozen['source_sha256']!=source['native_sha256']):
                raise ModelingError('冻结数据选择的资产、版本或源数据校验不一致')
            agreed=[f.model_dump(mode='json') for f in filters]
            if agreed!=frozen['filters']:
                # A saved chart may have a separate local filter. It must never silently
                # override the complete row-level selection used for model training.
                if request.view_id:
                    raise ModelingError('BI 保存视图与冻结数据选择的全局筛选不一致；请先统一筛选再建模')
                filters=[FilterSpec.model_validate(f) for f in frozen['filters']]
            warnings.append('训练行集固定引用已校验的数据选择；透视图本地筛选仍仅用于展示')
        if not request.confirm_scope:
            raise ModelingError('请确认训练范围：使用完整源版本及明确行级筛选；不会仅使用图上显示的样本')
        spec=QuerySpec(asset_id=request.asset_id,version_id=request.version_id,
                       columns=source['source_columns'],filters=filters)
        compile_query(spec,source['source_columns'])
        selection={'source':{k:source[k] for k in ('asset_id','version_id','generic_version_id',
                      'original_data_sha256','native_sha256','row_count')},
                   'view_id':request.view_id,'view_config_hash':view_hash,
                   'filters':[x.model_dump(mode='json') for x in filters],
                   'projections':'all_source_columns','aggregate':None,'limit':None,'offset':None}
        if frozen:
            selection['frozen_selection_id']=frozen['selection_id']
            selection['frozen_row_count']=frozen['row_count']
            selection['frozen_hash']=frozen['selection_hash']
        selection['selection_hash']=self.digest(selection)
        return selection,{'warnings':warnings,'source':source}

    def _store_draft(self, doc: dict):
        with self._lock, self.catalog._db() as db:
            db.execute('''INSERT INTO project_model_drafts
                (draft_id,project_id,asset_id,version_id,view_id,selection_hash,revision,status,document,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET
                revision=excluded.revision,status=excluded.status,document=excluded.document,
                updated_at=excluded.updated_at''',
                (doc['draft_id'],doc['project_id'],doc['asset_id'],doc['version_id'],doc.get('view_id'),
                 doc['selection']['selection_hash'],doc['revision'],doc['status'],
                 json.dumps(doc,ensure_ascii=False,sort_keys=True),doc['created_at'],doc['updated_at']))

    def create_draft(self,pid,request:CreateModelingDraft) -> dict:
        self.catalog.writable(pid)
        selection,found=self._selection(pid,request)
        cols=set(found['source']['source_columns'])
        mapping=request.mapping.model_dump(mode='json')
        for key in ('time','entity','target','available_at','batch'):
            if mapping.get(key) and mapping[key] not in cols:
                raise ModelingError(f'{key} 指定了不存在的源字段：{mapping[key]}')
        for feature in mapping['observed_features']:
            if feature not in cols: raise ModelingError('历史特征字段不存在：'+feature)
        for extra in (mapping['event']['column'],mapping['regime']['column'],*mapping.get('feature_available_at',{}).values(),
                      request.plan.classification.label_available_at if request.plan.classification else None):
            if extra and extra not in cols:raise ModelingError('所声明的事件、工况或标签时间字段不存在：'+extra)
        dt=now()
        doc={'draft_id':str(uuid4()),'project_id':pid,'asset_id':request.asset_id,
             'version_id':request.version_id,'view_id':request.view_id,'name':request.name,
             'selection':selection,'mapping':mapping,'plan':request.plan.model_dump(mode='json'),
             'warnings':found['warnings'],'revision':1,'status':'review_required',
             'preflight':None,'approval':None,'dataset_id':None,'run_id':None,
             'created_at':dt,'updated_at':dt}
        self._store_draft(doc)
        return doc

    def draft(self,pid,did):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('SELECT document FROM project_model_drafts WHERE project_id=? AND draft_id=?',
                           (self.catalog._id(pid),self._uuid(did))).fetchone()
        if not row: raise KeyError('当前项目不存在该建模草稿')
        return json.loads(row['document'])

    def drafts(self,pid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows=db.execute('SELECT document FROM project_model_drafts WHERE project_id=? ORDER BY updated_at DESC',
                            (self.catalog._id(pid),)).fetchall()
        return [json.loads(x['document']) for x in rows]

    def reproduce(self,pid,did):
        # Create a fresh, reviewable experiment using the exact source and
        # row-level selection; never mutate completed runs or reuse approvals.
        self.catalog.writable(pid)
        with self._lock:
            original=self.draft(pid,did)
            self._checked_source(original)
            new=json.loads(json.dumps(original,ensure_ascii=False))
            stamp=now()
            new.update(draft_id=str(uuid4()),name=(original['name']+' · 复现实验')[:120],
                       revision=1,status='review_required',approval=None,preflight=None,
                       dataset_id=None,run_id=None,created_at=stamp,updated_at=stamp,
                       reproduction_of=original['draft_id'])
            new.pop('materialization',None)
            self._store_draft(new)
            return new

    def edit_draft(self,pid,did,request:EditModelingDraft):
        self.catalog.writable(pid)
        with self._lock:
            doc=self.draft(pid,did)
            if doc['revision']!=request.expected_revision:
                raise ModelingError('草稿被其他操作修改，请刷新后重新确认')
            if doc.get('run_id') or doc.get('dataset_id'):
                raise ModelingError('已生成训练数据的草稿被冻结；请建立新的建模方案以保留实验可复现性')
            for key in ('name','mapping','plan'):
                value=getattr(request,key)
                if value is not None: doc[key]=value.model_dump(mode='json') if isinstance(value,BaseModel) else value
            doc.update(revision=doc['revision']+1,status='review_required',preflight=None,approval=None,
                       dataset_id=None,updated_at=now())
            self._store_draft(doc)
            return doc

    def _checked_source(self,doc):
        selection=doc['selection']
        source=self._source(doc['project_id'],doc['asset_id'],doc['version_id'])
        pinned=selection['source']
        for key in ('original_data_sha256','native_sha256','version_id','generic_version_id','row_count'):
            if pinned.get(key)!=source[key]:
                raise ModelingError('数据版本内容发生变化；原草稿仍保留，但禁止使用不一致的新文件')
        if selection.get('selection_hash')!=self.digest({k:v for k,v in selection.items() if k!='selection_hash'}):
            raise ModelingError('筛选条件的版本摘要不一致')
        if doc.get('view_id'):
            # Saved view is immutable; reject deletion, substitution or repointing.
            view=self.native.get_view(doc['view_id'])
            if view['asset_id']!=doc['asset_id'] or self.digest(view.get('config') or {})!=selection.get('view_config_hash'):
                raise ModelingError('BI 保存视图已修改，必须建立新的建模草稿')
        frozen_id=selection.get('frozen_selection_id')
        if frozen_id:
            item=self.platform.get_selection(doc['project_id'],frozen_id)
            if (item['selection_hash']!=selection['frozen_hash'] or
                item['source_sha256']!=source['native_sha256'] or
                item['filters']!=selection['filters']):
                raise ModelingError('冻结数据选择内容发生变化')
        # A fresh governance run with unresolved blocking violations must not
        # silently be ignored by a model execution. Old unchecked assets remain readable.
        for asset in self.platform.list_assets(doc['project_id']):
            if asset.get('native_asset_id')==doc['asset_id'] and not asset.get('missing'):
                report=self.platform.latest_quality(doc['project_id'],asset['asset_id'],source['generic_version_id'])
                if report and report.get('status')=='blocked' and report.get('version_id')==source['generic_version_id']:
                    raise ModelingError('数据质量仍有未处理的阻断项，请先在项目数据管理中处理')
                break
        return source

    def _read_selected(self,doc):
        source=self._checked_source(doc)
        filters=[FilterSpec.model_validate(x) for x in doc['selection']['filters']]
        cols=source['source_columns']
        spec=QuerySpec(asset_id=doc['asset_id'],version_id=doc['version_id'],
                       columns=cols,filters=filters)
        sql,params,count_sql=compile_query(spec,cols,source_sql='__tp_effective')
        if doc['version_id']!='base' or doc['selection'].get('frozen_selection_id'):
            # Frozen training runs the exact same DuckDB predicate as BI preview.
            with self.native._connection() as con:
                meta=self.native.asset(doc['asset_id'])
                self.native._install_effective_view(con,doc['asset_id'],doc['version_id'],meta['columns'])
                selected=int(con.execute(count_sql,params).fetchone()[0])
                if doc['selection'].get('frozen_row_count') is not None and selected!=doc['selection']['frozen_row_count']:
                    raise ModelingError('训练实际选中行数与冻结选择不一致')
                if selected>MODEL_MAX_ROWS: return None,selected
                frame=con.execute(sql,[*params,selected+1,0]).df()
            return frame,selected
        # Base version: read the immutable, hash-verified publish source. This
        # avoids depending on a 1000-row BI preview and keeps no silent row cap.
        # Filtering is evaluated against original field values, not chart data.
        with source['path'].open('r',encoding='utf-8',newline='') as stream:
            reader=csv.DictReader(stream)
            selected=[]
            count=0
            for row in reader:
                if self._match_filters(row,filters):
                    count+=1
                    if count<=MODEL_MAX_ROWS: selected.append(row)
            if doc['selection'].get('frozen_row_count') is not None and count!=doc['selection']['frozen_row_count']:
                raise ModelingError('训练实际选中行数与冻结选择不一致')
            if count>MODEL_MAX_ROWS: return None,count
        return pd.DataFrame(selected,columns=cols,dtype='string'),count

    @staticmethod
    def _match_filters(row:dict,filters:list[FilterSpec]):
        for f in filters:
            value=row.get(f.column)
            if f.op=='is_null': ok=value is None or not str(value).strip()
            elif f.op=='not_null': ok=value is not None and bool(str(value).strip())
            elif f.op=='contains': ok=isinstance(f.value,str) and str(f.value) in str(value or '')
            elif f.op=='starts_with': ok=isinstance(f.value,str) and str(value or '').startswith(f.value)
            elif f.op=='in': ok=str(value) in {str(v) for v in f.value}
            elif f.op in ('eq','ne'): ok=str(value)==str(f.value)
            else:
                try:
                    number=float(value); threshold=float(f.value)
                    ok=math.isfinite(number) and {'gt':lambda: number>threshold,'gte':lambda:number>=threshold,
                         'lt':lambda:number<threshold,'lte':lambda:number<=threshold}[f.op]()
                except (ValueError,TypeError): ok=False
            if f.op=='ne': ok=not ok
            if not ok:return False
        return True

    def _preflight(self,doc,*,return_frame=False):
        mapping=FieldMapping.model_validate(doc['mapping'])
        plan=ForecastPlan.model_validate(doc['plan'])
        blockers=[];warnings=list(doc.get('warnings') or [])
        if plan.task_kind not in ('continuous_forecast','classification'):
            blockers.append('此类研究任务暂未接入可执行模型插件')
        if plan.frequency not in SUPPORTED:
            blockers.append(f'原始采样频率 {plan.frequency} 未被现有预测引擎支持；请明确配置可审计重采样到小时或天')
        names=set(self._checked_source(doc)['source_columns'])
        for key in ('time','target'):
            if not getattr(mapping,key):blockers.append(f'请确认{key}源字段')
        for key in ('time','target','entity','available_at','batch'):
            col=getattr(mapping,key)
            if col and col not in names:blockers.append(f'{key} 字段不存在：{col}')
        if mapping.availability_policy=='unconfirmed':
            blockers.append('请确认数据可用时间来源或明确发布延迟')
        if mapping.event.column and not mapping.event.truthy_values:
            blockers.append('维护事件必须确认触发字段的取值')
        if mapping.observed_features:
            if plan.task_kind=='continuous_forecast' and plan.feature_policy!='historical_observed':
                warnings.append('已选的其他传感器暂不作为预测输入；切换多变量历史模型才能使用')
        if plan.task_kind=='continuous_forecast' and plan.feature_policy=='historical_observed' and not (mapping.observed_features or mapping.event.column or mapping.regime.column):
            blockers.append('多变量历史模型需要至少一种传感器、事件或工况输入')
        if plan.task_kind=='classification' and not mapping.observed_features:
            blockers.append('分类任务必须指定至少一个决策时刻可用的数值特征')
        if plan.task_kind=='classification' and mapping.target in mapping.observed_features:
            blockers.append('质量标签不能作为分类输入特征')
        if mapping.target in mapping.observed_features:
            blockers.append('目标不能重复作为当前时刻输入特征')
        if not mapping.target_unit:
            warnings.append('未确认目标物理单位；评估误差将保留源单位')
        if self.catalog.engine is None:blockers.append('模型执行器不可用')
        elif plan.frequency in SUPPORTED:
            for candidate in plan.model_candidates:
                try:
                    plugin=self.catalog.engine.plugins.get(candidate)
                    cap=plugin.capabilities()
                    if not plugin.available() or plan.frequency not in cap.supports_frequency:
                        blockers.append(f'{candidate} 插件不可用或不支持频率 {plan.frequency}')
                    if plan.task_kind not in cap.task_kinds:
                        blockers.append(f'{candidate} 插件不能执行 {plan.task_kind} 任务')
                    if plan.feature_policy=='historical_observed' and not cap.supports_observed_features:
                        blockers.append(f'{candidate} 仅支持单变量，不能伪装为多变量模型')
                except (ValueError,KeyError) as exc:blockers.append('模型插件不可用：'+str(exc))
        try:frame,selected_count=self._read_selected(doc)
        except (KeyError,ValueError,NativeBIError,DataValidationError) as exc:
            blockers.append('无法构建筛选后的数据：'+str(exc)[:300]);frame=None;selected_count=0
        if not selected_count:blockers.append('当前筛选后没有数据行')
        if selected_count>MODEL_MAX_ROWS:
            blockers.append(f'筛选后 {selected_count} 行超过训练适配上限 {MODEL_MAX_ROWS}')
        prepared=None;series_count=None;coverage=None;audit_preview=None;class_preview=None
        if frame is not None and mapping.time in names and mapping.target in names and plan.frequency in SUPPORTED:
            try:
                prepared,audit=prepare_observations(frame,mapping=mapping,plan=plan)
                series_count=int(prepared['series_id'].nunique())
                feature_cols=audit['feature_columns'] if plan.feature_policy!='target_history_only' else []
                audit_preview={k:v for k,v in audit.items() if k!='buckets'}
                audit_preview['bucket_count']=len(audit.get('buckets',[]))
                audit_preview['first_buckets']=audit.get('buckets',[])[:3]
                if plan.task_kind=='continuous_forecast':
                    counts=[]
                    for sid,group in prepared.groupby('series_id'):
                        sorted_group=group.sort_values('timestamp')
                        times=pd.to_datetime(sorted_group['timestamp'],utc=True)
                        expected=pd.date_range(start=times.iloc[0],periods=len(times),freq=plan.frequency,tz='UTC')
                        if not pd.DatetimeIndex(times).equals(expected):
                            blockers.append(f'实体 {sid}: 时间序列仍存在缺口或频率不匹配')
                        seasonal=12 if plan.frequency=='MS' else 7 if plan.frequency=='D' else 24
                        min_plugin=seasonal if 'seasonal_naive' in plan.model_candidates else 2
                        if 'historical_exog_ridge' in plan.model_candidates:min_plugin=max(24,min_plugin)
                        need=max(plan.min_history,min_plugin)+2*plan.horizon_steps+plan.validation_windows-1
                        if len(group)<need:blockers.append(f'实体 {sid}: 需要至少 {need} 个连续时间点，实际 {len(group)}')
                        else:
                            available=pd.to_datetime(sorted_group['available_at'],utc=True).reset_index(drop=True)
                            timestamps=times.reset_index(drop=True)
                            folds=[len(group)-2*plan.horizon_steps-(plan.validation_windows-1)+i
                                   for i in range(plan.validation_windows)]+[len(group)-plan.horizon_steps]
                            for origin_idx in folds:
                                last_available=available.iloc[:origin_idx].max()
                                # Interval labels represent bucket starts; their
                                # target becomes available at the end of the interval.
                                if available.iloc[origin_idx]<=last_available:
                                    blockers.append(f'实体 {sid}: 预测窗口目标已在模型起点可用（信息泄漏）')
                                    break
                                if last_available>timestamps.iloc[origin_idx]:
                                    blockers.append(f'实体 {sid}: 预测时刻早于历史数据实际可用时间（未来信息泄漏）')
                                    break
                        counts.append(len(group))
                    coverage={'start':str(prepared['timestamp'].min()),'end':str(prepared['timestamp'].max()),
                              'per_entity_min_rows':min(counts) if counts else 0}
                else:
                    split=temporal_classification_split(prepared,plan.classification)
                    class_preview={k:v for k,v in split['audit'].items()}
                    coverage={'start':str(prepared['timestamp'].min()),'end':str(prepared['timestamp'].max())}
            except (DataValidationError, ValueError, TypeError, OverflowError) as exc:
                blockers.append('数据适配失败：'+str(exc)[:480])
        check={'status':'blocked' if blockers else 'ready','ready':not blockers,
               'source_rows':doc['selection']['source']['row_count'],
               'selected_rows':selected_count,'eligible_rows':int(len(prepared)) if prepared is not None else None,
               'series_count':series_count,'time_coverage':coverage,
               'blockers':blockers[:40],'warnings':list(dict.fromkeys(warnings)),
               'task_kind':plan.task_kind,'feature_policy':plan.feature_policy,
               'feature_count':len(feature_cols) if prepared is not None else 0,
               'resampling':audit_preview,'classification':class_preview,
               'evaluation':{'split':'rolling_origin' if plan.task_kind=='continuous_forecast' else 'forward_temporal_holdout',
                             'validation_windows':plan.validation_windows,
                             'test_horizon':plan.horizon_steps,
                             'primary_metric':plan.primary_metric if plan.task_kind=='continuous_forecast' else 'recall',
                             'secondary_metrics':['RMSE','WAPE','MASE','Bias','ME'] if plan.task_kind=='continuous_forecast' else ['precision','false_positive_rate','confusion'],
                             'target_unit':mapping.target_unit or 'unit','task_family':plan.task_family,
                             'per_entity':True,'no_random_shuffle':True},
               'source_hash':doc['selection']['selection_hash'],
               'plan_hash':self.digest({'mapping':doc['mapping'],'plan':doc['plan'],
                                        'selection_hash':doc['selection']['selection_hash']})}
        if return_frame:return check,frame
        return check

    def inspect_preflight(self,pid,did):
        # Agent read permission must not alter any draft, revision or approval.
        return self._preflight(self.draft(pid,did))

    def preflight(self,pid,did):
        with self._lock:
            doc=self.draft(pid,did)
            check=self._preflight(doc)
            # Rechecking an approved materialization must never silently clear
            # its approval; amendments always go through edit_draft() and revision.
            if doc.get('approval') or doc.get('dataset_id') or doc.get('run_id'):
                return check
            doc.update(preflight=check,approval=None,updated_at=now())
            if doc.get('status') not in ('dataset_ready','queued'):doc['status']='ready' if check['ready'] else 'review_required'
            self._store_draft(doc)
            return check

    def approve(self,pid,did,request:ApproveModelingDraft):
        self.catalog.writable(pid)
        with self._lock:
            doc=self.draft(pid,did)
            if request.expected_revision!=doc['revision']:
                raise ModelingError('草稿版本已经变化，请刷新后重新审批')
            if not request.confirm or not request.acknowledge_single_target:
                raise ModelingError('需要明确确认预测目标及当前单目标历史模型限制')
            if not request.acknowledge_availability:
                raise ModelingError('请确认数据可用时间或人工声明的发布延迟')
            check=self._preflight(doc)
            if not check['ready']:raise ModelingError('建模前检查未通过：'+'；'.join(check['blockers'][:4]))
            doc.update(preflight=check, approval={'at':now(),'revision':doc['revision'],
                'plan_hash':check['plan_hash'],'source_hash':check['source_hash']},
                status='approved',updated_at=now())
            self._store_draft(doc)
            return doc

    def materialize(self,pid,did):
        self.catalog.writable(pid)
        with self._lock:
            doc=self.draft(pid,did)
            if doc.get('dataset_id'):
                manifest,_=get_dataset(self.catalog.engine.store,self.catalog.engine.settings,doc['dataset_id'])
                if manifest.source_sha256!=doc['materialization']['selected_input_sha256']:
                    raise ModelingError('物化结果源数据校验失败')
                return doc
            if doc['status']!='approved' or not doc.get('approval'):
                raise ModelingError('请先完成数据预检查和人工确认')
            check,frame=self._preflight(doc,return_frame=True)
            if not check['ready'] or check['plan_hash']!=doc['approval']['plan_hash']:
                raise ModelingError('数据或方案已变化，需要重新审核')
            mapping=FieldMapping.model_validate(doc['mapping'])
            plan=ForecastPlan.model_validate(doc['plan'])
            out=io.StringIO(newline='');frame.to_csv(out,index=False)
            selected_bytes=out.getvalue().encode('utf-8')
            content_sha=sha256_bytes(selected_bytes)
            prepared,audit=prepare_observations(frame,mapping=mapping,plan=plan)
            # Retain actual source-feature->canonical mappings. A classification
            # label is not a predictor; label maturity is independently checked.
            features=audit['feature_columns'] if plan.feature_policy!='target_history_only' else []
            if plan.feature_policy=='target_history_only':
                prepared=prepared.drop(columns=features,errors='ignore')
            contract={'contract_version':'industrial-data-v018-1',
                'source_selection_hash':doc['selection']['selection_hash'],
                'plan_hash':check['plan_hash'],'task_kind':plan.task_kind,
                'field_mapping':mapping.model_dump(mode='json'),
                'feature_policy':plan.feature_policy,'feature_columns':features,
                'classification':plan.classification.model_dump(mode='json') if plan.classification else None,
                'mechanism':plan.mechanism.model_dump(mode='json'),
                'resampling':plan.resampling.model_dump(mode='json'),
                'event':mapping.event.model_dump(mode='json'),
                'regime':mapping.regime.model_dump(mode='json')}
            name='model-'+doc['draft_id']
            existing=self.catalog.engine.store.dataset_by_name_or_id(name)
            if existing:
                from timepilot.contracts import DatasetManifest
                manifest=DatasetManifest.model_validate_json(existing.manifest_json)
                if manifest.source_sha256!=content_sha or manifest.modeling_contract!=contract:
                    raise ModelingError('发现不一致的同名物化数据，不能继续运行')
                get_dataset(self.catalog.engine.store,self.catalog.engine.settings,manifest.dataset_id)
            else:
                manifest=create_modeling_dataset(self.catalog.engine.store,self.catalog.engine.settings,
                    prepared,name=name,source_sha256=content_sha,source_name='model_selection.csv',
                    frequency=plan.frequency,unit=mapping.target_unit or 'unit',
                    task_kind=plan.task_kind,feature_columns=features,modeling_contract=contract)
            artifact_folder=self.catalog.engine.settings.home/'datasets'/manifest.dataset_id
            audit_file=artifact_folder/'resampling_audit.json'
            atomic_json(audit_file,audit)
            provenance={'draft_id':did,'project_id':pid,'selection':doc['selection'],
                        'mapping':doc['mapping'],'plan':doc['plan'],'plan_hash':check['plan_hash'],
                        'selected_input_sha256':content_sha,
                        'dataset_snapshot_sha256':manifest.snapshot_sha256,
                        'resampling_audit_sha256':sha256_file(audit_file),
                        'feature_columns':features,'data_contract':contract}
            atomic_json(artifact_folder/'modeling_provenance.json',provenance)
            doc.update(dataset_id=manifest.dataset_id,materialization=provenance,
                       status='dataset_ready',updated_at=now())
            self._store_draft(doc)
            return doc

    def submit(self,pid,did):
        self.catalog.writable(pid)
        with self._lock:
            doc=self.draft(pid,did)
            if doc.get('run_id'):
                return {'project_id':pid,'draft_id':did,'run_id':doc['run_id'],'reused':True}
            if doc['status']!='dataset_ready' or not doc.get('dataset_id') or not doc.get('approval'):
                raise ModelingError('请先完成确认与训练数据生成')
            check=self._preflight(doc)
            if not check['ready'] or check['plan_hash']!=doc['approval']['plan_hash']:
                raise ModelingError('源版本或评估配置已改变，无法继续使用历史审核')
            plan=ForecastPlan.model_validate(doc['plan'])
            task=TaskSpec(task_id='model-'+did,name=doc['name'],task_family=plan.task_family,
                task_kind=plan.task_kind,feature_columns=doc['materialization']['feature_columns'],
                modeling_contract=doc['materialization']['data_contract'],
                classification_min_recall=plan.classification.min_recall if plan.classification else .9,
                classification_val_fraction=plan.classification.validation_fraction if plan.classification else .2,
                classification_test_fraction=plan.classification.test_fraction if plan.classification else .2,
                classification_min_positive=(plan.classification.min_positive_train,
                    plan.classification.min_positive_validation,plan.classification.min_positive_test) if plan.classification else (5,2,2),
                frequency=plan.frequency,forecast_horizon=plan.horizon_steps,
                model_candidates=plan.model_candidates,approved=True,
                evaluation=EvaluationSpec(validation_windows=plan.validation_windows,
                  test_horizon=plan.horizon_steps, min_history=plan.min_history,
                  primary_metric=plan.primary_metric))
            manifest,frame=get_dataset(self.catalog.engine.store,self.catalog.engine.settings,doc['dataset_id'])
            if manifest.source_sha256!=doc['materialization']['selected_input_sha256']:
                raise ModelingError('训练数据和审核时的筛选记录不同')
            self.catalog.engine._validate(task,frame,manifest)
            # Reuse an existing enqueued run for this unique draft task_id if
            # the process crashed after queue() but before persisting linkage.
            prior=[r for r in self.catalog.engine.store.list_runs(limit=5000)
                   if r.task_id==task.task_id and r.dataset_id==doc['dataset_id']]
            if len(prior)>1:
                raise ModelingError('发现多个同任务运行，需要人工核对后再继续提交')
            run_id=prior[0].id if prior else self.catalog.engine.queue(task,doc['dataset_id'])
            self.catalog.attach_run(pid,run_id)
            task_sha=self.digest(task.model_dump(mode='json'))
            with self.catalog._db() as db:
                db.execute('''INSERT INTO project_model_run_sources
                    (project_id,run_id,draft_id,source_hash,dataset_id,task_sha256,created_at)
                    VALUES(?,?,?,?,?,?,?)''',
                    (pid,run_id,did,doc['selection']['selection_hash'],doc['dataset_id'],task_sha,now()))
            doc.update(run_id=run_id,status='queued',updated_at=now())
            self._store_draft(doc)
            return {'project_id':pid,'draft_id':did,'run_id':run_id,'dataset_id':doc['dataset_id'],
                    'selection_hash':doc['selection']['selection_hash'],'status':'queued'}

    def audit(self,pid,did,*,offset:int=0,limit:int=50):
        if offset<0 or not 1<=limit<=200:raise ModelingError('审计分页参数无效')
        doc=self.draft(pid,did)
        self._checked_source(doc)
        if not doc.get('dataset_id'):
            return {'dataset_id':None,'selection_hash':doc['selection']['selection_hash'],
                    'resampling_summary':doc.get('preflight',{}).get('resampling') if doc.get('preflight') else None,
                    'buckets':[],'total_buckets':0}
        from timepilot.storage import load_json
        manifest,_=get_dataset(self.catalog.engine.store,self.catalog.engine.settings,doc['dataset_id'])
        if manifest.modeling_contract.get('source_selection_hash')!=doc['selection']['selection_hash']:
            raise ModelingError('数据集不属于该草稿的冻结筛选')
        audit_path=self.catalog.engine.settings.home/'datasets'/doc['dataset_id']/'resampling_audit.json'
        if sha256_file(audit_path)!=doc['materialization']['resampling_audit_sha256']:
            raise ModelingError('重采样审计记录 SHA-256 不匹配')
        result=load_json(audit_path)
        buckets=result.get('buckets',[])
        return {'dataset_id':doc['dataset_id'],'selection_hash':doc['selection']['selection_hash'],
                'audit_sha256':doc['materialization']['resampling_audit_sha256'],
                'resampling_summary':{key:value for key,value in result.items() if key!='buckets'},
                'buckets':buckets[offset:offset+limit],'offset':offset,'limit':limit,
                'total_buckets':len(buckets)}

    def score_classification(self,pid,did,request:ClassificationScoreRequest):
        # Research inference only. No automatic scrap/reject actuator interface.
        doc=self.draft(pid,did)
        if doc['plan']['task_kind']!='classification' or not doc.get('run_id'):
            raise ModelingError('请先完成已审批的分类模型运行')
        if not request.confirm_feature_availability:
            raise ModelingError('必须确认每条在线输入在判断时刻已经真实可用')
        if doc['run_id'] not in {x['run_id'] for x in self.catalog.runs(pid)}:
            raise ModelingError('模型运行与当前项目不匹配')
        report=self.catalog.engine.report(doc['run_id'])
        if report.get('status')!='passed':
            raise ModelingError('独立测试召回率尚未达到审核阈值，禁止生成业务判断')
        run_dir=self.catalog.engine.run_dir(doc['run_id'])
        artifact=load_json(run_dir/'stages/classification_result.json')
        name=artifact['model_id']
        model_path=run_dir/'classification_models'/name/'model.json'
        if sha256_file(model_path)!=artifact['model_sha256']:
            raise ModelingError('分类模型权重文件的完整性检查失败')
        manifest,_=get_dataset(self.catalog.engine.store,self.catalog.engine.settings,doc['dataset_id'])
        features=manifest.feature_columns
        feature_map=doc['materialization']['data_contract']['field_mapping']['observed_features']
        allowed={f'obs_{index+1:02d}':source for index,source in enumerate(feature_map)}
        if set(features)!=set(allowed):
            raise ModelingError('当前模型使用事件/工况派生特征，在线推断需要先完成相同的上下文转换')
        expected=set(allowed.values())
        import pandas as pd
        records=[]
        for row in request.records:
            if set(row.values)!=expected:
                raise ModelingError('在线输入字段必须与审核的原始特征完全一致，不允许额外添加标签或未来字段')
            records.append({key:row.values[source] for key,source in allowed.items()})
        plugin=self.catalog.engine.plugins.get(name)
        model=plugin.load(model_path.parent)
        if model.get('version')!=artifact['plugin_version']:
            raise ModelingError('模型版本与原运行记录不一致')
        probabilities=plugin.predict_proba(model,pd.DataFrame(records))
        threshold=float(artifact['validation']['selected_threshold'])
        output=[{'entity_id':row.entity_id,'decision_time':row.decision_time.isoformat(),
                 'probability':round(float(p),8),'risk_flag':bool(p>=threshold),
                 'recommended_next_step':'人工复核高风险品' if p>=threshold else '按工艺规定继续流转'}
                for row,p in zip(request.records,probabilities)]
        # Audit metadata only. Raw sensor values never enter the event log.
        self.catalog.engine._event(doc['run_id'],'classification_scored',
            '受控推断已执行；无自动产线剔除动作',count=len(records),
            feature_contract_hash=self.digest({'fields':sorted(expected)}))
        return {'project_id':pid,'draft_id':did,'run_id':doc['run_id'],
                'model_id':name,'decision_stage':doc['plan']['classification']['decision_stage'],
                'threshold':threshold,'threshold_source':'validation_only',
                'mode':'research_decision_support','results':output}

    def evaluation(self,pid,did,*,offset:int=0,limit:int=100):
        if offset<0 or not 1<=limit<=200:raise ModelingError('评估分页参数无效')
        doc=self.draft(pid,did)
        if not doc.get('run_id'):
            return {'status':'not_submitted','draft_id':did,'run_id':None}
        owned={x['run_id'] for x in self.catalog.runs(pid)}
        if doc['run_id'] not in owned:raise ModelingError('项目模型运行来源异常')
        row=self.catalog.engine.store.run(doc['run_id'])
        result={'draft_id':did,'run_id':doc['run_id'],'status':str(row.status),
                'error_message':row.error_message,'task_kind':doc['plan']['task_kind']}
        if str(row.status)!='succeeded':return result
        report=self.catalog.engine.report(row.id)
        result['quality']=report
        if doc['plan']['task_kind']=='classification':
            classified=load_json(self.catalog.engine.run_dir(row.id)/'stages/classification_result.json')
            result['classification']={key:val for key,val in classified.items() if key!='predictions'}
            results=classified.get('predictions',[])
            result.update(predictions=results[offset:offset+limit],total_predictions=len(results))
        else:
            forecast=self.catalog.engine.forecast(row.id)
            rows=forecast.get('test',[])
            result.update(predictions=rows[offset:offset+limit],total_predictions=len(rows))
        return result

    def lineage(self,pid,did):
        doc=self.draft(pid,did)
        output={'project_id':pid,'draft_id':did,'source':doc['selection']['source'],
                'view_id':doc.get('view_id'),'filters':doc['selection']['filters'],
                'selection_hash':doc['selection']['selection_hash'],
                'mapping':doc['mapping'],'plan':doc['plan'],
                'reproduction_of':doc.get('reproduction_of'),
                'approval':doc.get('approval'),'dataset_id':doc.get('dataset_id'),
                'materialization':doc.get('materialization'),'run_id':doc.get('run_id')}
        if doc.get('run_id'):
            matches=[r for r in self.catalog.runs(pid) if r['run_id']==doc['run_id']]
            output['run']=matches[0] if matches else None
        return output
