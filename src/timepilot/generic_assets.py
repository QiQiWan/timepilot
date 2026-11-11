"""Lossless (at the CSV value level), project-scoped generic tabular publishing.

A region becomes an immutable, checksum-verified CSV retaining every source
column. It can be imported into the existing Perspective/DuckDB BI without
forcing all input tables through the forecast-only series_id/target schema.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.data_schema import infer_field, summarize_schema
from timepilot.generic_publish_checkpoint import materialize, cleanup, checkpoint_status
from timepilot.smart_import import (
    MAX_CANDIDATE_COLUMNS, SmartImportError, SmartImportService, _csv_rows,
    _column_names, _hash_file, _json_cell, _write_json,
)

FIELD_ROLES = {'time','entity','target','available_at','device','batch','process','event','group'}
MAX_GENERIC_ROWS = 5_000_000


class PublishGeneral(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    confirm: bool = False
    header_row: int | None = Field(default=None, ge=1, le=100)
    roles: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def verify_roles(self):
        if not set(self.roles).issubset(FIELD_ROLES) or any(
            not isinstance(col,str) or not col.strip() or len(col)>256
            for col in self.roles.values()
        ) or len(set(self.roles.values())) != len(self.roles):
            raise ValueError('字段角色映射无效或重复；所有角色必须对应不同的源字段')
        return self


class GenericAssetStore:
    def __init__(self, imports: SmartImportService):
        self.imports = imports
        self.root = imports.root / '_generic_versions'
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _candidate(self, import_id: str, candidate_id: str, header_row: int | None = None):
        manifest = self.imports.manifest(import_id)
        candidate = dict(self.imports._candidate(manifest, candidate_id))
        folder = self.imports._dir(import_id)
        source = next(x for x in manifest['files'] if x['file_id'] == candidate['file_id'])
        source_path = folder / source['stored']
        if not source_path.is_file() or _hash_file(source_path) != source['sha256']:
            raise SmartImportError('原始文件哈希不一致，拒绝发布或预览')
        if header_row is not None and header_row != candidate.get('header_row'):
            suffix = source_path.suffix.lower()
            if suffix == '.parquet':
                raise SmartImportError('Parquet 的表头来自 Schema，不支持手动改变行号')
            if suffix == '.csv':
                from timepilot.smart_import import _csv_reader
                with _csv_reader(source_path) as stream:
                    row = next((r for i, r in enumerate(_csv_rows(stream), 1) if i == header_row), None)
            else:
                from openpyxl import load_workbook
                book = load_workbook(source_path, read_only=True, data_only=False, keep_links=False)
                try:
                    sheet = book[candidate['sheet']]
                    sheet.reset_dimensions()
                    row = next(iter(sheet.iter_rows(min_row=header_row, max_row=header_row,
                                                    max_col=MAX_CANDIDATE_COLUMNS, values_only=True)), None)
                finally:
                    book.close()
            if row is None or not any(x is not None and str(x).strip() for x in row):
                raise SmartImportError('指定表头为空或超出范围，请重新选择表头行')
            from timepilot.table_region_scanner import _columns
            left=int(candidate.get('start_col') or 1)
            right=int(candidate.get('end_col') or MAX_CANDIDATE_COLUMNS)
            aliases,fields=_columns(list(row)[left-1:right],left,header_row,'Excel')
            candidate['columns']=aliases
            candidate['source_fields']=fields
            candidate['source_columns']=[f['source_name'] for f in fields]
            candidate['header_row']=header_row
            candidate['data_start_row']=header_row+1
        if len(candidate['columns']) != len(set(candidate['columns'])):
            raise SmartImportError('数据区域存在重复列名，请修正表头')
        if len(candidate['columns']) > MAX_CANDIDATE_COLUMNS or not candidate['columns']:
            raise SmartImportError('列数超过上限或无法识别')
        return manifest, candidate, source

    def inspect(self, import_id: str, candidate_id: str, *, header_row: int | None = None,
                max_sample: int = 120) -> dict:
        if not 5 <= max_sample <= 500:
            raise SmartImportError('预览行数上限为 5—500')
        manifest, candidate, source = self._candidate(import_id, candidate_id, header_row)
        columns = candidate['columns']
        rows = []
        for _, row in self.imports._rows(self.imports._dir(import_id), manifest, candidate):
            if any(x is not None and str(x).strip() for x in row):
                rows.append(row[:len(columns)])
            if len(rows) >= max_sample:
                break
        fields = summarize_schema(columns, rows, header_row=candidate.get('header_row'))
        source_fields=candidate.get('source_fields') or [{'ordinal':i,'source_name':col,'internal_alias':col,'source_cell':None} for i,col in enumerate(columns)]
        for field,raw in zip(fields,source_fields):
            field.update({k:raw.get(k) for k in ('source_name','source_cell','internal_alias','duplicate_source_name','missing_header')})
        warnings = ([f'存在 {sum(bool(f.get("missing_header")) for f in source_fields)} 个空白表头，已分配内部查询别名']
                    if any(f.get('missing_header') for f in source_fields) else [])
        if any(f.get('duplicate_source_name') for f in source_fields):
            warnings.append('源表存在重复表头，原始名称与原始单元格位置均已保留')
        if any(isinstance(x,str) and x.startswith('=') for row in rows for x in row):
            warnings.append('存在 Excel 公式：发布时仅保留原公式文本，不执行公式计算')
        return {'import_id': import_id, 'candidate_id': candidate_id,
                'source_file': source['filename'], 'source_sha256': source['sha256'],
                'sheet': candidate['sheet'], 'header_row': candidate['header_row'],
                'original_column_count': len(columns), 'columns': fields,
                'source_fields':source_fields,'bbox':candidate.get('bbox'),
                'sampled_rows': len(rows), 'scope': 'sample_only',
                'sample_rows': [dict(zip(columns, [_json_cell(v) for v in r])) for r in rows[:5]],
                'warnings': warnings,
                'next_step': '所有源列按原顺序发布到正式 BI，无需指定业务角色'}

    def _base(self, import_id: str, candidate_id: str, version_id: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{20}', candidate_id) or not re.fullmatch(r'[a-f0-9]{24}', version_id):
            raise SmartImportError('数据区域或版本编号无效')
        return self.root / import_id / candidate_id / version_id

    def publish(self, import_id: str, candidate_id: str, request: PublishGeneral) -> dict:
        if not request.confirm:
            raise SmartImportError('发布正式数据资产前，请确认表头及字段信息')
        manifest, candidate, source = self._candidate(import_id, candidate_id, request.header_row)
        columns = candidate['columns']
        unknown = set(request.roles.values()) - set(columns)
        if unknown:
            raise SmartImportError('已确认的字段角色不在源表中：'+'、'.join(sorted(unknown)))
        # A role is only ever a human-confirmed mapping. Other columns retain
        # unknown/unconfirmed semantic meaning, regardless of model proposals.
        identity = {'source_sha256':source['sha256'],'sheet':candidate['sheet'],
                    'header_row':candidate['header_row'],'columns':columns,
                    'roles':request.roles,
                    'region':candidate.get('bbox'),
                    'field_mapping':candidate.get('source_fields'),
                    'parser':'generic-publish-3' if candidate.get('bbox') else 'generic-publish-2'}  # Display name does not identify content}
        signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        version_id = signature[:24]
        target = self._base(import_id,candidate_id,version_id)
        document = target / 'manifest.json'
        with self._lock:
            if document.exists():
                return self.version(import_id,candidate_id,version_id)
            target.mkdir(parents=True, exist_ok=True)
            # Resume verified chunks only. The source file is SHA-checked by
            # _candidate(), and manifest.json is committed after all bytes.
            result=materialize(target=target,identity_hash=signature,
                source_sha256=source['sha256'],columns=columns,
                rows=lambda:self.imports._rows(self.imports._dir(import_id),manifest,candidate),
                max_rows=MAX_GENERIC_ROWS)
            total=result['row_count']
            fields=summarize_schema(columns,result['sample'],header_row=candidate.get('header_row'))
            for f,raw in zip(fields,candidate.get('source_fields') or []):
                f.update({k:raw.get(k) for k in ('source_name','source_cell','internal_alias','missing_header','duplicate_source_name')})
            for col_idx,item in enumerate(fields):
                n=result['nonempty_counts'][col_idx]
                item['nonempty_count']=n
                item['missing_count']=total-n
                item['missing_fraction']=round((total-n)/total,8)
                item['completeness_scope']='all_published_rows'
            record={'import_id':import_id,'candidate_id':candidate_id,
                    'version_id':version_id,'name':request.name.strip(),
                    'asset_kind':'generic_table','source_file':source['filename'],
                    'source_sheet':candidate['sheet'],'source_sha256':source['sha256'],
                    'source_header_row':candidate.get('header_row'),
                    'source_columns':candidate.get('source_columns',columns),
                    'internal_columns':columns,'source_fields':candidate.get('source_fields'),
                    'source_region':candidate.get('bbox'), 'parser_version':candidate.get('scanner_version','legacy'),
                    'confirmed_roles':request.roles,'columns':fields,
                    'row_count':total,'column_count':len(columns),
                    'data_sha256':result['sha256'],'file':'data.csv',
                    'verified_publish_chunks':result['verified_chunks'],
                    'created_at':datetime.now(timezone.utc).isoformat(),
                    'lineage':{'import_id':import_id,'candidate_id':candidate_id,
                               'source_sha256':source['sha256'],'recipe_sha256':signature},
                    'warnings':([{'kind':'header_review','message':'存在空白或重复源表头，已记录内部别名'}]
                                if any(f.get('missing_header') or f.get('duplicate_source_name') for f in candidate.get('source_fields',[])) else [])}
            _write_json(document,record)
            cleanup(result)
            return record

    def publish_checkpoint(self, import_id: str, candidate_id: str, request: PublishGeneral) -> dict:
        # Do not create a target; this is a read-only recovery/status probe.
        manifest,candidate,source=self._candidate(import_id,candidate_id,request.header_row)
        columns=candidate['columns']
        identity={'source_sha256':source['sha256'],'sheet':candidate['sheet'],
                  'header_row':candidate['header_row'],'columns':columns,
                  'roles':request.roles,'region':candidate.get('bbox'),
                  'field_mapping':candidate.get('source_fields'),
                  'parser':'generic-publish-3' if candidate.get('bbox') else 'generic-publish-2'}
        digest=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return checkpoint_status(self._base(import_id,candidate_id,digest[:24]))

    def version(self, import_id: str, candidate_id: str, version_id: str) -> dict:
        meta = self._base(import_id,candidate_id,version_id)/'manifest.json'
        if not meta.is_file():
            raise KeyError('通用资产版本不存在')
        result=json.loads(meta.read_text(encoding='utf-8'))
        if result.get('version_id')!=version_id:
            raise SmartImportError('通用资产版本元数据损坏')
        return result

    def version_file(self, import_id: str, candidate_id: str, version_id: str) -> Path:
        meta = self.version(import_id,candidate_id,version_id)
        path = self._base(import_id,candidate_id,version_id)/meta['file']
        if not path.is_file() or _hash_file(path)!=meta['data_sha256']:
            raise SmartImportError('通用数据资产完整性校验失败')
        return path

    def list_versions(self, import_id: str, candidate_id: str) -> list[dict]:
        self.imports._candidate(self.imports.manifest(import_id), candidate_id)
        root = self.root / import_id / candidate_id
        if not root.is_dir():return []
        items=[]
        for p in root.iterdir():
            try:items.append(self.version(import_id,candidate_id,p.name))
            except (KeyError,SmartImportError,ValueError):continue
        return sorted(items,key=lambda m:m['created_at'],reverse=True)
