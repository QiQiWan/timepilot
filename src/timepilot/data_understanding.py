"""v0.8.0 - Evidence-first data understanding and permissioned tool orchestration.

No generated code, SQL, shell command or filesystem path is accepted from an agent.
Plans and individual tool calls are read-only. The sole mutating tool delegates to
SmartImportService.start_transform(), which creates an immutable, hashed version.
Business ambiguities are review items, never silently resolved by an LLM.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.smart_import import (
    SmartImportError, SmartImportService, TransformRecipe, _hash_file,
    _numeric, _write_json,
)

TOOL_SCHEMA_VERSION = '1.0'
CAPABILITIES = (
    {'name': 'file.inspect', 'permission': 'read', 'description': '校验上传文件清单、工作表数量和来源哈希'},
    {'name': 'layout.detect', 'permission': 'read', 'description': '识别表头、时间形态及候选数据区域'},
    {'name': 'semantics.map', 'permission': 'read', 'description': '推断时间、目标、来源及业务维度字段；标记歧义'},
    {'name': 'data.clean_preview', 'permission': 'read', 'description': '预览确定性数据清洗规则，不修改原始文件'},
    {'name': 'quality.evaluate', 'permission': 'read', 'description': '查看样本质量、缺失值与口径风险'},
    {'name': 'series.preview', 'permission': 'read', 'description': '预估月度序列构造方式和记录量'},
    {'name': 'series.build', 'permission': 'write_confirm', 'description': '用户确认后调用确定性转换并产生不可变数据版本'},
)
TOOL_NAMES = frozenset(x['name'] for x in CAPABILITIES)
READ_TOOLS = TOOL_NAMES - {'series.build'}
FIELD_ROLES = {
    'country': ('国家/地区', '销售市场', '国家', 'country', 'market'),
    'brand': ('整车厂/品牌', '品牌', 'brand', 'manufacturer'),
    'vehicle': ('车型', '车系', 'vehicle', 'model_name'),
    'group_name': ('集团', '厂商', 'group_name'),
    'source_name': ('来源', '数据来源', 'source_name', 'source'),
    'source_url': ('来源网址', '来源定位', 'source_url', 'source_link'),
    'statistical_scope': ('统计口径', 'metric_scope', 'statistical_scope'),
    'series_id': ('序列标识', '序列ID', 'series_id', 'sku', 'item_id'),
    'timestamp': ('Time', 'timestamp', 'date', '日期', '时间', '年月'),
    'year': ('年份', '年度', '年', 'year'),
    'target': ('当月数量', '本月数量', '销量', '产量', 'target', 'value'),
    'available_at': ('available_at', '发布时间', '数据发布时间', '发布日期', 'published_at'),
}
# Source URL is inspected and retained by the existing importer; it is not
# a writable business mapping role in TransformRecipe.
FIELD_KEYS = frozenset(FIELD_ROLES) - {'source_url'}
MAX_CANDIDATES = 100


class UnderstandingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_ids: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    request: str = Field(default='', max_length=600)
    name: str = Field(default='智能整理的月度数据', min_length=1, max_length=100)
    use_model: bool = False

    @model_validator(mode='after')
    def unique_ids(self):
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError('数据区域不可重复选择')
        return self


class ReadToolRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_ids: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    version_id: str | None = None


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm: Literal[True]
    acknowledged_issues: list[str] = Field(default_factory=list, max_length=12)
    metric_column: str | None = Field(default=None, max_length=120)
    allowed_scopes: list[str] = Field(default_factory=list, max_length=30)
    scope_selection_mode: Literal['explicit', 'keep_separate'] = 'explicit'
    field_mapping: dict[str, str] = Field(default_factory=dict)
    duplicate_policy: Literal['keep_and_flag', 'drop_identical'] = 'keep_and_flag'
    publication_lag_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode='after')
    def check(self):
        if any(k not in FIELD_KEYS or not isinstance(v, str) or len(v) > 120 or not v.strip()
               for k, v in self.field_mapping.items()):
            raise ValueError('字段映射含未经授权的角色或列名')
        if len(self.acknowledged_issues) != len(set(self.acknowledged_issues)):
            raise ValueError('确认事项不得重复')
        return self


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError):
        raise SmartImportError('数据理解方案编号无效') from None


def _field_roles(candidate: dict) -> dict:
    columns = candidate['columns']
    output = {}
    for role, aliases in FIELD_ROLES.items():
        matches = [c for c in columns if any(c.casefold() == a.casefold() for a in aliases)]
        if role == 'timestamp' and candidate.get('time_column') and candidate['time_column'] not in matches:
            matches.insert(0, candidate['time_column'])
        if role == 'year' and candidate.get('year_column') and candidate['year_column'] not in matches:
            matches.insert(0, candidate['year_column'])
        if role == 'target' and candidate.get('kind') != 'wide_monthly':
            matches += [x for x in candidate.get('quantity_columns', []) if x not in matches]
        if matches:
            output[role] = {'suggested': matches[0], 'alternatives': matches[1:4],
                            'confidence': 'high' if len(matches) == 1 and not re.fullmatch(r'[A-Z]\d+', matches[0]) else 'review'}
    if candidate.get('kind') == 'wide_monthly':
        output['target'] = {'suggested': '月度数值列', 'alternatives': [], 'confidence': 'high'}
    return output


class DataUnderstandingService:
    def __init__(self, imports: SmartImportService, gateway=None):
        self.imports = imports
        self.gateway = gateway
        self._lock = threading.RLock()

    def capabilities(self) -> dict:
        return {'version': TOOL_SCHEMA_VERSION, 'tools': list(CAPABILITIES),
                'model_optional': True, 'automatic_write': False,
                'source_rows_sent_to_model': False}

    def _folder(self, import_id: str) -> Path:
        return self.imports._dir(import_id) / 'understanding'

    def _candidates(self, manifest: dict, requested: list[str]) -> list[dict]:
        candidates = {x['candidate_id']: x for x in manifest['candidates']}
        ids = requested if requested else manifest.get('suggested_candidate_ids', [])
        if not ids:
            raise SmartImportError('没有可自动确定的数据区域，请先在列表中选择工作表')
        if len(ids) > MAX_CANDIDATES or len(set(ids)) != len(ids):
            raise SmartImportError('单次最多选择 100 个不重复的数据区域')
        if any(x not in candidates for x in ids):
            raise SmartImportError('选择的数据区域不在本次导入文件中')
        result = [candidates[x] for x in ids]
        # Aggregated source rows and vehicle-detail observations may share a
        # wide-monthly schema; combining them doubles counts.
        summary = [bool('汇总' in c.get('sheet', '') or '汇总标签' in c.get('columns', [])) for c in result]
        if any(summary) and not all(summary):
            raise SmartImportError('汇总区域与车型明细不能同批转换，可能重复计数；请分别建立方案')
        kinds = {c['kind'] for c in result}
        if len(kinds) != 1 or next(iter(kinds)) not in {'wide_monthly', 'long_monthly', 'ordinary_timeseries'}:
            raise SmartImportError('本轮需选择相同结构的时序工作表；汇总或说明页应单独处理')
        return result

    @staticmethod
    def _scope_values(candidates: list[dict]) -> list[str]:
        values = set()
        for c in candidates:
            key = c.get('scope_column')
            for row in c.get('sample_rows', [])[:8]:
                cols = c['columns']
                if key in cols:
                    ix = cols.index(key)
                    value = str(row[ix] if ix < len(row) and row[ix] is not None else '').strip() or '未标注'
                    values.add(value)
                else:
                    values.add('未标注')
        return sorted(values)[:60]

    def run_read_tool(self, import_id: str, name: str, req: ReadToolRequest) -> dict:
        if name not in READ_TOOLS:
            raise SmartImportError('未授权的工具，或写入工具不能通过只读接口执行')
        manifest = self.imports.manifest(import_id)
        candidates = self._candidates(manifest, req.candidate_ids)
        if name == 'file.inspect':
            return {'total_files': len(manifest['files']), 'source_sha256': manifest['source_sha256'],
                    'selected_files': len({c['file_id'] for c in candidates}),
                    'file_samples': [{'file_id': f['file_id'], 'filename': f['filename'],
                                      'bytes': f['bytes'], 'sha256': f['sha256'],
                                      'sheets': f.get('sheet_count', 0), 'warning': f.get('warning')}
                                     for f in manifest['files'][:30]],
                    'limit_note': '列表最多展示前 30 个文件；校验哈希在正式执行前完成'}
        if name == 'layout.detect':
            return {'candidates': [
                {'candidate_id': c['candidate_id'], 'file_id': c['file_id'], 'sheet': c['sheet'],
                 'kind': c['kind'], 'header_row': c['header_row'],
                 'year_column': c.get('year_column'), 'time_column': c.get('time_column'),
                 'months': len(c.get('month_columns', [])), 'rows_estimate': c.get('rows_estimate'),
                 'confidence': 'high' if c.get('suggested') and c['kind']=='wide_monthly' else 'review',
                 'columns': c['columns'][:35]}
                for c in candidates], 'structure': candidates[0]['kind']}
        if name == 'semantics.map':
            return {'fields': [{'candidate_id': c['candidate_id'], 'sheet': c['sheet'],
                                'roles': _field_roles(c)} for c in candidates[:30]],
                    'scope_samples': self._scope_values(candidates),
                    'mapping_is_proposal': True,
                    'notice': '列名只是证据；A2/A14 等代码字段应由业务人员确认含义'}
        if name == 'quality.evaluate':
            if req.version_id:
                meta = self.imports.version(import_id, req.version_id)
                return {'basis': 'full_immutable_version', 'version_id': req.version_id,
                        'rows': meta['counts']['output_rows'], 'series': meta['series_count'],
                        'counts': meta['counts'], 'quality_rates': meta['quality_rates'],
                        'warnings': meta.get('warnings', []),
                        'statistical_scopes': meta['statistical_scopes']}
            stats = Counter()
            for c in candidates:
                cols = c['columns']
                indices = [x['index'] for x in c.get('month_columns', [])]
                if not indices and c.get('quantity_columns'):
                    indices = [cols.index(c['quantity_columns'][0])]
                for row in c.get('sample_rows', [])[:8]:
                    for index in indices:
                        value = row[index] if index < len(row) else None
                        _, flag = _numeric(value)
                        stats['sampled_cells'] += 1
                        if flag: stats[flag] += 1
            return {'basis': 'sample_only', 'counts': dict(stats),
                    'scope_samples': self._scope_values(candidates),
                    'warnings': ['抽样统计不能代表全量质量；转换后将对全部记录重新检查',
                                 '未提供真实发布时间时不自动填充 available_at']}
        if name == 'data.clean_preview':
            return {'operations': [
                {'name': 'trim_dimension_whitespace', 'mode': 'deterministic'},
                {'name': 'parse_decimal_with_thousands_separator', 'mode': 'deterministic'},
                {'name': 'preserve_missing_values_and_flags', 'mode': 'deterministic'},
                {'name': 'flag_exact_duplicates_and_conflicts', 'mode': 'keep_all_default'},
                {'name': 'retain_original_cell_and_unmodified_source', 'mode': 'immutable'}],
                'requires_confirmation_for': ['drop_identical', 'publication_lag_days', 'mixed_scope'],
                'imputation': 'disabled', 'external_data_transfer': False}
        if name == 'series.preview':
            entries = [{'candidate_id': c['candidate_id'], 'file': c['logical_file'],
                        'months': len(c.get('month_columns', [])),
                        'estimated_observations': ((c['rows_estimate'] * len(c['month_columns']))
                            if c['kind']=='wide_monthly' and isinstance(c.get('rows_estimate'),int) else c.get('rows_estimate'))}
                       for c in candidates[:30]]
            estimate = sum(x['estimated_observations'] for x in entries if isinstance(x['estimated_observations'], int))
            return {'structure': candidates[0]['kind'], 'candidate_count': len(candidates),
                    'first_30_estimates': entries, 'estimated_rows_first_30': estimate,
                    'limit_note': '估算按表头后行数计算，含空行/汇总行，不能作为真实记录数',
                    'period_format': 'YYYY-MM-01' if candidates[0]['kind'] != 'ordinary_timeseries' else '原始日期',
                    'auto_train': False}
        raise SmartImportError('工具不支持')

    def _review_issues(self, candidates: list[dict]) -> list[dict]:
        issues=[]
        scopes=self._scope_values(candidates)
        # Short header previews cannot prove that a 50,000-row sheet uses only
        # one scope. Wide monthly business exports must be reviewed explicitly.
        if (len(scopes) > 1 or any(('待核实' in x or x in {'未标注','未知'}) for x in scopes)
            or any(c.get('scope_column') for c in candidates)):
            issues.append({'code':'scope_review', 'title':'确认统计口径',
                           'reason':'样本包含多个口径或待核实标签；产量、销量与不同来源不能盲目求和',
                           'options':scopes[:30], 'required':True})
        if any('汇总' in c.get('sheet', '') or '汇总标签' in c.get('columns', []) for c in candidates):
            issues.append({'code':'aggregate_review', 'title':'确认汇总数据的统计层级',
                           'reason':'源表包含各层级合计。单独分析时需确认层级，禁止与车型明细直接相加', 'required':True})
        if any(len(c.get('quantity_columns', [])) > 1 for c in candidates if c['kind']!='wide_monthly'):
            issues.append({'code':'metric_review', 'title':'确认预测数值列',
                           'reason':'多个数值列可能分别表示价格、数量或其它测量值', 'required':True})
        if any(any(re.fullmatch(r'[A-Z]\d+', x) for x in c['columns']) for c in candidates):
            issues.append({'code':'semantic_review', 'title':'确认代码字段',
                           'reason':'A2、A14 等字段无法凭名称确定业务含义，请核实目标和序列标识', 'required':True})
        source_values=set()
        for c in candidates:
            roles=_field_roles(c)
            name=roles.get('source_name',{}).get('suggested')
            if name in c['columns']:
                i=c['columns'].index(name)
                for row in c.get('sample_rows',[])[:8]:
                    if i < len(row) and row[i]: source_values.add(str(row[i]).strip())
        # A single source in the first few rows does not establish the source
        # contract for all months/years. Explicitly confirm separate provenance.
        if (len(source_values)>1 or any('source_name' in _field_roles(c)
                                  or 'source_url' in _field_roles(c) for c in candidates)):
            issues.append({'code':'source_review','title':'核对多个数据来源',
                           'reason':'来源名称或网址需要核对。原始来源维度和冲突记录将分别保留，禁止未经核实合并', 'required':True})
        return issues

    def propose(self, import_id: str, request: UnderstandingRequest) -> dict:
        manifest = self.imports.manifest(import_id)
        candidates = self._candidates(manifest, request.candidate_ids)
        ids = [x['candidate_id'] for x in candidates]
        steps=[]; findings={}
        for name in ('file.inspect','layout.detect','semantics.map','data.clean_preview',
                     'quality.evaluate','series.preview'):
            output=self.run_read_tool(import_id,name,ReadToolRequest(candidate_ids=ids))
            findings[name]=output
            steps.append({'tool':name,'permission':'read','status':'completed',
                          'output_sha256':_digest(output)})
        steps.append({'tool':'series.build','permission':'write_confirm','status':'awaiting_approval'})
        advice={'origin':'deterministic','summary':'已生成结构、字段和质量检查建议，未修改数据。',
                'recommended_analysis':['按统计口径筛选月度趋势','查看缺失月份与来源冲突',
                                        '保存 BI 视图并生成预测草稿'],
                'cautions':['发布时间尚未核实，不能直接投入防泄漏回测']}
        if request.use_model:
            if self.gateway is None:raise SmartImportError('未配置模型连接')
            # Explicit opt-in only; schema metadata, no rows/file names/source URLs.
            schema=[{'candidate_id':c['candidate_id'],'kind':c['kind'],
                     'columns':c['columns'][:35],'month_count':len(c.get('month_columns', [])),
                     'mapping':{k:v['suggested'] for k,v in _field_roles(c).items()}}
                    for c in candidates[:12]]
            try:
                advice=self.gateway.propose_data_understanding(request.request, schema)
            except Exception as exc:
                # Preserve all completed deterministic evidence and the reviewable
                # plan even when the optional model times out or returns invalid JSON.
                advice={**advice, 'model_status':'fallback',
                        'model_error_type':type(exc).__name__,
                        'cautions':[ *advice['cautions'], '模型建议暂不可用，已保留本地检查结果。']}
        record={'plan_id':str(uuid4()),'import_id':import_id,
                'source_sha256':manifest['source_sha256'],
                'created_at':_utcnow(), 'name':request.name.strip(),
                'request':request.request,'candidate_ids':ids,
                'structure':candidates[0]['kind'], 'scope_samples':self._scope_values(candidates),
                'issues':self._review_issues(candidates), 'steps':steps,
                'findings':findings,'advice':advice,
                'execution_policy':{'requires_user_approval':True,
                                    'external_model_can_execute':False,
                                    'auto_train':False}}
        record['plan_sha256']=_digest(record)
        with self._lock:
            _write_json(self._folder(import_id)/'plans'/f"{record['plan_id']}.json",record)
        return record

    def plan(self, import_id: str, plan_id: str) -> dict:
        path=self._folder(import_id)/'plans'/f'{_safe_id(plan_id)}.json'
        if not path.is_file():raise KeyError('数据理解方案不存在')
        record=json.loads(path.read_text(encoding='utf-8'))
        expected=record.get('plan_sha256','')
        if expected!=_digest({k:v for k,v in record.items() if k!='plan_sha256'}):
            raise SmartImportError('数据理解方案内容已变动，需重新生成')
        if record.get('import_id')!=import_id or record.get('source_sha256')!=self.imports.manifest(import_id)['source_sha256']:
            raise SmartImportError('方案与当前数据来源不一致，需重新分析')
        return record

    def plans(self, import_id: str) -> list[dict]:
        root=self._folder(import_id)/'plans'
        if not root.is_dir():return []
        output=[]
        for p in root.glob('*.json'):
            try:
                plan=self.plan(import_id,p.stem)
                output.append({'plan_id':plan['plan_id'],'plan_sha256':plan['plan_sha256'],
                               'created_at':plan['created_at'],'name':plan['name'],
                               'candidate_count':len(plan['candidate_ids']),
                               'required_reviews':len(plan['issues'])})
            except (ValueError, KeyError, OSError):continue
        return sorted(output,key=lambda x:x['created_at'],reverse=True)[:30]

    def _validate_approval(self, import_id: str, record: dict, decision: Approval) -> TransformRecipe:
        if decision.plan_sha256!=record['plan_sha256']:
            raise SmartImportError('方案哈希不匹配，请重新打开方案')
        missing={x['code'] for x in record['issues']} - set(decision.acknowledged_issues)
        if missing:
            raise SmartImportError('请逐项确认业务歧义：'+', '.join(sorted(missing)))
        candidates=self._candidates(self.imports.manifest(import_id),record['candidate_ids'])
        column_sets=[set(x['columns']) for x in candidates]
        for role,col in decision.field_mapping.items():
            if not all(col in columns for columns in column_sets):
                raise SmartImportError(f'字段映射 {role}={col} 不存在于全部选中工作表')
        if decision.metric_column and not all(decision.metric_column in s for s in column_sets):
            raise SmartImportError('指定数值列并非所有选中数据区域共有')
        if 'metric_review' in {x['code'] for x in record['issues']} and not (
            decision.metric_column or decision.field_mapping.get('target')):
            raise SmartImportError('存在多个数值字段，必须明确选择目标数值列')
        if 'semantic_review' in {x['code'] for x in record['issues']} and candidates[0]['kind']=='ordinary_timeseries' and not (
            decision.metric_column or decision.field_mapping.get('target')):
            raise SmartImportError('代码字段需要明确指定数值目标列')
        # Scope labels are sampled. The user may specify more values than the first
        # eight sample rows, but they must be manually acknowledged as reviewable.
        if any(not v.strip() or len(v)>100 for v in decision.allowed_scopes):
            raise SmartImportError('统计口径字段无效')
        if 'scope_review' in {x['code'] for x in record['issues']} and (
            decision.scope_selection_mode=='explicit' and not decision.allowed_scopes):
            raise SmartImportError('请选定统计口径，或确认保留全部不同口径的原始分组')
        if decision.scope_selection_mode=='keep_separate' and decision.allowed_scopes:
            raise SmartImportError('保留全部口径时不能同时指定筛选口径')
        if decision.duplicate_policy=='drop_identical' and 'duplicate_review' not in decision.acknowledged_issues:
            raise SmartImportError('删除完全相同的重合观测需额外确认 duplicate_review')
        # Opaque identifiers cannot be silently ignored even after the user
        # confirms the target. Fail before creating a conversion job.
        from timepilot.smart_import import _guard_entity_mapping
        for candidate in candidates:
            _guard_entity_mapping(candidate, TransformRecipe(
                candidate_ids=record['candidate_ids'],field_mapping=decision.field_mapping))
        metric=decision.metric_column or decision.field_mapping.get('target')
        mapping={k:v for k,v in decision.field_mapping.items() if k!='target'}
        return TransformRecipe(candidate_ids=record['candidate_ids'],name=record['name'],
                               metric_column=metric, allowed_scopes=decision.allowed_scopes,
                               duplicate_policy=decision.duplicate_policy,
                               publication_lag_days=decision.publication_lag_days,
                               field_mapping=mapping)

    def _check_source_bytes(self, import_id: str, record: dict) -> None:
        manifest=self.imports.manifest(import_id)
        ids={x['file_id'] for x in self._candidates(manifest,record['candidate_ids'])}
        physical=self.imports._dir(import_id).resolve()
        for item in manifest['files']:
            if item['file_id'] in ids:
                path=(physical/item['stored']).resolve()
                if (not path.is_relative_to(physical) or not path.is_file()
                        or _hash_file(path)!=item['sha256']):
                    raise SmartImportError('源文件丢失或校验失败，拒绝建立数据版本')

    def execute(self, import_id: str, plan_id: str, decision: Approval) -> dict:
        with self._lock:
            record=self.plan(import_id,plan_id)
            recipe=self._validate_approval(import_id,record,decision)
            folder=self._folder(import_id)
            receipt_path=folder/'executions'/f"{record['plan_id']}.json"
            if receipt_path.exists():
                existing=json.loads(receipt_path.read_text(encoding='utf-8'))
                if existing['decision_sha256']!=_digest(decision.model_dump(mode='json')):
                    raise SmartImportError('方案已提交执行，不能变更已确认条件；请另建方案')
                return self.execution(import_id,plan_id)
            manifest=self.imports.manifest(import_id)
            # A release manifest only attests metadata; rehash source bytes
            # immediately before both first execution and every retry.
            self._check_source_bytes(import_id, record)
            job=self.imports.start_transform(import_id,recipe)
            receipt={'plan_id':record['plan_id'],'plan_sha256':record['plan_sha256'],
                     'decision_sha256':_digest(decision.model_dump(mode='json')),
                     'approval':decision.model_dump(mode='json'),
                     'recipe_sha256':_digest(recipe.model_dump(mode='json')),
                     'source_sha256':record['source_sha256'],'job_id':job['job_id'],
                     'created_at':_utcnow(),'status':'queued'}
            receipt['receipt_sha256']=_digest(receipt)
            _write_json(receipt_path,receipt)
            return self.execution(import_id,plan_id)

    def execution(self, import_id: str, plan_id: str) -> dict:
        record=self.plan(import_id,plan_id)
        path=self._folder(import_id)/'executions'/f"{record['plan_id']}.json"
        if not path.is_file():return {'status':'not_started','plan_id':record['plan_id']}
        receipt=json.loads(path.read_text(encoding='utf-8'))
        if receipt.get('receipt_sha256')!=_digest({k:v for k,v in receipt.items() if k!='receipt_sha256'}):
            raise SmartImportError('执行审计记录完整性校验失败')
        if receipt.get('plan_sha256')!=record['plan_sha256']:
            raise SmartImportError('执行记录的方案哈希校验不一致')
        job=self.imports.transform_job(import_id,receipt['job_id'])
        result={'plan_id':record['plan_id'],'plan_sha256':record['plan_sha256'],
                'job_id':receipt['job_id'], 'status':job['status'],
                'completed_candidates':job.get('completed_candidates',0),
                'total_candidates':job.get('total_candidates',len(record['candidate_ids'])),
                'output_rows':job.get('output_rows',0), 'error':job.get('error'),
                'version_id':job.get('version_id')}
        if job['status']=='completed' and job.get('version_id'):
            meta=self.imports.version(import_id,job['version_id'])
            allowed=receipt['approval'].get('allowed_scopes',[])
            selection={'filters': {'statistical_scope': allowed} if allowed else {},
                       'date_from':None,'date_to':None}
            result.update({'version_sha256':meta['sha256'],
                           'quality_counts':meta['counts'],
                           'scope_counts':meta['statistical_scopes'],
                           'analysis_task_draft': {
                               'selection':selection,
                               'analysis_checks':['monthly_trend','coverage','missing_values',
                                                  'duplicates','source_conflicts'],
                               'requires_scope_choice':len(meta['statistical_scopes'])>1 and not allowed,
                               'requires_source_choice':len(meta['source_counts'])>1,
                               'saved_view_created':False, 'forecast_task_created':False,
                               'next_step':'在 BI 中核对数据范围并保存筛选视图'},
                           'next_url':f"/imports/{import_id}/versions/{job['version_id']}/workbench",
                           'training_started':False})
        return result

    def retry(self, import_id: str, plan_id: str, decision: Approval) -> dict:
        """Retry an interrupted/failed conversion with the SAME confirmed recipe.

        Retries do not bypass approval, introduce new tool arguments, or run
        another job if the previous job is still active. At most 3 attempts.
        """
        with self._lock:
            plan=self.plan(import_id,plan_id)
            self._validate_approval(import_id,plan,decision)
            path=self._folder(import_id)/'executions'/f"{plan['plan_id']}.json"
            if not path.is_file():raise SmartImportError('方案尚未执行，请先确认并提交')
            previous=self.execution(import_id,plan_id)
            if previous['status'] not in {'failed','interrupted'}:
                raise SmartImportError('只有执行失败或服务重启中断的任务才能重试')
            receipt=json.loads(path.read_text(encoding='utf-8'))
            if receipt['decision_sha256']!=_digest(decision.model_dump(mode='json')):
                raise SmartImportError('重试必须沿用原已确认的方案和处理条件')
            history=receipt.get('history',[])
            if len(history)>=2:
                raise SmartImportError('已达到最多 3 次执行尝试，请重新审查数据并创建方案')
            recipe=self._validate_approval(import_id,plan,decision)
            self._check_source_bytes(import_id,plan)
            job=self.imports.start_transform(import_id,recipe)
            history.append({'job_id':receipt['job_id'],'final_status':previous['status'],
                            'replaced_at':_utcnow()})
            receipt['history']=history
            receipt['job_id']=job['job_id']
            receipt['receipt_sha256']=_digest({k:v for k,v in receipt.items() if k!='receipt_sha256'})
            _write_json(path,receipt)
            return self.execution(import_id,plan_id)
