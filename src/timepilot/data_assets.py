"""Canonical, version-pinned TimePilot DataAsset references and shared BI views.

A smart-import asset points at an existing immutable normalized CSV.  The
catalog does not copy raw bytes.  SQLite indexes are disposable query caches.
Untrusted paths, SQL and model-generated expressions are never accepted.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.smart_import import CSV_COLUMNS, SmartImportError, SmartImportService, _write_json
from timepilot.smart_workbench import SaveView, SmartWorkbench, ViewSelection
from timepilot.db import BITableRow

ASSET_VERSION = 1
ASSET_FIELDS = frozenset(CSV_COLUMNS) | {'numeric_target'}
GRID_FIELDS = frozenset({'id','series_id','entity_id','timestamp','target','numeric_target',
    'available_at','statistical_scope','source_name','country','brand','vehicle','quality_flags',
    'source_file','source_sheet','source_row','source_cell'})
GROUP_FIELDS = frozenset({'series_id','entity_id','timestamp','statistical_scope','source_name','country','brand','vehicle'})
SEMANTIC_ROLES = frozenset({'entity','time','target','availability','scope','source',
    'country','brand','vehicle','source_file','source_sheet','quality_flags'})
CANONICAL_ROLES = {
    'entity':'entity_id','time':'timestamp','target':'target',
    'availability':'available_at','scope':'statistical_scope','source':'source_name',
    'country':'country','brand':'brand','vehicle':'vehicle',
    'source_file':'source_file','source_sheet':'source_sheet','quality_flags':'quality_flags',
}


def asset_id_for(import_id: str) -> str:
    return 'si_' + UUID(import_id).hex


def import_id_for(asset_id: str) -> str:
    if not re.fullmatch(r'si_[a-f0-9]{32}', asset_id or ''):
        raise SmartImportError('未知的数据资产编号')
    return str(UUID(hex=asset_id[3:]))


class SemanticRevision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    roles: dict[str,str] = Field(default_factory=dict, max_length=15)
    confirmed: bool = False
    note: str = Field(default='', max_length=500)

    @model_validator(mode='after')
    def check(self):
        if not set(self.roles).issubset(SEMANTIC_ROLES):
            raise ValueError('语义角色不在授权范围内')
        if any(v not in ASSET_FIELDS for v in self.roles.values()):
            raise ValueError('语义字段不存在于标准资产')
        if len(set(self.roles.values())) != len(self.roles):
            raise ValueError('一个字段不能被多次赋予不同语义角色')
        return self


class GridRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: ViewSelection = Field(default_factory=ViewSelection)
    columns: list[str] = Field(default_factory=list, max_length=30)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=2_000_000)

    @model_validator(mode='after')
    def fields(self):
        if not set(self.columns).issubset(GRID_FIELDS):
            raise ValueError('请求包含未知的数据字段')
        return self


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: ViewSelection = Field(default_factory=ViewSelection)
    group_by: str = 'timestamp'
    operation: Literal['count','sum','avg','min','max'] = 'count'
    limit: int = Field(default=100, ge=1, le=300)

    @model_validator(mode='after')
    def check(self):
        if self.group_by not in GROUP_FIELDS:
            raise ValueError('不支持的分组维度')
        return self


class PivotRequest(BaseModel):
    """Compact read-only pivot operations over the shared DataAsset selection."""
    model_config = ConfigDict(extra='forbid')
    selection: ViewSelection = Field(default_factory=ViewSelection)
    row_field: str = 'country'
    column_field: str = 'statistical_scope'
    operation: Literal['count','sum','avg','min','max'] = 'count'
    limit: int = Field(default=250, ge=1, le=1000)

    @model_validator(mode='after')
    def validate_fields(self):
        if self.row_field not in GROUP_FIELDS or self.column_field not in GROUP_FIELDS:
            raise ValueError('交叉透视字段必须来自标准业务维度')
        if self.row_field == self.column_field:
            raise ValueError('行字段与列字段不能相同')
        return self


class DataAssetCatalog:
    """Read-through registry over immutable smart-import manifests.

    Links share the already-saved workbench views and forecast drafts; native
    Perspective can open the same CSV through a read-only DuckDB view.
    """
    def __init__(self, imports: SmartImportService, workbench: SmartWorkbench, legacy_bi=None):
        self.imports = imports
        self.workbench = workbench
        self.legacy_bi = legacy_bi
        self.root = imports.root.parent / 'data_assets'
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, asset_id: str, version_id: str | None = None):
        import_id = import_id_for(asset_id)
        source = self.imports.manifest(import_id)
        if version_id is None:
            versions = source.get('versions', [])
            if not versions:
                raise KeyError('该资产尚无可分析的数据版本')
            version_id = versions[-1]
        version = self.imports.version(import_id, version_id)
        if version_id not in source.get('versions', []):
            raise SmartImportError('数据资产和版本不匹配')
        return import_id, source, version

    def list_assets(self, limit: int = 100) -> list[dict]:
        results = []
        for imp in self.imports.list_imports()[:limit]:
            source = self.imports.manifest(imp['import_id'])
            if source.get('versions'):
                results.append({'asset_id':asset_id_for(imp['import_id']),
                    'source_kind':'smart_import', 'name':imp['filename'],
                    'version_count':len(source['versions']),
                    'current_version':source['versions'][-1],
                    'created_at':imp['created_at'], 'zero_copy':True})
        return results

    def asset(self, asset_id: str) -> dict:
        import_id, src, v = self._resolve(asset_id)
        return {'asset_id':asset_id,'asset_schema_version':ASSET_VERSION,
                'name':src.get('filename',v['name']),'source_kind':'smart_import',
                'import_id':import_id,'created_at':src['created_at'],
                'current_version':v['version_id'],
                'versions':[{'version_id':x,'source_kind':'smart_import'} for x in src['versions']],
                'zero_copy':True,'backends':['basic_bi','timeseries_bi','native_readonly','generic_bi_reference'],
                'data_policy':'immutable_source; explicit_edit_creates_new_version'}

    def version(self, asset_id: str, version_id: str) -> dict:
        import_id, src, v = self._resolve(asset_id,version_id)
        roles = self.semantics(asset_id,version_id)
        return {'asset_id':asset_id,'version_id':version_id,'name':v['name'],
                'row_count':v['counts']['output_rows'],'series_count':v['series_count'],
                'source_sha256':v['source_sha256'],'data_sha256':v['sha256'],
                'recipe_sha256':v['recipe_sha256'],'fields':list(CSV_COLUMNS),
                'semantic_profile':roles,'created_at':v['created_at'],
                'lineage':{'import_id':import_id,'source_sha256':v['source_sha256'],
                           'recipe':v['recipe']},'zero_copy':True,
                'view_count':len(self.workbench.views(import_id,version_id)),
                'backends':['basic_bi','timeseries_bi','native_readonly','generic_bi_reference']}

    def _semantic_folder(self, asset_id: str, version_id: str) -> Path:
        self._resolve(asset_id,version_id)
        return self.root / asset_id / version_id / 'semantics'

    def semantics(self, asset_id: str, version_id: str) -> dict:
        folder=self._semantic_folder(asset_id,version_id)
        pointer=folder/'current.json'
        if pointer.is_file():
            current=json.loads(pointer.read_text(encoding='utf-8'))
            sha=current.get('revision_sha256')
            if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):
                raise SmartImportError('字段语义版本指针异常')
            record=json.loads((folder/(sha+'.json')).read_text(encoding='utf-8'))
            content={k:v for k,v in record.items() if k!='revision_sha256'}
            actual=hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,
                separators=(',',':')).encode()).hexdigest()
            if actual!=sha:
                raise SmartImportError('字段语义版本 SHA256 校验失败')
            return record
        return {'roles':dict(CANONICAL_ROLES),'confirmed':False,
                'note':'标准转换字段语义，由用户复核后可生成独立语义版本',
                'revision_sha256':None,'source':'system_default'}

    def revise_semantics(self, asset_id: str, version_id: str, spec: SemanticRevision) -> dict:
        import_id,_,meta=self._resolve(asset_id,version_id)
        # A profile is metadata only. It can never change the dataset identity.
        self.imports.version_file(import_id,version_id)
        if not spec.confirmed:
            raise SmartImportError('更改字段语义前需要明确确认')
        data={'asset_id':asset_id,'version_id':version_id,
              'source_sha256':meta['sha256'],'roles':spec.roles,
              'confirmed':True,'note':spec.note,'created_at':datetime.now(timezone.utc).isoformat()}
        digest=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode()).hexdigest()
        folder=self._semantic_folder(asset_id,version_id)
        _write_json(folder/(digest+'.json'),{**data,'revision_sha256':digest})
        _write_json(folder/'current.json',{'revision_sha256':digest})
        return self.semantics(asset_id,version_id)

    def analyze(self, asset_id: str, version_id: str, selection: ViewSelection):
        import_id, _, _ = self._resolve(asset_id,version_id)
        data=self.workbench.analyze(import_id,version_id,selection)
        return {'asset_id':asset_id,**data}

    def grid(self, asset_id: str, version_id: str, request: GridRequest):
        import_id,_,_ = self._resolve(asset_id,version_id)
        fields=request.columns or ['timestamp','series_id','target','available_at',
                                    'statistical_scope','source_name','country','brand','vehicle','quality_flags']
        connection=self.workbench._connect(import_id,version_id)
        try:
            where,args=self.workbench._where(request.selection)
            names=','.join('"'+x+'"' for x in fields)
            query=f'SELECT {names} FROM observations{where} ORDER BY id LIMIT ? OFFSET ?'
            rows=[dict(x) for x in connection.execute(query,[*args,request.limit,request.offset])]
            return {'asset_id':asset_id,'version_id':version_id,'selection':request.selection.canonical(),
                    'columns':fields,'rows':rows,'offset':request.offset,'limit':request.limit}
        finally:
            connection.close()

    def group(self, asset_id: str, version_id: str, request: GroupRequest):
        import_id,_,_ = self._resolve(asset_id,version_id)
        connection=self.workbench._connect(import_id,version_id)
        try:
            where,args=self.workbench._where(request.selection)
            if request.operation != 'count':
                scopes,sources=connection.execute('SELECT COUNT(DISTINCT statistical_scope),'
                    'COUNT(DISTINCT source_name) FROM observations'+where,args).fetchone()
                if (scopes or 0)>1 or (sources or 0)>1:
                    raise SmartImportError('聚合数值需要选择单一统计口径和来源，防止重复或错误加总')
            expression = {'count':'COUNT(*)','sum':'SUM(numeric_target)',
                          'avg':'AVG(numeric_target)','min':'MIN(numeric_target)',
                          'max':'MAX(numeric_target)'}[request.operation]
            q=f'SELECT "{request.group_by}" AS dimension, {expression} AS value, COUNT(*) AS records FROM observations{where} GROUP BY "{request.group_by}" ORDER BY dimension LIMIT ?'
            return {'asset_id':asset_id,'version_id':version_id,
                    'selection':request.selection.canonical(),'group_by':request.group_by,
                    'operation':request.operation,
                    'rows':[dict(x) for x in connection.execute(q,[*args,request.limit])]}
        finally:
            connection.close()

    def pivot(self, asset_id: str, version_id: str, request: PivotRequest):
        """Legacy BI-compatible pivot without cloning a user dataset."""
        import_id,_,_ = self._resolve(asset_id,version_id)
        connection=self.workbench._connect(import_id,version_id)
        try:
            where,args=self.workbench._where(request.selection)
            if request.operation != 'count':
                scopes,sources=connection.execute('SELECT COUNT(DISTINCT statistical_scope),'
                    'COUNT(DISTINCT source_name) FROM observations'+where,args).fetchone()
                if (scopes or 0)>1 or (sources or 0)>1:
                    raise SmartImportError('交叉透视数值运算需要单一统计口径和数据来源')
            expression={'count':'COUNT(*)','sum':'SUM(numeric_target)',
                        'avg':'AVG(numeric_target)','min':'MIN(numeric_target)',
                        'max':'MAX(numeric_target)'}[request.operation]
            q=f'SELECT "{request.row_field}" AS row_name, "{request.column_field}" AS column_name, {expression} AS value, COUNT(*) AS records FROM observations{where} GROUP BY "{request.row_field}", "{request.column_field}" ORDER BY row_name,column_name LIMIT ?'
            return {'asset_id':asset_id,'version_id':version_id,'selection':request.selection.canonical(),
                    'row_field':request.row_field,'column_field':request.column_field,
                    'operation':request.operation,'rows':[dict(x) for x in connection.execute(q,[*args,request.limit])]}
        finally:
            connection.close()

    def views(self, asset_id: str, version_id: str):
        import_id,_,_ = self._resolve(asset_id,version_id)
        return [{**x,'asset_id':asset_id} for x in self.workbench.views(import_id,version_id)]

    def save_view(self, asset_id: str, version_id: str, payload: SaveView):
        import_id,_,_ = self._resolve(asset_id,version_id)
        return {'asset_id':asset_id, **self.workbench.save_view(import_id,version_id,payload)}

    def view(self, asset_id: str, version_id: str, view_id: str):
        import_id,_,_ = self._resolve(asset_id,version_id)
        return {'asset_id':asset_id,**self.workbench.view(import_id,version_id,view_id)}

    def link_legacy_bi(self, asset_id: str, version_id: str,
                       selection: ViewSelection, name: str = '') -> dict:
        """Register a bounded, zero-copy, read-only reference in generic BI.

        Generic BI's pandas operations have an explicit MAX_ROWS memory limit.
        An oversized selection is rejected rather than silently sampled.
        Transforming a linked BI table materializes a new, lineage-tracked
        snapshot, leaving the original SmartImport CSV untouched.
        """
        from timepilot.bi import MAX_ROWS, BIError, BIService
        if self.legacy_bi is None:
            raise SmartImportError('通用 BI 服务尚未配置')
        bi: BIService = self.legacy_bi
        import_id, source, meta = self._resolve(asset_id, version_id)
        # Detect source mutation before creating a durable reference.
        self.imports.version_file(import_id, version_id)
        connection = self.workbench._connect(import_id, version_id)
        try:
            where, params = self.workbench._where(selection)
            count = int(connection.execute('SELECT COUNT(*) FROM observations'+where,params).fetchone()[0])
        finally:
            connection.close()
        if count == 0:
            raise SmartImportError('当前筛选无数据，不创建通用 BI 引用')
        if count > MAX_ROWS:
            raise SmartImportError(
                f'通用 BI 单次处理上限为 {MAX_ROWS:,} 行；当前筛选 {count:,} 行。'
                '请先选择国家、品牌、来源或统计口径，或使用可查询完整数据的基础/原生 BI。')
        canonical = selection.canonical()
        identity = json.dumps({'asset_id':asset_id, 'version_id':version_id,
                               'selection':canonical,'data_sha256':meta['sha256']},
                              ensure_ascii=False,sort_keys=True,separators=(',',':'))
        table_id = str(uuid5(NAMESPACE_URL, 'timepilot:linked-bi:'+identity))
        title = bi._normal_name(name or f'关联资产：{source.get("filename","数据")}')
        lineage = {'type':'data_asset_reference', 'asset_id':asset_id,
                   'version_id':version_id,'selection':canonical,
                   'source_sha256':meta['sha256'], 'readonly_source':True}
        columns=[{'name':n,'type':'numeric' if n=='target' else 'text',
                  'null_count':0,'sample':[]} for n in CSV_COLUMNS]
        with bi.store.sessions.begin() as session:
            existing=session.get(BITableRow,table_id)
            if existing is None:
                existing=BITableRow(id=table_id,name=title,row_count=count,
                    columns_json=json.dumps(columns,ensure_ascii=False),
                    lineage_json=json.dumps(lineage,ensure_ascii=False),sha256=meta['sha256'])
                session.add(existing)
            elif json.loads(existing.lineage_json)!=lineage or existing.row_count!=count:
                raise BIError('通用 BI 已存在同名资产引用，但源数据版本发生不一致')
        return {**bi.table(table_id), 'zero_copy':True,'readonly_source':True,
                'asset_id':asset_id,'version_id':version_id,'selection':canonical}

    def native_sql_source(self, asset_id: str, version_id: str, selection: ViewSelection | None = None) -> str:
        """DuckDB expression for read-only Perspective without copying a Parquet asset.

        This is a trusted internal path, never a user/LLM-selected file.
        """
        import_id,_,_ = self._resolve(asset_id,version_id)
        path=self.imports.version_file(import_id,version_id)
        safe=str(path.resolve()).replace("'","''")
        fields=', '.join('TRY_CAST("target" AS DOUBLE) AS "target"' if name=='target'
                  else '"'+name+'"' for name in CSV_COLUMNS)
        selection = selection or ViewSelection()
        clauses=[]
        # All column names are Pydantic-validated ViewSelection FILTER_FIELDS,
        # and all filter values are quoted by this code, never by an LLM.
        for column,values in selection.canonical()['filters'].items():
            quoted=','.join("'"+v.replace("'","''")+"'" for v in values)
            clauses.append(f'"{column}" IN ({quoted})')
        if selection.date_from:
            clauses.append('SUBSTR("timestamp",1,7) >= '+"'"+selection.date_from+"'")
        if selection.date_to:
            clauses.append('SUBSTR("timestamp",1,7) <= '+"'"+selection.date_to+"'")
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        return (f'SELECT ROW_NUMBER() OVER () - 1 AS "__tp_rowid", {fields} '
                f"FROM read_csv('{safe}', header=true, all_varchar=true)"+where)
