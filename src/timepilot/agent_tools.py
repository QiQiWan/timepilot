"""TimePilot v0.10.0: one typed, permissioned tool catalogue shared by data and BI agents.

No model-provided SQL, shell, Python, paths or arbitrary callables are accepted.
Every tool has a strict Pydantic argument contract and explicit effects classification.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.bi import AggregateSpec, PivotSpec
from timepilot.auto_experiment import ExperimentSpec

from timepilot.data_assets import GridRequest, GroupRequest
from timepilot.data_understanding import Approval, ReadToolRequest
from timepilot.smart_workbench import DraftOptions, ViewSelection
from timepilot.generic_assets import PublishGeneral
from timepilot.modeling_workflow import CreateModelingDraft
from timepilot.data_platform import DataPlatform, SelectionInput


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportArgs(Strict):
    import_id: str = Field(min_length=32, max_length=40)
    candidate_ids: list[str] = Field(default_factory=list, max_length=100)
    version_id: str | None = None


class ProjectArgs(Strict):
    project_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')


class RegionArgs(ProjectArgs):
    import_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')
    candidate_id: str = Field(pattern=r'^[a-f0-9]{20}$')
    header_row: int | None = Field(default=None, ge=1, le=100)


class GenericVersionArgs(RegionArgs):
    version_id: str = Field(pattern=r'^[a-f0-9]{24}$')


class UnifiedAssetArgs(ProjectArgs):
    asset_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')


class SelectionAgentArgs(ProjectArgs):
    native_asset_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')
    version_id: str = 'base'
    filters: list[dict] = Field(default_factory=list,max_length=12)
    confirm: bool = False


class ModelingAssetArgs(ProjectArgs):
    asset_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')
    version_id: str = Field(default='base', min_length=4, max_length=50)


class ModelingDraftArgs(ProjectArgs):
    draft_id: str = Field(pattern=r'^[0-9a-fA-F-]{36}$')


class ModelingCreateArgs(ProjectArgs, CreateModelingDraft):
    """Projects and immutable asset selectors are both validated by the tool contract."""


class AssetArgs(Strict):
    asset_id: str = Field(min_length=5, max_length=120)
    version_id: str = Field(min_length=16, max_length=64)
    selection: ViewSelection = Field(default_factory=ViewSelection)


class AssetGridArgs(AssetArgs):
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AssetGroupArgs(AssetArgs):
    group_by: Literal['country', 'brand', 'vehicle', 'source_name', 'statistical_scope', 'timestamp'] = 'timestamp'
    operation: Literal['count', 'sum', 'avg', 'min', 'max'] = 'count'


class TableArgs(Strict):
    table_id: str = Field(min_length=32, max_length=40)


class AggregateArgs(TableArgs):
    spec: AggregateSpec


class PivotArgs(TableArgs):
    spec: PivotSpec


class FeatureArgs(AssetArgs):
    lags: list[int] = Field(default_factory=lambda: [1, 3, 12], min_length=1, max_length=4)
    max_rows: int = Field(default=100_000, ge=1, le=200_000)

    @model_validator(mode='after')
    def check_lags(self):
        if any(n < 1 or n > 36 for n in self.lags) or len(self.lags) != len(set(self.lags)):
            raise ValueError('滞后阶数必须是 1—36 的不重复正整数')
        return self


class ForecastArgs(AssetArgs):
    view_id: str = Field(min_length=32, max_length=40)
    options: DraftOptions = Field(default_factory=DraftOptions)


class TransformArgs(Strict):
    import_id: str = Field(min_length=32, max_length=40)
    plan_id: str = Field(min_length=32, max_length=40)
    decision: Approval


class SemanticSeedArgs(Strict):
    import_id: str = Field(min_length=32, max_length=40)
    max_cases: int = Field(default=100,ge=1,le=200)


class SemanticEvaluateArgs(Strict):
    use_model: bool = False
    max_cases: int = Field(default=3,ge=1,le=5)  # Bound external cost in one Agent step.
    input_usd_per_million: float | None = Field(default=None,ge=0,le=1000)
    output_usd_per_million: float | None = Field(default=None,ge=0,le=1000)


class MechanismArgs(Strict):
    evidence_id: str=Field(pattern=r'^[a-f0-9]{64}$')
    asset_id: str | None = None
    version_id: str | None = None

    @model_validator(mode='after')
    def matching_reference(self):
        if bool(self.asset_id) != bool(self.version_id):
            raise ValueError('机理证据资产关联必须同时包含资产 ID 和版本 ID')
        return self


def _assess_mechanism(s, args: MechanismArgs) -> dict:
    report = s.mechanism.assess(args.evidence_id)
    if args.asset_id and (report.get('source_asset_id') != args.asset_id or
                          report.get('source_version_id') != args.version_id):
        raise ValueError('机理数据不对应当前预测数据资产版本；拒绝错误关联')
    return report


@dataclass(frozen=True)
class Tool:
    name: str
    domain: str
    permission: Literal['read', 'write_confirm']
    units: int
    args: type[BaseModel]
    run: Callable[[Any, BaseModel], dict]
    description: str

    def public(self) -> dict:
        return {'name': self.name, 'domain': self.domain, 'permission': self.permission,
                'cost_units': self.units, 'description': self.description,
                'input_schema': self.args.model_json_schema()}


class ToolRegistry:
    """Explicit allowlist; no dynamic imports, arbitrary code or model-side dispatch."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, spec: Tool) -> None:
        if spec.name in self._tools:
            raise ValueError('duplicate tool: ' + spec.name)
        if spec.units < 1:
            raise ValueError('tool units must be positive')
        self._tools[spec.name] = spec

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ValueError(f'未授权工具：{name}')
        return self._tools[name]

    def public(self) -> list[dict]:
        return [tool.public() for tool in self._tools.values()]

    def execute(self, name: str, args: dict, services: Any) -> dict:
        spec = self.get(name)
        values = spec.args.model_validate(args)
        output = spec.run(services, values)
        if not isinstance(output, dict):
            raise ValueError('工具必须返回结构化字典')
        return output


@dataclass
class ToolServices:
    understanding: Any
    assets: Any
    workbench: Any
    bi: Any
    settings: Any
    cancel_requested: Callable[[], bool] | None = None
    benchmark: Any = None
    experiments: Any = None
    mechanism: Any = None
    projects: Any = None
    generic: Any = None
    native: Any = None
    modeling: Any = None
    platform: Any = None


def _read_tool(name: str) -> Callable:
    def run(s: ToolServices, v: ImportArgs):
        return s.understanding.run_read_tool(v.import_id, name,
                    ReadToolRequest(candidate_ids=v.candidate_ids, version_id=v.version_id))
    return run


def _require_immutable_single_scope(analysis: dict) -> None:
    summary = analysis.get('summary', {})
    if summary.get('distinct_scopes', 0) != 1 or summary.get('distinct_sources', 0) != 1:
        raise ValueError('特征工程需要选定单一统计口径与单一来源，禁止混合来源生成训练特征')


def _feature_rows(s: ToolServices, args: FeatureArgs, *, preview_only: bool,
                  should_cancel: Callable[[], bool] | None = None) -> dict:
    """Compute previous-period lags without using current/future target as a feature.

    Observations from future publication times are suppressed. Outputs are explicitly
    exploratory; no implicit model training occurs. A 200k row bound prevents OOM.
    """
    import_id, _, version = s.assets._resolve(args.asset_id, args.version_id)
    analysis = s.assets.analyze(args.asset_id, args.version_id, args.selection)
    _require_immutable_single_scope(analysis)
    where, params = s.workbench._where(args.selection)
    connection = s.workbench._connect(import_id, args.version_id)
    try:
        total = connection.execute('SELECT COUNT(*) FROM observations' + where, params).fetchone()[0]
        if total > args.max_rows:
            raise ValueError(f'筛选包含 {total} 行，超过特征构建上限 {args.max_rows}；请先筛选系列')
        statement = ('SELECT series_id,timestamp,target,available_at,quality_flags '
                     'FROM observations' + where + ' ORDER BY series_id,timestamp,id')
        cursor = connection.execute(statement, params)
        previous: dict[int, tuple[int, float | None, date | None]] = {}
        last_series = None
        observed_months: set[int] = set()
        preview: list[dict] = []
        complete = 0
        suppressed = 0
        count = 0
        output_path = None
        temp_path = None
        stream = None
        writer = None
        if not preview_only:
            store_root = Path(s.settings.home) / 'agent_artifacts' / 'features'
            store_root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(json.dumps({'source_sha256': version['sha256'],
                        'asset_id': args.asset_id, 'version_id': args.version_id,
                        'selection': args.selection.canonical(), 'lags': sorted(args.lags)},
                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            output_path = store_root / f'{digest}.csv'
            manifest_path=output_path.with_suffix('.json')
            if output_path.exists():
                if not manifest_path.is_file():
                    raise ValueError('特征产物缺少完整性清单，拒绝重用')
                old=json.loads(manifest_path.read_text(encoding='utf-8'))
                actual_sha=hashlib.sha256(output_path.read_bytes()).hexdigest()
                if old.get('sha256') != actual_sha or old.get('source_sha256') != version['sha256']:
                    raise ValueError('特征文件或来源已被修改，拒绝重用')
                return {'reused': True, 'artifact_id': digest, 'artifact_path': str(output_path),
                        'artifact_sha256': actual_sha, 'version_id': args.version_id,
                        'source_sha256': version['sha256']}
            fd, temp_path = tempfile.mkstemp(prefix='.features-', suffix='.csv', dir=store_root)
            stream = os.fdopen(fd, 'w', newline='', encoding='utf-8')
            writer = csv.DictWriter(stream, fieldnames=['series_id','timestamp','target',
                        *[f'lag_{n}' for n in sorted(args.lags)]])
            writer.writeheader()
        try:
            for record in cursor:
                if should_cancel is not None and should_cancel():
                    raise RuntimeError('任务已请求取消，停止输出特征文件')
                count += 1
                sid, timestamp = record['series_id'], record['timestamp']
                if sid != last_series:
                    previous, observed_months, last_series = {}, set(), sid
                month = int(timestamp[:4]) * 12 + int(timestamp[5:7]) - 1
                try:
                    numeric = float(record['target']) if record['target'] not in (None, '') else None
                    if numeric is not None and not math.isfinite(numeric): numeric = None
                except (ValueError, TypeError): numeric = None
                try:
                    publication = date.fromisoformat(str(record['available_at'])[:10]) if record['available_at'] else None
                except (ValueError, TypeError): publication = None
                period_date = date.fromisoformat(timestamp[:10])
                item = {'series_id': sid, 'timestamp': timestamp, 'target': record['target']}
                valid = True
                for lag in sorted(args.lags):
                    item[f'lag_{lag}'] = None
                    old = previous.get(month - lag)
                    if old is not None and old[1] is not None and old[2] is not None and old[2] <= period_date:
                        item[f'lag_{lag}'] = old[1]
                    else:
                        valid = False
                        suppressed += 1
                if valid: complete += 1
                if len(preview) < 12: preview.append(item)
                if writer: writer.writerow(item)
                # Duplicate periods do not arbitrarily overwrite previous observations.
                if month not in observed_months:
                    previous[month] = (month, numeric, publication)
                    observed_months.add(month)
            if stream:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                stream = None
                artifact_sha=hashlib.sha256(Path(temp_path).read_bytes()).hexdigest()
                # Metadata itself is atomic. A file without its manifest is an
                # incomplete release and will fail closed on the next attempt.
                os.replace(temp_path, output_path)
                temp_path = None
                metadata={'sha256':artifact_sha,'source_sha256':version['sha256'],
                          'asset_id':args.asset_id,'version_id':args.version_id,
                          'selection':args.selection.canonical(),'lags':sorted(args.lags)}
                meta_tmp=output_path.with_suffix('.json.tmp')
                meta_tmp.write_text(json.dumps(metadata,ensure_ascii=False,sort_keys=True),encoding='utf-8')
                os.replace(meta_tmp,output_path.with_suffix('.json'))
        finally:
            if stream: stream.close()
            if temp_path and os.path.exists(temp_path): os.unlink(temp_path)
        return {'total_rows': count, 'feature_complete_rows': complete,
                'suppressed_or_missing_lag_values': suppressed,
                'lags': sorted(args.lags), 'preview': preview,
                'reused': False, 'source_sha256': version['sha256'],
                'version_id': args.version_id,
                'artifact_path': str(output_path) if output_path else None,
                'artifact_sha256':artifact_sha if output_path else None,
                'exploratory_only': True,
                'warning': '特征按观测日期的已发布历史计算；输出不能绕过预测任务的发布时间和质量预检'}
    finally:
        connection.close()


def _project_inventory(s, args: ProjectArgs) -> dict:
    s.projects.get(args.project_id)
    # Metadata only; actual source rows remain inside the project BI.
    generic=[]
    for item in s.projects.generic_for(args.project_id):
        ids={k:item[k] for k in ('import_id','candidate_id','version_id','native_asset_id')}
        try:
            meta=s.generic.version(item['import_id'],item['candidate_id'],item['version_id'])
            generic.append(ids | {'name':meta['name'],'row_count':meta['row_count'],
                                  'column_count':meta['column_count'],
                                  'metric_candidates':sum(bool(f.get('metric_candidate')) for f in meta['columns']),
                                  'confirmed_roles':meta['confirmed_roles']})
        except (OSError,KeyError,ValueError):
            generic.append(ids | {'missing':True})
    return {'project_id':args.project_id,
            'standard_assets':s.projects.assets_for(args.project_id),
            'generic_tables':generic,
            'hint':'调用 generic.schema.inspect 获取全部源字段；候选指标的业务含义必须经过人工核实'}


def _generic_schema(s,args:RegionArgs) -> dict:
    s.projects.require_import(args.project_id,args.import_id)
    report=s.generic.inspect(args.import_id,args.candidate_id,header_row=args.header_row)
    # Do not export source record values through Agent outputs; a user can
    # inspect them directly in the local project UI if authorized.
    report.pop('sample_rows',None)
    return report


def _generic_version(s,args:GenericVersionArgs) -> dict:
    s.projects.require_import(args.project_id,args.import_id)
    meta=s.generic.version(args.import_id,args.candidate_id,args.version_id)
    if not any(item['import_id']==args.import_id and item['candidate_id']==args.candidate_id
               and item['version_id']==args.version_id
               for item in s.projects.generic_for(args.project_id)):
        raise KeyError('该项目尚未发布这个通用数据版本')
    return meta


def _platform(s):
    return s.platform or DataPlatform(s.projects, s.native, s.generic)


def _selection_request(a):
    return SelectionInput(native_asset_id=a.native_asset_id,version_id=a.version_id,
                          filters=a.filters,confirm=a.confirm)


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool('project.assets.list', 'data_understanding', 'read', 1,
                           ProjectArgs,_project_inventory,'项目内统一资产发现；只返回版本和元数据'))
    registry.register(Tool('generic.schema.inspect', 'data_understanding', 'read', 2,
                           RegionArgs,_generic_schema,'基于源文件结构和有限样本推断全部列类型；不返回原始行'))
    registry.register(Tool('generic.version.inspect', 'data_understanding', 'read', 1,
                           GenericVersionArgs,_generic_version,'查看当前项目已发布表格的字段、版本和来源哈希'))
    registry.register(Tool('assets.catalog.list','data_understanding','read',1,
                           ProjectArgs,lambda s,a:{'assets':_platform(s).list_assets(a.project_id)},
                           '统一项目资产目录，包含质量状态和规范版本；不返回源行'))
    registry.register(Tool('assets.versions','data_understanding','read',1,
                           UnifiedAssetArgs,lambda s,a:{'versions':_platform(s).versions(a.project_id,a.asset_id)},
                           '查询不可变内容版本和原生 BI 引用'))
    registry.register(Tool('assets.schema.inspect','data_understanding','read',2,
                           UnifiedAssetArgs,lambda s,a:_platform(s).schema(a.project_id,a.asset_id),
                           '字段类型、已确认角色和完整率；不传输原始样本行'))
    registry.register(Tool('quality.issues.list','quality','read',2,
                           ProjectArgs,lambda s,a:{'issues':_platform(s).issues(a.project_id)},
                           '当前项目已识别的质量问题及处理状态'))
    registry.register(Tool('quality.version.inspect','quality','read',2,
                           UnifiedAssetArgs,lambda s,a:_platform(s).latest_quality(a.project_id,a.asset_id)
                           or {'status':'unchecked'},'只读查看版本化质量检查结果'))
    registry.register(Tool('quality.version.evaluate','quality','write_confirm',5,
                           UnifiedAssetArgs,lambda s,a:_platform(s).evaluate_quality(a.project_id,a.asset_id),
                           '人工批准后对已冻结数据版本执行全量质量检查'))
    registry.register(Tool('selection.preview','bi_analysis','read',3,
                           SelectionAgentArgs,lambda s,a:_platform(s).create_selection(
                               a.project_id,_selection_request(a),save=False),
                           '只读预估完整行集数量，忽略图表分页和透视聚合'))
    registry.register(Tool('selection.freeze','bi_analysis','write_confirm',4,
                           SelectionAgentArgs,lambda s,a:_platform(s).create_selection(
                               a.project_id,_selection_request(a),save=True),
                           '人工确认后保存行级筛选、固定数据版本和选择哈希'))
    registry.register(Tool('assets.downstream','data_understanding','read',3,
                           UnifiedAssetArgs,lambda s,a:_platform(s).impact(a.project_id,a.asset_id),
                           '查看质量变更可能影响的 BI 视图及模型运行'))
    registry.register(Tool('assets.storage.inspect','quality','read',4,
                           UnifiedAssetArgs,lambda s,a:_platform(s).storage_scan(a.project_id,a.asset_id),
                           '核对源文件是否存在及哈希，不自动删除数据'))
    registry.register(Tool('modeling.profile.inspect','forecast','read',3,
                           ModelingAssetArgs,
                           lambda s,a:s.modeling.profile(a.project_id,a.asset_id,a.version_id),
                           '项目通用资产字段角色、采样间隔和证据摘要，不返回原始数据行'))
    registry.register(Tool('modeling.tasks.suggest','forecast','read',4,
                           ModelingAssetArgs,
                           lambda s,a:s.modeling.suggestions(a.project_id,a.asset_id,a.version_id),
                           '候选建模任务及执行器支持状态；不会自动训练'))
    registry.register(Tool('modeling.draft.inspect','forecast','read',1,
                           ModelingDraftArgs,
                           lambda s,a:s.modeling.draft(a.project_id,a.draft_id),
                           '查看冻结的筛选、字段、时间与模型验证方案'))
    registry.register(Tool('modeling.draft.preflight','forecast','read',6,
                           ModelingDraftArgs,
                           lambda s,a:s.modeling.inspect_preflight(a.project_id,a.draft_id),
                           '仅检查时间泄漏、训练覆盖及插件支持，不更改已审批方案'))
    registry.register(Tool('modeling.draft.lineage','forecast','read',2,
                           ModelingDraftArgs,
                           lambda s,a:s.modeling.lineage(a.project_id,a.draft_id),
                           '查询严格冻结的数据版本、BI 筛选、特征与评估契约'))
    registry.register(Tool('modeling.draft.audit','forecast','read',3,
                           ModelingDraftArgs,
                           lambda s,a:s.modeling.audit(a.project_id,a.draft_id,limit=5),
                           '校验逐桶审计记录的 SHA-256，并列出前 5 个桶的完整率与可用时间'))
    registry.register(Tool('modeling.run.evaluation','forecast','read',3,
                           ModelingDraftArgs,
                           lambda s,a:s.modeling.evaluation(a.project_id,a.draft_id,limit=20),
                           '查阅项目模型预测或分类实验结果、验证阈值和独立测试指标'))
    registry.register(Tool('modeling.draft.prepare','forecast','write_confirm',5,
                           ModelingCreateArgs,
                           lambda s,a:s.modeling.create_draft(a.project_id,
                               CreateModelingDraft.model_validate(a.model_dump(exclude={'project_id'}))),
                           '人工授权后创建项目建模草稿，训练与发布仍需独立人工确认'))
    for name, domain, units in (
        ('file.inspect', 'data_understanding', 1),
        ('layout.detect', 'data_understanding', 2),
        ('semantics.map', 'data_understanding', 2),
        ('data.clean_preview', 'data_understanding', 1),
        ('quality.evaluate', 'quality', 2),
        ('series.preview', 'data_understanding', 2),
    ):
        registry.register(Tool(name, domain, 'read', units, ImportArgs, _read_tool(name),
                               '候选数据区域检查；只返回有限样本及结构统计'))
    registry.register(Tool('quality.asset', 'quality', 'read', 3, AssetArgs,
                           lambda s, a: s.assets.analyze(a.asset_id, a.version_id, a.selection),
                           '分析已保存版本的缺失、覆盖率、来源和统计口径'))
    registry.register(Tool('bi.asset_grid', 'bi', 'read', 2, AssetGridArgs,
                           lambda s, a: s.assets.grid(a.asset_id, a.version_id,
                               GridRequest(selection=a.selection, limit=a.limit, offset=a.offset)),
                           '读取经过筛选的资产样本，不改变原始数据'))
    registry.register(Tool('bi.asset_group', 'bi', 'read', 3, AssetGroupArgs,
                           lambda s, a: s.assets.group(a.asset_id, a.version_id,
                               GroupRequest(selection=a.selection, group_by=a.group_by,
                                            operation=a.operation)),
                           '受控按维度汇总；数值跨来源聚合受阻断'))
    registry.register(Tool('bi.profile', 'bi', 'read', 3, TableArgs,
                           lambda s, a: s.bi.profile(a.table_id), 'BI 已导入数据字段画像'))
    registry.register(Tool('bi.aggregate', 'bi', 'read', 3, AggregateArgs,
                           lambda s, a: s.bi.aggregate(a.table_id, a.spec), '按显式字段与指标执行受控聚合'))
    registry.register(Tool('bi.pivot', 'bi', 'read', 4, PivotArgs,
                           lambda s, a: s.bi.pivot(a.table_id, a.spec), '执行受控交叉透视'))
    registry.register(Tool('features.preview', 'feature_engineering', 'read', 4, FeatureArgs,
                           lambda s, a: _feature_rows(s, a, preview_only=True),
                           '按实体和发布时间计算滞后特征的安全预览'))
    registry.register(Tool('features.export', 'feature_engineering', 'write_confirm', 6, FeatureArgs,
                           lambda s, a: _feature_rows(s, a, preview_only=False, should_cancel=s.cancel_requested),
                           '确认后生成不可变、内容寻址的探索性特征文件'))
    registry.register(Tool('forecast.draft', 'forecast', 'write_confirm', 4, ForecastArgs,
                           lambda s, a: s.workbench.create_draft(s.assets._resolve(a.asset_id, a.version_id)[0],
                                      a.version_id, a.view_id, a.options),
                           '人工授权后生成预测草稿，绝不启动训练'))
    registry.register(Tool('series.build', 'data_understanding', 'write_confirm', 6, TransformArgs,
                           lambda s, a: s.understanding.execute(a.import_id, a.plan_id, a.decision),
                           '人工确认已有数据整理方案后生成版本，不运行训练'))
    registry.register(Tool('semantic.seed', 'semantic_evaluation', 'write_confirm', 4, SemanticSeedArgs,
                           lambda s,a: s.benchmark.seed(a.import_id,max_cases=a.max_cases),
                           '从真实导入工作表创建人工标注候选；不伪造标签'))
    registry.register(Tool('semantic.evaluate', 'semantic_evaluation', 'write_confirm', 10, SemanticEvaluateArgs,
                           lambda s,a: s.benchmark.evaluate(**a.model_dump(mode='json')),
                           '人工标注的规则/真实模型对照；真实模型调用按预算及独立许可'))
    registry.register(Tool('automodel.plan', 'autonomous_modeling', 'read', 5, ExperimentSpec,
                           lambda s,a: s.experiments.plan(a),
                           '质量和时间泄漏检查，检查同一数据资产的可比较滚动窗口'))
    registry.register(Tool('automodel.evaluate', 'autonomous_modeling', 'write_confirm', 18, ExperimentSpec,
                           lambda s,a: s.experiments.run(a,cancel=s.cancel_requested),
                           '审批后运行本地统计基线和无泄漏滞后特征回归，并保存实验报告；不部署'))
    registry.register(Tool('mechanism.assess', 'mechanism_learning', 'read', 6, MechanismArgs,
                           _assess_mechanism,
                           '使用明确的状态/控制和事件数据对一阶动力学进行辨识和留出验证'))
    return registry


SKILL_TOOLS = {
    'data-understanding': ('project.assets.list','assets.catalog.list','assets.versions','assets.schema.inspect','assets.downstream','generic.schema.inspect','generic.version.inspect',
                           'file.inspect', 'layout.detect', 'semantics.map', 'data.clean_preview', 'series.preview', 'series.build'),
    'data-quality': ('quality.evaluate','quality.asset','quality.issues.list','quality.version.inspect','quality.version.evaluate','assets.storage.inspect'),
    'bi-analysis': ('selection.preview','selection.freeze','bi.asset_grid', 'bi.asset_group', 'bi.profile', 'bi.aggregate', 'bi.pivot'),
    'feature-engineering': ('features.preview', 'features.export'),
    'forecast-preparation': ('forecast.draft','modeling.profile.inspect',
                             'modeling.tasks.suggest','modeling.draft.inspect',
                             'modeling.draft.preflight','modeling.draft.lineage',
                             'modeling.draft.audit','modeling.run.evaluation','modeling.draft.prepare'),
    'semantic-evaluation': ('semantic.seed', 'semantic.evaluate'),
    'autonomous-modeling': ('automodel.plan', 'automodel.evaluate',
                            'modeling.profile.inspect','modeling.tasks.suggest',
                            'modeling.draft.inspect','modeling.draft.preflight'),
    'mechanism-learning': ('mechanism.assess',),
}
