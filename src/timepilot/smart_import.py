"""TimePilot smart data receiving and reproducible structural transformations.

No SQL supplied by a user is executed. File discovery is bounded and its results
are suggestions; metric/scope conflicts remain visible until explicitly resolved.
All uploaded originals and transformed revisions are immutable and content-hashed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import threading
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, BinaryIO, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ZIP_ENTRIES = 300
MAX_MEMBER_BYTES = 250 * 1024 * 1024
MAX_CANDIDATE_COLUMNS = 256
MAX_TRANSFORMED_CELLS = 8_000_000
MAX_GENERIC_REGION_ROWS = 5_000_000
SUPPORTED = {'.xlsx', '.csv', '.parquet'}


def _detect_csv_encoding(path: Path) -> str:
    """Decode CSV headings without replacing undecodable bytes or losing names."""
    with path.open('rb') as stream:
        head=stream.read(131072)
    if head.startswith((b'\xff\xfe', b'\xfe\xff')):
        return 'utf-16'
    # A bounded sample may end inside a multi-byte code point. Omit a few
    # trailing sample bytes when the file continues, while decoding the full
    # CSV strictly when parsing it later.
    preview=head[:-4] if len(head)==131072 else head
    for encoding in ('utf-8-sig','gb18030'):
        try:
            preview.decode(encoding,errors='strict')
            return encoding
        except UnicodeDecodeError:
            continue
    raise SmartImportError('CSV 编码无法可靠识别，请转换为 UTF-8 或 GB18030 后重试')

def _csv_reader(path: Path):
    return path.open('r',encoding=_detect_csv_encoding(path),newline='',errors='strict')

def _csv_rows(stream):
    """Sniff CSV delimiter from its actual content, including TSV and semicolon."""
    first=stream.read(8192)
    stream.seek(0)
    try:
        dialect=csv.Sniffer().sniff(first,delimiters=',;\t|')
    except csv.Error:
        dialect=csv.excel
    return csv.reader(stream,dialect)

MONTH = re.compile(r'^(?:0?([1-9]|1[0-2])\s*(?:月|月份)|(?:month\s*)?0?([1-9]|1[0-2]))$', re.I)
DATE_KEYS = {'time','timestamp','date','datetime','日期','时间','统计月份','统计月','月份','年月'}
YEAR_KEYS = {'year','年份','年度','年'}
QUANTITY_HINTS = ('当月数量','本月数量','数量_辆','销量','产量','销售量','target','value','amount','count')
ROLE_KEYS = {'原始行类别','汇总标签','行类型','统计口径','类别','type'}
CSV_COLUMNS = ['series_id', 'entity_id', 'timestamp', 'target', 'available_at', 'statistical_scope',
               'source_name', 'source_file', 'source_sheet', 'source_row', 'source_cell',
               'source_url', 'record_role', 'country', 'group_name', 'brand', 'vehicle', 'vehicle_type',
               'category', 'powertrain', 'quality_flags', 'raw_value']

class SmartImportError(ValueError):
    pass


class TransformRecipe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_ids: list[str] = Field(min_length=1, max_length=300)
    name: str = Field(default='解析数据', min_length=1, max_length=100)
    metric_column: str | None = Field(default=None, max_length=100)
    allowed_scopes: list[str] = Field(default_factory=list, max_length=30)
    duplicate_policy: Literal['keep_and_flag', 'drop_identical'] = 'keep_and_flag'
    include_aggregate_rows: bool = False
    # Publication time is unknown in historical exports. Do not invent it.
    publication_lag_days: int | None = Field(default=None, ge=0, le=3650)
    # Explicit, reviewable column-to-role mapping. All selected sheets must
    # contain these columns; the agent verifies this before execution.
    field_mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def check_field_mapping(self):
        permitted = {'country','brand','vehicle','group_name','source_name',
                     'statistical_scope','series_id','timestamp','year','target','available_at',
                     'vehicle_type','category','powertrain'}
        if (not set(self.field_mapping).issubset(permitted) or
            any(not isinstance(v,str) or not v.strip() or len(v)>120 for v in self.field_mapping.values())):
            raise ValueError('字段映射只能使用已授权业务角色和有效列名')
        return self


def _json_cell(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        if isinstance(obj, float) and not math.isfinite(obj):
            return None
        return obj
    return str(obj)


def _hash_file(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            dig.update(chunk)
    return dig.hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.writing')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temp, path)


def _column_label(value: Any, number: int) -> str:
    label = str(value).strip() if value is not None else ''
    return label or f'unnamed_{number}'


def _month_number(value: Any) -> int | None:
    if value is None:
        return None
    m = MONTH.fullmatch(str(value).strip())
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _year_value(value: Any) -> int | None:
    try:
        year = int(float(str(value).strip()))
        return year if 1900 <= year <= 2100 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _looks_like_number(value: Any) -> bool:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return True
    if value is None or isinstance(value, (bool, date, datetime)):
        return False
    try:
        Decimal(str(value).replace(',', '').strip())
        return True
    except InvalidOperation:
        return False


def _numeric(value: Any) -> tuple[str, str | None]:
    if value is None or str(value).strip() in {'', '—', '-', '--', '–', 'N/A', 'NA', 'null', 'None'}:
        return '', 'missing'
    try:
        num = Decimal(str(value).replace(',', '').strip())
        if not num.is_finite():
            return '', 'non_numeric'
        flag = 'negative' if num < 0 else None
        return format(num, 'f'), flag
    except InvalidOperation:
        return '', 'non_numeric'


# Publication timestamps are source data, not inferred from the observation period.
PUBLICATION_KEYS = ('available_at', 'available at', '发布时间', '数据发布时间',
                    '发布日期', '可用时间', 'published_at', 'publication_date')
ENTITY_HINT = re.compile(r'^(?:.*(?:series[_ ]?id|item[_ ]?id|entity[_ ]?id|sku|device[_ ]?id|设备编号|产品编号|实体编号|序列标识|物料编码|车型代码|代码|编号))$', re.I)


def _publication_value(raw: Any, *, label: str) -> tuple[str, str]:
    """Validate and preserve a true publication date/time; never fabricate one."""
    if raw is None or str(raw).strip() == '':
        return '', ''
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, date):
        return raw.isoformat(), ''
    elif isinstance(raw, str):
        value_str = raw.strip()
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value_str):
                return date.fromisoformat(value_str).isoformat(), ''
            value = datetime.fromisoformat(value_str.replace('Z', '+00:00'))
        except ValueError as exc:
            raise SmartImportError(f'{label} 的真实发布时间格式不合法：{value_str[:60]}') from exc
    else:
        raise SmartImportError(f'{label} 的发布时间类型不支持；请明确转换规则')
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'), ''
    return value.isoformat(timespec='seconds'), 'publication_timezone_unverified'


def _publication_field(columns: list[str], mapping: dict[str, str]) -> str | None:
    if 'available_at' in mapping:
        return mapping['available_at']
    found = [c for c in columns if c.strip().lower() in PUBLICATION_KEYS]
    if len(found) > 1:
        raise SmartImportError('检测到多个发布时间字段，请在字段映射中明确 available_at')
    return found[0] if found else None


def _guard_entity_mapping(candidate: dict, recipe: 'TransformRecipe') -> None:
    """Prevent opaque entity columns from collapsing distinct series silently."""
    if candidate['kind'] != 'ordinary_timeseries' or 'series_id' in recipe.field_mapping:
        return
    columns = candidate['columns']
    # A2 may be a numeric entity ID: only the confirmed target is exempt.
    mapped_target = recipe.metric_column or recipe.field_mapping.get('target')
    quantities = candidate.get('quantity_columns') or []
    targets = {mapped_target} if mapped_target else (set(quantities) if len(quantities) == 1 else set())
    from timepilot.data_schema import UNKNOWN_HEADER
    samples=candidate.get('sample_rows') or []
    possible_opaque_ids=set()
    temporal_col=candidate.get('time_column')
    temporal_idx=columns.index(temporal_col) if temporal_col in columns else None
    for idx,col in enumerate(columns):
        if not UNKNOWN_HEADER.fullmatch(col.strip()) or col in targets:
            continue
        observed=[str(row[idx]).strip() for row in samples if len(row)>idx and
                  row[idx] is not None and str(row[idx]).strip()]
        # A1, A2, etc. are opaque labels. Numeric signals must not silently
        # become entity IDs; distinguish ID-looking text only from evidence.
        text_identifier=(len(observed)>=2 and len(set(observed))>1 and any(
                not _looks_like_number(v) and not re.fullmatch(r'\d{4}-\d{2}-\d{2}.*',v)
                for v in observed))
        # Duplicate observations at the same timestamp with different values
        # in an otherwise opaque numeric column may represent different
        # entities. A review is required to avoid collapsing those records.
        ambiguous_duplicates=False
        if temporal_idx is not None and len(observed)>=2:
            time_groups=defaultdict(set)
            for row in samples:
                if len(row)>max(temporal_idx,idx) and row[temporal_idx] not in (None,''):
                    time_groups[str(row[temporal_idx])].add(str(row[idx]))
            ambiguous_duplicates=any(len(vals)>1 for vals in time_groups.values())
        if text_identifier or ambiguous_duplicates:
            possible_opaque_ids.add(col)
    omitted=[col for col in columns if (ENTITY_HINT.fullmatch(col.strip()) or col in possible_opaque_ids)
             and col not in recipe.field_mapping.values() and col not in targets]
    if omitted:
        raise SmartImportError('存在未映射的实体或代码字段 ' + '、'.join(omitted[:5]) +
                               '；请先指定 series_id，防止不同实体误合并')

def _column_names(values: list[Any]) -> list[str]:
    names, seen = [], Counter()
    for i, val in enumerate(values):
        label = _column_label(val, i + 1)
        seen[label] += 1
        names.append(label if seen[label] == 1 else f'{label}__{seen[label]}')
    return names


def _shape(columns: list[str], rows: list[list[Any]], sheet_name: str) -> dict:
    months = [{'column': col, 'index': i, 'month': _month_number(col)}
              for i, col in enumerate(columns) if _month_number(col) is not None]
    years = [c for c in columns if c.lower() in YEAR_KEYS]
    from timepilot.data_schema import TIME_NAME, _is_date
    dates = [c for i,c in enumerate(columns)
             if c.lower() in DATE_KEYS or (TIME_NAME.search(c) and
                 any(len(row)>i and _is_date(row[i]) for row in rows[:8]))]
    scopes = [c for c in columns if c == '统计口径' or c.lower() == 'metric_scope']
    quantities = [c for c in columns if any(hint in c.lower() for hint in QUANTITY_HINTS)]
    # Source sheets containing technical notes must not be suggested for training.
    if len(months) >= 3 and years:
        kind = 'wide_monthly'
    elif (years and ('月份' in columns or 'month' in [c.lower() for c in columns]) and quantities):
        kind = 'long_monthly'
    elif dates:
        kind = 'ordinary_timeseries'
    else:
        kind = 'ordinary_table' if len(columns) > 2 else 'metadata'
    if not quantities and kind == 'ordinary_timeseries':
        numeric_candidates = []
        for i, col in enumerate(columns):
            if col in dates or col in scopes: continue
            sample = [row[i] for row in rows[:8] if len(row) > i and row[i] not in ('', None)]
            if sample and sum(_looks_like_number(v) for v in sample) >= max(1, len(sample) * 0.65):
                numeric_candidates.append(col)
        quantities = numeric_candidates
    preferred = bool(kind in {'wide_monthly', 'long_monthly', 'ordinary_timeseries'} and
                     not any(x in sheet_name for x in ('说明','汇总','总计','目录','覆盖','未完成','来源')))
    if kind in {'wide_monthly', 'long_monthly'} and ('汇总' in sheet_name or '来源' in sheet_name):
        preferred = False
    return {'kind': kind, 'month_columns': months, 'year_column': years[0] if years else None,
            'time_column': dates[0] if dates else None,
            'scope_column': scopes[0] if scopes else None, 'quantity_columns': quantities[:10],
            'suggested': preferred,
            'requires_scope_confirmation': kind in ('wide_monthly', 'long_monthly')}


def _header_candidate(rows: list[list[Any]], sheet_name: str) -> tuple[int, list[str], dict]:
    best = (-1, 0, [], {})
    for i, raw in enumerate(rows[:25]):
        if not raw: continue
        cols = _column_names(raw[:MAX_CANDIDATE_COLUMNS])
        valid = [str(v).strip() for v in raw if v is not None and str(v).strip()]
        if not valid: continue
        shape = _shape(cols, rows[i+1:i+9], sheet_name)
        # Avoid dense numeric data rows becoming headers.
        text_count = sum(0 if _looks_like_number(v) else 1 for v in valid)
        # Month-like numeric *data values* must not outscore a genuine header.
        # Only grant a monthly layout bonus when a source year column exists.
        score = text_count * 2 + (len(shape['month_columns']) * 3 if shape['year_column'] else 0)
        score += 8 if shape['kind'] == 'wide_monthly' else 5 if shape['kind'] in {'long_monthly','ordinary_timeseries'} else 0
        if '统计口径' in cols: score += 2
        if score > best[0]: best = (score, i + 1, cols, shape)
    # A field name taken from the source header is verifiable; its business
    # meaning is not. Keep uncertainty visible for opaque columns.
    if best[2]:
        from timepilot.data_schema import UNKNOWN_HEADER, summarize_schema
        code_columns=[col for col in best[2] if UNKNOWN_HEADER.fullmatch(col)]
        proposal=summarize_schema(best[2],rows[best[1]:best[1]+8],header_row=best[1])
        best[3]['header_confidence']='review' if code_columns or best[1]>3 else 'suggested'
        best[3]['opaque_column_count']=len(code_columns)
        best[3]['field_suggestions']=proposal
    return best[1], best[2], best[3]


class SmartImportService:
    def __init__(self, home: Path):
        self.root = Path(home) / 'smart_import'
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._job_mutex = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._running_jobs = 0

    def _dir(self, import_id: str) -> Path:
        try:
            ident = str(UUID(import_id))
        except (TypeError, ValueError):
            raise SmartImportError('导入任务编号无效') from None
        root = self.root / ident
        if not (root / 'manifest.json').is_file():
            raise KeyError('导入任务不存在')
        return root

    def manifest(self, import_id: str) -> dict:
        return json.loads((self._dir(import_id) / 'manifest.json').read_text(encoding='utf-8'))

    def list_imports(self) -> list[dict]:
        entries = []
        for folder in self.root.iterdir():
            file = folder / 'manifest.json'
            if not file.is_file(): continue
            try:
                m = json.loads(file.read_text(encoding='utf-8'))
                entries.append({'import_id':m['import_id'], 'filename': m['filename'],
                                'file_count': len(m['files']), 'candidate_count':len(m['candidates']),
                                'created_at':m['created_at'], 'source_sha256':m['source_sha256']})
            except (KeyError, OSError, ValueError):
                continue
        return sorted(entries, key=lambda x:x['created_at'], reverse=True)

    def _scan_excel(self, physical: Path, logical_name: str, file_id: str,
                    *, checkpoints=None, progress=None, member_sha256=None, unassigned_sink=None) -> list[dict]:
        """Scan every worksheet with a two-pass, bounded-memory region scanner."""
        from openpyxl import load_workbook
        from timepilot.table_region_scanner import discover_regions
        try:
            workbook=load_workbook(physical,read_only=True,data_only=False,keep_links=False)
        except Exception as exc:
            raise SmartImportError(f'{logical_name} 不是可读取的 XLSX：{exc}') from exc
        results=[]
        if checkpoints is not None and member_sha256 is None:
            member_sha256=_hash_file(physical)
        try:
            sheets=workbook.worksheets
            if progress:
                progress('file_started',file_id=file_id,filename=logical_name,total_sheets=len(sheets))
            for index,sheet in enumerate(sheets):
                if progress:progress('sheet_started',file_id=file_id,sheet=sheet.title,index=index)
                cached=checkpoints.get(file_id=file_id,sha256=member_sha256,index=index,
                                       title=sheet.title) if checkpoints is not None else None
                if cached is not None:
                    if unassigned_sink is not None:
                        for c in cached:
                            unassigned_sink.extend(c.get('sheet_unassigned_regions',[]))
                    results.extend(cached)
                    if progress:progress('sheet_completed',file_id=file_id,sheet=sheet.title,index=index,reused=True)
                    continue
                sheet.reset_dimensions()
                def rows():
                    # The same read-only sheet can generate a fresh iterator for each pass.
                    for number,values in enumerate(sheet.iter_rows(values_only=True),start=1):
                        yield number,list(values)
                try:
                    scan=discover_regions(rows,file_id=file_id,logical_file=logical_name,sheet=sheet.title,
                                          max_table_columns=MAX_CANDIDATE_COLUMNS)
                except ValueError as exc:
                    raise SmartImportError(f'{logical_name} / {sheet.title}: {exc}') from exc
                discovered=scan['regions']
                if unassigned_sink is not None:
                    unassigned_sink.extend([{**x,'file_id':file_id,'logical_file':logical_name}
                                            for x in scan['unassigned_regions']])
                # Legacy forecast transform compatibility only. These hints are
                # not used by structural discovery or the default import UI.
                for candidate in discovered:
                    old_shape=_shape(candidate['columns'],candidate['sample_rows'],sheet.title)
                    candidate.update(old_shape)
                    candidate['structural_kind']='generic_table'
                if discovered:
                    discovered[0]['sheet_unassigned_regions']=scan['unassigned_regions']
                if checkpoints is not None:
                    checkpoints.put(file_id=file_id,sha256=member_sha256,index=index,
                                    title=sheet.title,candidates=discovered)
                results.extend(discovered)
                if progress:progress('sheet_completed',file_id=file_id,sheet=sheet.title,index=index,reused=False)
        finally:
            workbook.close()
        return results

    def _scan_csv(self, physical: Path, logical_name: str, file_id: str, *, unassigned_sink=None) -> list[dict]:
        from timepilot.table_region_scanner import discover_regions
        def rows():
            try:
                with _csv_reader(physical) as stream:
                    for n,values in enumerate(_csv_rows(stream),start=1):
                        yield n,values
            except (OSError,csv.Error,UnicodeError) as exc:
                raise SmartImportError(f'CSV 解析失败：{exc}') from exc
        try:
            scan=discover_regions(rows,file_id=file_id,logical_file=logical_name,sheet='CSV',
                                  max_table_columns=MAX_CANDIDATE_COLUMNS)
        except ValueError as exc:
            raise SmartImportError(f'{logical_name}: {exc}') from exc
        discovered=scan['regions']
        if unassigned_sink is not None:
            unassigned_sink.extend([{**x,'file_id':file_id,'logical_file':logical_name}
                                    for x in scan['unassigned_regions']])
        for candidate in discovered:
            candidate.update(_shape(candidate['columns'],candidate['sample_rows'],logical_name))
            candidate['structural_kind']='generic_table'
        if discovered:discovered[0]['sheet_unassigned_regions']=scan['unassigned_regions']
        return discovered

    def _scan_parquet(self, physical: Path, logical_name: str, file_id: str) -> list[dict]:
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise SmartImportError('导入 Parquet 时需要现有 Python 环境安装 pyarrow') from None
        p = pq.ParquetFile(physical)
        all_columns=p.schema_arrow.names
        if len(all_columns)>MAX_CANDIDATE_COLUMNS:
            raise SmartImportError(f'Parquet 有 {len(all_columns)} 列，超过单表 {MAX_CANDIDATE_COLUMNS} 列上限；拒绝静默丢列')
        columns=all_columns
        # A Parquet row group can contain millions of records: read a small
        # batch directly instead of materializing the entire row group.
        sample = next(p.iter_batches(batch_size=8,columns=columns),None)
        sample = sample.to_pylist() if sample is not None else []
        rows = [[r.get(c) for c in columns] for r in sample]
        detail = _shape(columns, rows, logical_name)
        from timepilot.data_schema import summarize_schema, UNKNOWN_HEADER
        detail['field_suggestions']=summarize_schema(columns,rows,header_row=1)
        detail['opaque_column_count']=sum(bool(UNKNOWN_HEADER.fullmatch(c)) for c in columns)
        detail['header_confidence']='source_schema'
        from timepilot.table_region_scanner import _columns
        _,source_fields=_columns(columns,1,None,'Parquet')
        return [{'candidate_id':hashlib.sha256(file_id.encode()).hexdigest()[:20],
                 'file_id':file_id,'logical_file':logical_name,'sheet':'Parquet','header_row':1,
                 'columns':columns,'source_columns':columns,'source_fields':source_fields,
                 'bbox':{'row_start':1,'row_end':p.metadata.num_rows,'col_start':1,'col_end':len(columns)},
                 'start_col':1,'end_col':len(columns),'row_start':1,'row_end':p.metadata.num_rows,
                 'data_start_row':1,'header_rows':[],
                 'rows_estimate':p.metadata.num_rows,'sample_rows':rows, **detail}]

    def ingest(self, handle: BinaryIO, filename: str, *, max_bytes: int = MAX_UPLOAD_BYTES,
               import_id: str | None = None, checkpoint_dir: Path | None = None,
               progress_callback=None) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED | {'.zip'}:
            raise SmartImportError('支持 ZIP、XLSX、CSV 和 Parquet；请先选择有效文件')
        ident = str(UUID(import_id)) if import_id is not None else str(uuid4())
        folder = self.root / ident
        folder.mkdir(parents=True, exist_ok=False)
        try:
            raw = folder / ('source' + suffix)
            digest = hashlib.sha256()
            size=0
            with raw.open('wb') as out:
                while chunk := handle.read(1024 * 1024):
                    size+=len(chunk)
                    if size > max_bytes:
                        raise SmartImportError(f'上传文件超过 {max_bytes//1048576} MiB，未保存')
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0: raise SmartImportError('上传文件为空')
            archive_files = []
            if progress_callback: progress_callback('unpacking', filename=Path(filename).name)
            raw_dir = folder / 'workbooks'
            raw_dir.mkdir()
            if suffix == '.zip':
                try:
                    with zipfile.ZipFile(raw) as z:
                        infos = [x for x in z.infolist() if not x.is_dir() and
                                 Path(x.filename).suffix.lower() in SUPPORTED and
                                 not Path(x.filename).name.startswith(('~$', '.'))]
                        if len(infos) > MAX_ZIP_ENTRIES: raise SmartImportError('ZIP 中可读取文件数量超过安全上限')
                        if sum(x.file_size for x in infos) > MAX_UNCOMPRESSED_BYTES:
                            raise SmartImportError('ZIP 解压后体积超过安全上限')
                        for idx, member in enumerate(infos):
                            if member.file_size > MAX_MEMBER_BYTES:
                                raise SmartImportError(f'ZIP 子文件体积超限：{member.filename}')
                            if member.flag_bits & 1:
                                raise SmartImportError('不支持带密码的 ZIP 文件')
                            if member.compress_size == 0 or member.file_size > member.compress_size * 1000:
                                raise SmartImportError('ZIP 文件包含可疑的高压缩比内容')
                            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                                raise SmartImportError('ZIP 内不支持符号链接')
                            actual = raw_dir / f'{idx:04d}{Path(member.filename).suffix.lower()}'
                            with z.open(member) as fi, actual.open('wb') as fo:
                                shutil.copyfileobj(fi, fo, 1024*1024)
                            archive_files.append((actual,member.filename))
                except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                    raise SmartImportError(f'ZIP 无法读取：{exc}') from exc
            else:
                archive_files.append((raw, Path(filename).name))
            if not archive_files:
                raise SmartImportError('文件内没有可读取的 Excel、CSV 或 Parquet')
            from timepilot.import_checkpoints import WorksheetCheckpoints
            checkpoints = WorksheetCheckpoints(checkpoint_dir) if checkpoint_dir else None
            files, candidates, uncovered = [], [], []
            for i,(file_path,logical) in enumerate(archive_files):
                file_id=f'f{i:04d}'
                entry={'file_id':file_id,'filename':logical,'bytes':file_path.stat().st_size,
                       'sha256':_hash_file(file_path),'stored':str(file_path.relative_to(folder))}
                try:
                    extension=file_path.suffix.lower()
                    entries = (self._scan_excel(file_path,logical,file_id,checkpoints=checkpoints,
                                               progress=progress_callback,member_sha256=entry['sha256'],
                                               unassigned_sink=uncovered) if extension=='.xlsx' else
                               self._scan_csv(file_path,logical,file_id,unassigned_sink=uncovered) if extension=='.csv' else
                               self._scan_parquet(file_path,logical,file_id))
                    if extension!='.xlsx' and progress_callback:
                        progress_callback('file_started',file_id=file_id,filename=logical,total_sheets=1)
                        progress_callback('sheet_completed',file_id=file_id,sheet=extension[1:].upper(),
                                          index=0,reused=False)
                    candidates.extend(entries)
                    if extension=='.xlsx':
                        from openpyxl import load_workbook
                        details=load_workbook(file_path,read_only=True,data_only=True,keep_links=False)
                        try:entry['worksheets']=[{'name':ws.title,'index':idx,'state':ws.sheet_state}
                                                 for idx,ws in enumerate(details.worksheets)]
                        finally:details.close()
                    else:entry['worksheets']=[{'name':extension[1:].upper(),'index':0,'state':'visible'}]
                    entry['sheet_count']=len(entry['worksheets'])
                    entry['region_count']=len(entries)
                except SmartImportError as exc:
                    # Known malformed source files can be summarized as a
                    # warning. Transient OS/disk I/O errors must escape so
                    # the worker can resume from per-sheet checkpoints.
                    entry['warning']=str(exc)
                    entry['sheet_count']=0
                files.append(entry)
            if progress_callback: progress_callback('assembling')
            if not candidates: raise SmartImportError('文件均未找到可识别的数据区域')
            groups: dict[tuple[str,str,str],list[str]] = defaultdict(list)
            for c in candidates:
                # Same source file/sheet form a navigable group; no semantic merger.
                key=(c['file_id'],c['sheet'],hashlib.sha1('|'.join(c['columns']).encode()).hexdigest()[:12])
                groups[key].append(c['candidate_id'])
            group_list=[]
            for (file_id,sheet,sha),ids in groups.items():
                example=next(x for x in candidates if x['candidate_id']==ids[0])
                group_list.append({'group_id':sha,'sheet':sheet,'kind':example['kind'],'candidate_ids':ids,
                                   'files':len(ids),'suggested':example['suggested'],
                                   'columns':example['columns']})
            recommended=None  # All discovered regions are selected by default.
            manifest={'import_id':ident,'filename':Path(filename).name,'source_sha256':digest.hexdigest(),
                      'source_bytes':size,'created_at':datetime.now().astimezone().isoformat(),
                      'files':files,'candidates':candidates,'groups':group_list,
                      'parser_version':'2.2.1','unassigned_regions':uncovered,
                      'suggested_candidate_ids':[c['candidate_id'] for c in candidates if c.get('suggested')],
                      'versions':[],'notices':['统计口径与来源不允许自动混合；跨来源冲突保留供核实',
                      '未提供真实 available_at，系统不会默认填充历史数据发布时间']}
            _write_json(folder / 'manifest.json',manifest)
            return manifest
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def ingest_many(self, attachments: list[tuple[BinaryIO, str]], *, import_id: str | None = None,
                    checkpoint_dir: Path | None = None, progress_callback=None) -> dict:
        """Bundle separately uploaded files without allocating an extra Python environment.

        A single ZIP is handled by ingest(). Multiple Excel/CSV/Parquet uploads
        are copied in bounded chunks into one safe internal archive.
        """
        if not attachments or len(attachments) > MAX_ZIP_ENTRIES:
            raise SmartImportError('请上传 1—300 个数据文件')
        if len(attachments) == 1:
            stream, name = attachments[0]
            return self.ingest(stream, name, import_id=import_id,
                               checkpoint_dir=checkpoint_dir, progress_callback=progress_callback)
        total = 0
        with tempfile.TemporaryFile(mode='w+b') as packed:
            with zipfile.ZipFile(packed, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
                for n, (handle, raw_name) in enumerate(attachments):
                    leaf = raw_name.replace('\\', '/').split('/')[-1]
                    ext = Path(leaf).suffix.lower()
                    if ext not in SUPPORTED:
                        raise SmartImportError('批量上传支持 XLSX、CSV、Parquet；已有 ZIP 请单独上传')
                    # Store all members under generated names; never trust client paths.
                    with archive.open(f'{n:04d}{ext}', 'w') as out:
                        file_bytes = 0
                        while data := handle.read(1024 * 1024):
                            total += len(data)
                            file_bytes += len(data)
                            if total > MAX_UPLOAD_BYTES or file_bytes > MAX_MEMBER_BYTES:
                                raise SmartImportError('批量上传文件超过安全大小上限')
                            out.write(data)
            packed.seek(0)
            result = self.ingest(packed, 'multi_file_upload.zip', import_id=import_id,
                                 checkpoint_dir=checkpoint_dir, progress_callback=progress_callback)
        # The safe archive filenames are mapped back to user-visible names.
        original_names = [name.replace('\\','/').split('/')[-1] for _,name in attachments]
        for item in result['files']:
            n = int(item['file_id'][1:])
            item['filename'] = original_names[n]
        for item in result['candidates']:
            n = int(item['file_id'][1:])
            item['logical_file'] = original_names[n]
        _write_json(self._dir(result['import_id']) / 'manifest.json', result)
        return result

    def revise_region(self,import_id:str,candidate_id:str,*,bbox:dict|None=None,
                      header_row:int|None=None)->dict:
        """Create a new auditable region revision without mutating prior versions."""
        from timepilot.table_region_scanner import _columns
        with self._mutex:
            manifest=self.manifest(import_id)
            old=self._candidate(manifest,candidate_id)
            if old.get('superseded'):
                raise SmartImportError('区域已修订，请重新加载目录')
            if old['sheet']=='Parquet':
                raise SmartImportError('Parquet 的列名来自文件 Schema，不支持修改表头位置')
            source=next(x for x in manifest['files'] if x['file_id']==old['file_id'])
            path=self._dir(import_id)/source['stored']
            if _hash_file(path)!=source['sha256']:
                raise SmartImportError('源文件哈希发生变化，拒绝修订')
            current=dict(old.get('bbox') or {})
            if bbox is not None:
                if not isinstance(bbox,dict) or set(bbox)!={'row_start','row_end','col_start','col_end'}:
                    raise SmartImportError('区域边界必须包含四个整数坐标')
                current=dict(bbox)
            try:
                first=int(current['row_start']);last=int(current['row_end'])
                left=int(current['col_start']);right=int(current['col_end'])
            except (ValueError,KeyError,TypeError):
                raise SmartImportError('当前源表没有可修订的区域坐标') from None
            if (min(first,left)<1 or last<first or right<left or right-left+1>MAX_CANDIDATE_COLUMNS
                    or last-first+1>MAX_GENERIC_REGION_ROWS or last>10_000_000 or right>16384):
                raise SmartImportError('修订区域范围非法或超出安全上限')
            header=header_row if header_row is not None else old.get('header_row')
            if header is not None and (type(header) is not int or not first<=header<=last):
                raise SmartImportError('表头行不在当前区域内')
            if header is not None:
                if path.suffix.lower()=='.csv':
                    with _csv_reader(path) as stream:
                        row=next((v for n,v in enumerate(_csv_rows(stream),1) if n==header),None)
                    values=(row or [])[left-1:right]
                else:
                    from openpyxl import load_workbook
                    book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
                    try:
                        sheet=book[old['sheet']];sheet.reset_dimensions()
                        values=list(next(iter(sheet.iter_rows(min_row=header,max_row=header,min_col=left,
                                                  max_col=right,values_only=True)),[]))
                    finally:book.close()
                if not any(v is not None and str(v).strip() for v in values):
                    raise SmartImportError('选定表头行完全为空，请重新选择')
            else:values=[None]*(right-left+1)
            aliases,raw_fields=_columns(values,left,header,'Excel')
            next_data=header+1 if header else first
            if next_data>last:raise SmartImportError('区域只有表头，没有可发布的记录')
            payload={'source_sha256':source['sha256'],'sheet':old['sheet'],'bbox':current,
                     'header_row':header,'scanner':'manual-review-2.2.1'}
            new_id=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
            existing=next((c for c in manifest['candidates'] if c['candidate_id']==new_id),None)
            if existing:return existing
            revised={**old,'candidate_id':new_id,'region_id':new_id,'bbox':current,
                     'start_col':left,'end_col':right,'row_start':first,'row_end':last,
                     'header_row':header,'header_rows':[header] if header else [],
                     'data_start_row':next_data,'columns':aliases,
                     'source_columns':[x['source_name'] for x in raw_fields],
                     'source_fields':raw_fields,'rows_estimate':last-next_data+1,
                     'supersedes_candidate_id':candidate_id,'header_confidence':'human_confirmed',
                     'needs_review':False,'scanner_version':'manual-review-2.2.1',
                     'sample_rows':[]}
            # Read small samples only from the selected data window.
            for n,values in self._rows(self._dir(import_id),manifest,revised):
                if any(v is not None and str(v).strip() for v in values):
                    revised['sample_rows'].append([_json_cell(v) for v in values])
                    if len(revised['sample_rows'])>=6:break
            old['superseded']=True
            manifest['candidates'].append(revised)
            manifest['suggested_candidate_ids']=[x for x in manifest.get('suggested_candidate_ids',[]) if x!=candidate_id]+[new_id]
            manifest.setdefault('layout_revisions',[]).append({'source_candidate_id':candidate_id,
                 'new_candidate_id':new_id,'bbox':current,'header_row':header,
                 'created_at':datetime.now(timezone.utc).isoformat()})
            _write_json(self._dir(import_id)/'manifest.json',manifest)
            return revised

    def start_transform(self, import_id: str, recipe: TransformRecipe) -> dict:
        """Run a large structural conversion off the HTTP request thread.

        One active conversion per process bounds disk/CPU contention. The immutable
        output survives process restart; an interrupted job is never marked done.
        """
        folder = self._dir(import_id)
        job_id = str(uuid4())
        with self._job_mutex:
            if self._running_jobs >= 1:
                raise SmartImportError('已有数据转换任务正在运行，请等待当前任务完成')
            self._running_jobs += 1
            job = {'job_id':job_id,'import_id':import_id,'status':'queued',
                   'completed_candidates':0,'total_candidates':len(recipe.candidate_ids),
                   'output_rows':0,'version_id':None,'error':None}
            self._jobs[job_id]=job
            _write_json(folder / 'jobs' / f'{job_id}.json', job)

        def update(**fields):
            with self._job_mutex:
                job.update(fields)
                _write_json(folder / 'jobs' / f'{job_id}.json', job)

        def run():
            update(status='running')
            try:
                result = self.transform(import_id, recipe, progress_callback=update)
                update(status='completed',version_id=result['version_id'],
                       completed_candidates=len(recipe.candidate_ids),
                       output_rows=result['counts']['output_rows'])
            except Exception as exc:
                update(status='failed',error=str(exc)[:1200])
            finally:
                with self._job_mutex:
                    self._running_jobs -= 1
        threading.Thread(target=run,name='timepilot-smart-import',daemon=True).start()
        return dict(job)

    def transform_job(self, import_id: str, job_id: str) -> dict:
        folder=self._dir(import_id)
        try:
            ident=str(UUID(job_id))
        except (ValueError,TypeError):
            raise SmartImportError('转换任务编号无效') from None
        with self._job_mutex:
            if ident in self._jobs and self._jobs[ident]['import_id']==import_id:
                return dict(self._jobs[ident])
        path=folder / 'jobs' / f'{ident}.json'
        if not path.is_file():
            raise KeyError('转换任务不存在')
        job=json.loads(path.read_text(encoding='utf-8'))
        if job['status'] in {'queued','running'}:
            job['status']='interrupted'
            job['error']='服务已重启。已完成的正式数据版本仍保留，可重新执行转换。'
        return job

    def _candidate(self, manifest: dict, candidate_id: str) -> dict:
        for c in manifest['candidates']:
            if c['candidate_id']==candidate_id: return c
        raise SmartImportError(f'数据区域不存在：{candidate_id}')

    def _rows(self, folder: Path, manifest: dict, candidate: dict):
        """Yield only the selected region, with absolute source row coordinates.

        Old manifests without a bounding box keep their original row reader.
        """
        source=next(x for x in manifest['files'] if x['file_id']==candidate['file_id'])
        path=folder/source['stored']
        header=candidate.get('header_row') or 0
        first=int(candidate.get('data_start_row') or header+1)
        end=int(candidate.get('row_end') or 0)
        left=int(candidate.get('start_col') or 1)
        right=int(candidate.get('end_col') or 0)
        ext=path.suffix.lower()
        if ext=='.xlsx':
            from openpyxl import load_workbook
            book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
            try:
                sheet=book[candidate['sheet']]
                sheet.reset_dimensions()
                args={'min_row':first,'min_col':left,'values_only':True}
                if end:args['max_row']=end
                if right:args['max_col']=right
                for number,values in enumerate(sheet.iter_rows(**args),start=first):
                    yield number,list(values)
            finally:book.close()
        elif ext=='.csv':
            with _csv_reader(path) as stream:
                for number,values in enumerate(_csv_rows(stream),start=1):
                    if number<first:continue
                    if end and number>end:break
                    yield number,values[left-1:right] if right else values[left-1:]
        else:
            try:import pyarrow.parquet as pq
            except ImportError:raise SmartImportError('Parquet 需要 pyarrow') from None
            for batch in pq.ParquetFile(path).iter_batches(batch_size=4096):
                for item in batch.to_pylist():
                    yield None,[item.get(c) for c in candidate['columns']]

    def preview_region(self, import_id: str, candidate_id: str, limit: int = 15) -> dict:
        manifest=self.manifest(import_id)
        folder=self._dir(import_id)
        c=self._candidate(manifest,candidate_id)
        from itertools import islice
        sample=[{'excel_row':i,'values':dict(zip(c['columns'],[_json_cell(v) for v in row]))}
                for i,row in islice(self._rows(folder,manifest,c),max(1,min(50,limit)))]
        return {'candidate':c,'rows':sample}

    def _records(self, candidate: dict, row_num: int | None, row: list[Any], recipe: TransformRecipe):
        cols=candidate['columns']
        values={col:row[i] if i<len(row) else None for i,col in enumerate(cols)}
        if all(value is None or str(value).strip()=='' for value in row): return
        override=recipe.field_mapping
        scope_name=override.get('statistical_scope') or candidate['scope_column']
        scope=str(values.get(scope_name) or '未标注').strip() if scope_name else '未标注'
        if recipe.allowed_scopes and scope not in recipe.allowed_scopes: return
        source_url=str(values.get('来源网址') or values.get('来源定位') or '')
        source_name=str((values.get(override['source_name']) or '未标注') if 'source_name' in override else (
                        values.get('来源') or values.get('数据来源') or
                        urlsplit(source_url).hostname or
                        re.sub(r'20[0-9]{2}', '', Path(candidate['logical_file']).stem))).strip()
        role_text = str(values.get('原始行类别') or values.get('行类型') or values.get('汇总标签') or '')
        record_role = ('aggregate' if any(x in role_text.lower() for x in ('aggregate','total','汇总','合计'))
                       else 'detail')
        if record_role == 'aggregate' and not recipe.include_aggregate_rows:
            return
        dims={
          'country':values.get('国家/地区') or values.get('销售市场') or values.get('国家'),
          'group_name':values.get('集团') or values.get('厂商'),
          'brand':values.get('整车厂/品牌') or values.get('品牌'),
          'vehicle':values.get('车型') or values.get('车系'),
          'vehicle_type':values.get('车种') or values.get('分类原标签'),
          'category':values.get('级别'),
          'powertrain':values.get('动力总成') or values.get('动力原标签'),
        }
        for key in dims:
            if key in override:
                dims[key]=values.get(override[key])
            if isinstance(dims[key],str): dims[key]=dims[key].strip()
        explicit_series=values.get(override['series_id']) if 'series_id' in override else None
        if isinstance(explicit_series,str):explicit_series=explicit_series.strip()
        if 'series_id' in override and (explicit_series is None or explicit_series == ''):
            raise SmartImportError(f"{candidate['logical_file']} 第 {row_num or '?'} 行实体标识为空，不能合并到匿名序列")
        entity_components=[scope,*[_json_cell(v) for v in dims.values()]]
        if explicit_series is not None: entity_components.append(_json_cell(explicit_series))
        # Unknown anonymous entities from unrelated sources must not be linked.
        if explicit_series is None and not any(dims.values()):
            entity_components.append(source_name)
        entity_id=hashlib.sha256(json.dumps(entity_components, ensure_ascii=False).encode()).hexdigest()[:20]
        identity=[source_name,scope,*[_json_cell(v) for v in dims.values()]]
        if explicit_series is not None: identity.append(_json_cell(explicit_series))
        series_id=hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
        publication_field=_publication_field(cols,override)
        published,publication_flag=_publication_value(values.get(publication_field) if publication_field else None,
            label=f"{candidate['logical_file']} / {candidate['sheet']} 第 {row_num or '?'} 行")
        common={'series_id':series_id,'entity_id':entity_id,'available_at':published,'statistical_scope':scope,
                'source_name':source_name,'source_file':candidate['logical_file'],
                'source_sheet':candidate['sheet'],'source_row':row_num or '', 'source_url':source_url,
                'record_role':record_role,
                **{k:_json_cell(v) or '' for k,v in dims.items()}}
        if candidate['kind']=='wide_monthly':
            year=_year_value(values.get(override.get('year') or candidate['year_column']))
            if year is None: return
            for m in candidate['month_columns']:
                month=m['month']
                v=values.get(m['column'])
                raw=_json_cell(v)
                num,flag=_numeric(v)
                col_idx=m['index']
                # Excel coordinate is preserved, not just a reconstructed label.
                from openpyxl.utils import get_column_letter
                address=get_column_letter(col_idx+1)+(str(row_num) if row_num else '')
                yield {**common,'timestamp':f'{year:04d}-{month:02d}-01','target':num,
                       'source_cell':address,'raw_value':raw if raw is not None else '',
                       'quality_flags':';'.join(x for x in (flag, publication_flag, 'scope_unverified' if '待核实' in scope else '') if x)}
        elif candidate['kind']=='long_monthly':
            year=_year_value(values.get(override.get('year') or candidate['year_column']))
            month_raw=values.get('月份') or values.get('month')
            try: month=int(month_raw)
            except (TypeError,ValueError): return
            if year is None or not 1<=month<=12:return
            metric=recipe.metric_column or override.get('target') or next((x for x in candidate['quantity_columns'] if x in values),None)
            if metric is None:return
            num,flag=_numeric(values[metric]);idx=cols.index(metric)
            from openpyxl.utils import get_column_letter
            yield {**common,'timestamp':f'{year:04d}-{month:02d}-01','target':num,
                   'source_cell':get_column_letter(idx+1)+str(row_num or ''),
                   'raw_value':_json_cell(values[metric]) or '',
                   'quality_flags':';'.join(x for x in (flag, publication_flag, 'scope_unverified' if '待核实' in scope else '') if x)}
        elif candidate['kind']=='ordinary_timeseries':
            timestamp=values.get(override.get('timestamp') or candidate['time_column'])
            if isinstance(timestamp, datetime):
                period=timestamp.isoformat(timespec='microseconds' if timestamp.microsecond else 'seconds')
            elif isinstance(timestamp, date):
                period=timestamp.isoformat()
            else:
                try:
                    # A numeric timestamp needs an explicit epoch/unit mapping;
                    # pandas would otherwise interpret it as nanoseconds.
                    if isinstance(timestamp,(int,float)) or (isinstance(timestamp,str) and
                            re.fullmatch(r'\d{10,16}',timestamp.strip())):
                        raise ValueError('数值时间戳缺少单位')
                    import pandas as pd
                    parsed=pd.to_datetime(timestamp,errors='raise')
                    period=(parsed.date().isoformat() if isinstance(timestamp,str) and
                            re.fullmatch(r'\d{4}-\d{2}-\d{2}',timestamp.strip())
                            else parsed.isoformat())
                except (ValueError,TypeError,OverflowError) as exc:
                    raise SmartImportError(f"{candidate['logical_file']} 第 {row_num or '?'} 行的时间字段无法解析；数值时间戳须先明确秒/毫秒单位") from exc
            metric=recipe.metric_column or override.get('target') or next((x for x in candidate['quantity_columns'] if x in values),None)
            if metric is None:return
            num,flag=_numeric(values[metric]);idx=cols.index(metric)
            from openpyxl.utils import get_column_letter
            yield {**common,'timestamp':period,'target':num,
                   'source_cell':get_column_letter(idx+1)+str(row_num or ''),
                   'raw_value':_json_cell(values[metric]) if values[metric] is not None else '', 'quality_flags':';'.join(x for x in (flag,publication_flag) if x)}

    def transform(self, import_id: str, recipe: TransformRecipe, *, progress_callback=None) -> dict:
        folder=self._dir(import_id)
        source=self.manifest(import_id)
        chosen={x: self._candidate(source,x) for x in recipe.candidate_ids}
        if len(chosen)!=len(recipe.candidate_ids): raise SmartImportError('数据区域不能重复选择')
        if any(c['kind'] not in {'wide_monthly','long_monthly','ordinary_timeseries'} for c in chosen.values()):
            raise SmartImportError('说明页和普通统计表暂不能直接转换为时间序列')
        if len({c['kind'] for c in chosen.values()})>1:
            raise SmartImportError('请分别转换不同结构的数据区域，避免错误拼接')
        for candidate in chosen.values():
            missing=[f'{role}: {column}' for role,column in recipe.field_mapping.items()
                     if column not in candidate['columns']]
            if missing:
                raise SmartImportError('字段映射不存在于所选工作表：'+'、'.join(missing[:6]))
            if recipe.metric_column and recipe.metric_column not in candidate['columns']:
                raise SmartImportError('指定的目标数值列在所选工作表中不存在')
            _guard_entity_mapping(candidate,recipe)
        signature=hashlib.sha256((source['source_sha256']+'|'+recipe.model_dump_json(exclude_none=False)+
                                  '|smart-import-0.9.0').encode()).hexdigest()
        version=signature[:24]
        outdir=folder/'versions'/version
        final=outdir/'manifest.json'
        with self._mutex:
            if final.is_file():return json.loads(final.read_text(encoding='utf-8'))
            outdir.mkdir(parents=True,exist_ok=True)
            output=outdir/'normalized.csv'
            temp=outdir/'normalized.partial.csv'
            counts=Counter()
            scopes=Counter();sources=Counter();periods=Counter();series=set()
            last_valid_by_series={}  # bounded to one numeric point per discovered series
            # A disk-backed identity index prevents unbounded dedupe memory use.
            import sqlite3
            index=outdir/'duplicate_index.sqlite3'
            # A power loss may leave an uncommitted disk-backed dedupe index.
            # Recompute it from the beginning; it is derived state only.
            index.unlink(missing_ok=True)
            (outdir/'duplicate_index.sqlite3-shm').unlink(missing_ok=True)
            (outdir/'duplicate_index.sqlite3-wal').unlink(missing_ok=True)
            con=sqlite3.connect(index)
            con.execute('CREATE TABLE IF NOT EXISTS entries (identity TEXT PRIMARY KEY, value TEXT NOT NULL, source TEXT NOT NULL)')
            con.execute('PRAGMA journal_mode=WAL')
            try:
                with temp.open('w',encoding='utf-8',newline='') as stream:
                    writer=csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
                    writer.writeheader()
                    for candidate_number,c in enumerate(sorted(chosen.values(),key=lambda x:(x['logical_file'],x['sheet'])), start=1):
                        for row_num,row in self._rows(folder,source,c):
                            for rec in self._records(c,row_num,row,recipe):
                                counts['cells_scanned']+=1
                                if counts['cells_scanned']>MAX_TRANSFORMED_CELLS:
                                    raise SmartImportError('结构转换超过 800 万个单元格上限，请分批转换')
                                if rec['available_at']:
                                    try:
                                        published_day=date.fromisoformat(rec['available_at'][:10])
                                        observed_day=date.fromisoformat(rec['timestamp'][:10])
                                    except ValueError as exc:
                                        raise SmartImportError('真实发布时间或观测日期无法校验') from exc
                                    if published_day < observed_day:
                                        raise SmartImportError(f"数据发布时间早于观测日：{rec['source_file']} {rec['source_sheet']} {rec['source_cell']}")
                                    counts['real_publication_times']+=1
                                elif recipe.publication_lag_days is not None:
                                    rec['available_at']=(date.fromisoformat(rec['timestamp'][:10]) +
                                      timedelta(days=recipe.publication_lag_days)).isoformat()
                                    rec['quality_flags']=(rec['quality_flags']+';assumed_publication').strip(';')
                                    counts['assumed_publication_times']+=1
                                else:
                                    counts['missing_publication_times']+=1
                                flags=[f for f in str(rec['quality_flags']).split(';') if f]
                                val=rec['target']
                                if not val:counts['missing_values']+=1
                                if 'negative' in flags:counts['negative_values']+=1
                                if 'non_numeric' in flags:counts['non_numeric_values']+=1
                                if 'scope_unverified' in flags:counts['unverified_scope_values']+=1
                                if val:
                                    if Decimal(val) == 0: counts['zero_values'] += 1
                                    # Consecutive-month, factor-of-five jump/drop is a
                                    # REVIEW candidate, not a claim of an erroneous value.
                                    previous=last_valid_by_series.get(rec['series_id'])
                                    ym=rec['timestamp'][:7]
                                    if previous and len(ym)==7 and ym[4]=='-':
                                        prev_ym,prev_num=previous
                                        year,month=int(ym[:4]),int(ym[5:7])
                                        expected=f'{year-1:04d}-12' if month==1 else f'{year:04d}-{month-1:02d}'
                                        numeric=Decimal(val)
                                        if prev_ym==expected and prev_num > 0 and numeric >= 0 and abs(numeric-prev_num)>=100:
                                            if numeric >= 5*prev_num:
                                                flags.append('abrupt_increase_review')
                                                counts['abrupt_change_review']+=1
                                            elif numeric*5 <= prev_num:
                                                flags.append('abrupt_decrease_review')
                                                counts['abrupt_change_review']+=1
                                    last_valid_by_series[rec['series_id']] = (ym,Decimal(val))
                                # Entity identity includes an explicit mapped series code.
                                # Excluding source allows cross-provider review of the SAME
                                # entity, while different entities on the same date stay apart.
                                identity=hashlib.sha256(json.dumps([rec['entity_id'], rec['timestamp'],
                                    rec['statistical_scope']], ensure_ascii=False).encode()).hexdigest()
                                # The common case is a new observation. INSERT OR IGNORE
                                # avoids a SELECT on every cell in large multi-year archives.
                                inserted=con.execute('INSERT OR IGNORE INTO entries VALUES (?,?,?)',
                                                     (identity,val,rec['source_name']))
                                if inserted.rowcount == 0:
                                    present=con.execute('SELECT value,source FROM entries WHERE identity=?',
                                                        (identity,)).fetchone()
                                    if present and present[0]==val:
                                        counts['identical_overlap']+=1
                                        flags.append('duplicate_identical')
                                        if recipe.duplicate_policy=='drop_identical':
                                            counts['dropped_identical']+=1
                                            continue
                                    elif present:
                                        counts['conflicting_overlap']+=1
                                        flags.append('conflict_review')
                                rec['quality_flags']=';'.join(dict.fromkeys(flags))
                                writer.writerow(rec)
                                counts['output_rows']+=1
                                scopes[rec['statistical_scope']]+=1
                                sources[rec['source_name']]+=1
                                periods[rec['timestamp'][:7]]+=1
                                series.add(rec['series_id'])
                        if progress_callback:
                            progress_callback(completed_candidates=candidate_number,
                                              total_candidates=len(chosen),
                                              output_rows=counts['output_rows'])
                if counts['output_rows'] == 0:
                    raise SmartImportError('所选区域没有可转换的数据，请检查工作表与统计口径')
                con.commit()
                os.replace(temp,output)
                meta={'import_id':import_id,'version_id':version,'name':recipe.name,
                      'source_sha256':source['source_sha256'],
                      'recipe':recipe.model_dump(mode='json'),
                      'recipe_sha256':hashlib.sha256(recipe.model_dump_json().encode()).hexdigest(),
                      'file':'normalized.csv','sha256':_hash_file(output),
                      'bytes':output.stat().st_size,'counts':dict(counts),
                      'series_count':len(series),'statistical_scopes':dict(scopes),
                      'source_counts':dict(sources),'period_min':min(periods,default=None),
                      'period_max':max(periods,default=None),
                      'created_at':datetime.now().astimezone().isoformat(),
                      'quality_rates':{'missing_fraction':round(counts['missing_values']/max(1,counts['cells_scanned']),5),
                                       'negative_fraction':round(counts['negative_values']/max(1,counts['cells_scanned']),5),
                                       'conflicting_overlap_fraction':round(counts['conflicting_overlap']/max(1,counts['cells_scanned']),5)},
                      'warnings':(['不同统计口径仍分别保留，使用预测前需要选择口径'] if len(scopes)>1 else [])+
                                 (['检测到重合数据，冲突记录全部保留供核实'] if counts['conflicting_overlap'] else [])+
                                 (['部分记录缺少发布时间，不能直接进行防泄漏回测'] if counts['missing_publication_times'] else [])+
                                 (['部分记录使用发布时间假定，训练前应复核'] if counts['assumed_publication_times'] else [])}
                _write_json(final,meta)
                # Input manifest is a navigational index; immutable version manifests remain authoritative.
                if version not in source['versions']:
                    source['versions'].append(version)
                    _write_json(folder/'manifest.json',source)
                return meta
            except Exception:
                shutil.rmtree(outdir,ignore_errors=True)
                raise
            finally:
                con.close()
                index.unlink(missing_ok=True)
                (outdir/'duplicate_index.sqlite3-shm').unlink(missing_ok=True)
                (outdir/'duplicate_index.sqlite3-wal').unlink(missing_ok=True)

    def version(self, import_id: str, version_id: str) -> dict:
        if not re.fullmatch(r'[a-f0-9]{24}',version_id): raise SmartImportError('数据版本编号无效')
        path=self._dir(import_id)/'versions'/version_id/'manifest.json'
        if not path.is_file():raise KeyError('数据版本不存在')
        return json.loads(path.read_text(encoding='utf-8'))

    def version_file(self, import_id: str, version_id: str) -> Path:
        meta=self.version(import_id,version_id)
        path=self._dir(import_id)/'versions'/version_id/meta['file']
        if not path.is_file() or _hash_file(path)!=meta['sha256']:
            raise SmartImportError('转换输出文件缺失或 SHA256 校验失败')
        return path

    def preview_version(self, import_id: str, version_id: str, *, limit: int = 50,
                        offset: int = 0, scopes: list[str] | None = None,
                        sources: list[str] | None = None, series_id: str = '') -> dict:
        path=self.version_file(import_id,version_id)
        limit=min(500,max(1,limit));offset=min(5_000_000,max(0,offset))
        from itertools import islice
        rows=[]; matched=0
        with path.open('r',encoding='utf-8',newline='') as f:
            for row in csv.DictReader(f):
                if scopes and row['statistical_scope'] not in scopes:continue
                if sources and row['source_name'] not in sources:continue
                if series_id and row['series_id']!=series_id:continue
                if matched>=offset:
                    rows.append(row)
                    if len(rows)>=limit:break
                matched+=1
        return {'version':self.version(import_id,version_id),'rows':rows,'offset':offset,'limit':limit}

    def basic_pivot(self, import_id: str, version_id: str, *, group: str='timestamp',
                    scopes: list[str] | None=None, sources: list[str] | None=None,
                    series_id: str='', limit: int=80) -> dict:
        # Lightweight streaming aggregation for fallback BI when DuckDB/Perspective is unavailable.
        allowed={'timestamp','statistical_scope','country','brand','vehicle','source_name','series_id'}
        if group not in allowed:raise SmartImportError('该字段不能用于基础 BI 聚合')
        accum: dict[str,list[Decimal|int]]={}
        with self.version_file(import_id,version_id).open('r',encoding='utf-8',newline='') as f:
            for row in csv.DictReader(f):
                if scopes and row['statistical_scope'] not in scopes:continue
                if sources and row['source_name'] not in sources:continue
                if series_id and row['series_id']!=series_id:continue
                if not row['target']:continue
                try: val=Decimal(row['target'])
                except InvalidOperation:continue
                key=row[group][:7] if group=='timestamp' else row[group]
                if key not in accum:accum[key]=[Decimal(0),0]
                accum[key][0]+=val;accum[key][1]+=1
        result=[{'name':k,'value':float(v[0]),'count':v[1]} for k,v in sorted(accum.items())[:min(300, max(1,limit))]]
        meta=self.version(import_id,version_id)
        warnings=[]
        if not scopes and len(meta.get('statistical_scopes',{}))>1:
            warnings.append('当前查询可能混合产量与销量，请先限定统计口径')
        if not sources and len(meta.get('source_counts',{}))>1:
            warnings.append('当前查询包含多个数据来源，可能存在重复计数，请先限定来源')
        return {'group':group,'rows':result,'total_groups':len(accum),
                'warning':'；'.join(warnings) if warnings else None}
