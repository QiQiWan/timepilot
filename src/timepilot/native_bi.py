"""Versioned Parquet assets and bounded, parameterized DuckDB BI queries.

This module intentionally coexists with the legacy CSV/pandas BIService.
No user-supplied SQL, filesystem path, or raw database identifier is executed.
All code paths requiring DuckDB raise an explicit missing-dependency error.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from timepilot.config import Settings
from timepilot.db import NativeBIAssetHeadRow, NativeBIAssetRow, NativeBISavedViewRow, Store
from timepilot.storage import sha256_file
from timepilot.native_bi_edit import NativeBIEditMixin
from timepilot.physical_store import ParquetBlobStore
from timepilot.native_bi_types import infer_text_schema, valid_type

MAX_PREVIEW_ROWS = 1000
MAX_COLUMNS = 256
MAX_FILTERS = 12
MAX_QUERY_TIMEOUT = 60
MAX_OFFSET = 2_000_000
SAFE_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SUPPORTED_EXTENSIONS = {".csv", ".parquet", ".xlsx"}


class NativeBIError(ValueError):
    pass


class NativeBIDependencyError(NativeBIError):
    pass


def require_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise NativeBIDependencyError(
            "原生 BI 查询需要 DuckDB。请在当前 Python 3.12.12 环境执行 pip install -r requirements.txt。"
        ) from exc
    return duckdb


def quote_identifier(name: str) -> str:
    """Identifiers are validated against the asset schema before reaching here."""
    if not isinstance(name, str) or not name or "\x00" in name:
        raise NativeBIError("字段名称无效")
    return '"' + name.replace('"', '""') + '"'


def safe_json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    return value


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "starts_with", "in", "is_null", "not_null"]
    value: Any = None


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["count", "sum", "avg", "min", "max", "count_distinct"]
    column: str | None = None
    alias: str = "metric"

    @field_validator("alias")
    @classmethod
    def check_alias(cls, value: str) -> str:
        if not SAFE_ALIAS.fullmatch(value):
            raise ValueError("指标别名只能包含英文字母、数字和下划线，且不能以数字开头")
        return value


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    descending: bool = False


class QuerySpec(BaseModel):
    """Allow-listed JSON query language; never exposes arbitrary SQL to a client or LLM."""
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    columns: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=MAX_FILTERS)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    metrics: list[MetricSpec] = Field(default_factory=list, max_length=8)
    order_by: list[OrderSpec] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=100, ge=1, le=MAX_PREVIEW_ROWS)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)
    include_total: bool = False
    include_row_ids: bool = False
    version_id: str | None = None


class ViewSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)


def compile_query(spec: QuerySpec, columns: list[str], source_sql: str = "read_parquet(?)") -> tuple[str, list[Any], str]:
    """Build SELECT + filters. The returned SQL expects a bound Parquet path first.

    Returns (SQL, filter values, count SQL). Row order is reproducible by __tp_rowid.
    """
    allowed = set(columns)
    if not allowed:
        raise NativeBIError("数据资产不包含可以查询的字段")
    if any(col == "__tp_rowid" for col in allowed):
        raise NativeBIError("字段名 __tp_rowid 为系统保留名称")
    def check(column: str):
        if column not in allowed:
            raise NativeBIError(f"字段不存在或不允许访问：{column}")
        return quote_identifier(column)

    grouped = bool(spec.group_by or spec.metrics)
    if grouped and spec.columns:
        raise NativeBIError("聚合查询请使用 group_by 和 metrics；不能同时提供普通列选择")
    if len(set(spec.group_by)) != len(spec.group_by):
        raise NativeBIError("分组字段不能重复")
    where: list[str] = []
    params: list[Any] = []
    for f in spec.filters:
        col = check(f.column)
        if f.op == "is_null":
            where.append(f"({col} IS NULL OR TRIM(CAST({col} AS VARCHAR)) = '')")
        elif f.op == "not_null":
            where.append(f"({col} IS NOT NULL AND TRIM(CAST({col} AS VARCHAR)) <> '')")
        elif f.op == "contains":
            if not isinstance(f.value, str) or len(f.value) > 512:
                raise NativeBIError("包含搜索仅支持最长 512 字符的文本")
            where.append(f"CONTAINS(CAST({col} AS VARCHAR), ?)")
            params.append(f.value)
        elif f.op == "starts_with":
            if not isinstance(f.value, str) or len(f.value) > 512:
                raise NativeBIError("前缀搜索仅支持最长 512 字符的文本")
            where.append(f"STARTS_WITH(CAST({col} AS VARCHAR), ?)")
            params.append(f.value)
        elif f.op == "in":
            if not isinstance(f.value, list) or not 1 <= len(f.value) <= 50:
                raise NativeBIError("in 查询必须提供 1–50 项列表")
            where.append(f"{col} IN (" + ",".join("?" for _ in f.value) + ")")
            params.extend(f.value)
        elif f.op in {"gt", "gte", "lt", "lte"}:
            # Numeric comparisons must have explicit numeric values; never compare numeric text lexicographically.
            if not isinstance(f.value, (int, float)) or isinstance(f.value, bool) or not math.isfinite(f.value):
                raise NativeBIError("大于/小于比较需要数字类型")
            op = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.op]
            where.append(f"TRY_CAST({col} AS DOUBLE) {op} ?")
            params.append(f.value)
        else:
            if isinstance(f.value, (dict, list)) or f.value is None:
                raise NativeBIError("等值筛选需要非空标量")
            where.append(f"{col} {'=' if f.op == 'eq' else '<>'} ?")
            params.append(f.value)
    source = source_sql
    condition = " WHERE " + " AND ".join(where) if where else ""
    aliases = [m.alias for m in spec.metrics]
    if len(set(aliases)) != len(aliases) or any(x in spec.group_by for x in aliases):
        raise NativeBIError("指标别名重复或与分组字段冲突")
    if grouped:
        selections = [check(c) for c in spec.group_by]
        for m in spec.metrics:
            if m.operation == "count" and not m.column:
                expr = "COUNT(*)"
            else:
                if m.column is None:
                    raise NativeBIError("此统计指标必须指定字段")
                col = check(m.column)
                if m.operation in {"sum", "avg"}:
                    expr = f"{m.operation.upper()}(TRY_CAST({col} AS DOUBLE))"
                elif m.operation == "count_distinct":
                    expr = f"COUNT(DISTINCT {col})"
                else:
                    expr = f"{m.operation.upper()}({col})"
            selections.append(f"{expr} AS {quote_identifier(m.alias)}")
        if not selections:
            raise NativeBIError("聚合查询没有分组或统计指标")
        group_clause = " GROUP BY " + ", ".join(check(c) for c in spec.group_by) if spec.group_by else ""
        legal_order = set(spec.group_by) | set(aliases)
        default_order = " ORDER BY " + quote_identifier(spec.group_by[0]) if spec.group_by else ""
    else:
        chosen = spec.columns or columns[:30]
        if len(set(chosen)) != len(chosen):
            raise NativeBIError("不能重复选择字段")
        selections = [check(c) for c in chosen]
        if spec.include_row_ids:
            selections.insert(0, '"__tp_rowid"')
        group_clause = ""
        legal_order = allowed
        default_order = ' ORDER BY "__tp_rowid" ASC'
    if spec.order_by:
        order_parts = []
        for o in spec.order_by:
            if o.field not in legal_order:
                raise NativeBIError(f"排序字段不合法：{o.field}")
            order_parts.append(quote_identifier(o.field) + (" DESC" if o.descending else " ASC") + " NULLS LAST")
        order_clause = " ORDER BY " + ", ".join(order_parts)
    else:
        order_clause = default_order
    base = f"SELECT {', '.join(selections)} FROM {source}{condition}{group_clause}"
    sql = base + order_clause + " LIMIT ? OFFSET ?"
    # Counts only compute on request. Group totals must reflect groups, not source rows.
    count_sql = f"SELECT COUNT(*) FROM ({base}) AS __tp_aggregate_count" if grouped else f"SELECT COUNT(*) FROM {source}{condition}"
    return sql, params, count_sql


class NativeBIService(NativeBIEditMixin):
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings
        self.home = settings.home / "native_bi"
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.blobs = ParquetBlobStore(self.home)

    def capabilities(self) -> dict:
        import importlib.util
        from timepilot.native_bi_bundle import bundle_status
        bundle = bundle_status(Path(__file__).resolve().parent / 'static' / 'native-bi')
        virtual = False
        virtual_error = '缺少 perspective-python'
        if importlib.util.find_spec('perspective') is not None:
            try:
                from perspective.virtual_servers.duckdb import DuckDBVirtualServer
                from perspective.handlers.starlette import PerspectiveStarletteHandler
                virtual = callable(DuckDBVirtualServer) and callable(PerspectiveStarletteHandler)
                virtual_error = None if virtual else 'Perspective WebSocket 或 DuckDB Virtual Server 接口不可调用'
            except (ImportError, AttributeError, OSError, RuntimeError) as exc:
                virtual_error = f'{type(exc).__name__}: {exc}'[:350]
        try:
            db = require_duckdb()
            return {"duckdb": True, "duckdb_version": db.__version__, "storage": "parquet", "mode": "server_query",
                    "max_result_rows": MAX_PREVIEW_ROWS, "perspective_bundle_ready": bundle['ready'],
                    "perspective_bundle": bundle,
                    "perspective_virtual_server_error": virtual_error if not virtual else None,
                    "perspective_virtual_server_ready": bool(virtual and bundle['ready']),
                    "perspective_mode": "virtual_duckdb_and_arrow_fallback" if virtual and bundle['ready'] else "build_or_dependency_required",
                    "revisions": True, "editable_max_cells": 5000}
        except NativeBIDependencyError:
            return {"duckdb": False, "storage": "unavailable", "mode": "legacy_fallback",
                    "perspective_bundle_ready": False, "perspective_mode": "unavailable",
                    "perspective_virtual_server_ready": False,
                    "perspective_virtual_server_error": virtual_error, "perspective_bundle": bundle,
                    "revisions": True, "editable_max_cells": 5000,
                    "install_hint": "pip install -r requirements.txt（Python 3.12.12）"}

    def _asset(self, asset_id: str) -> NativeBIAssetRow:
        with self.store.sessions() as s:
            row = s.get(NativeBIAssetRow, asset_id)
            if row is None:
                raise KeyError("原生 BI 数据资产不存在")
            return row

    def _path(self, asset_id: str) -> Path:
        # Only identifiers loaded from the metadata database are used as paths.
        from uuid import UUID
        return self.home / str(UUID(asset_id)) / "data.parquet"

    @staticmethod
    def _metadata(row: NativeBIAssetRow) -> dict:
        return {"asset_id": row.id, "name": row.name, "row_count": row.row_count,
                "columns": json.loads(row.columns_json), "lineage": json.loads(row.lineage_json),
                "sha256": row.sha256, "file_bytes": row.file_bytes,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "format": "parquet"}

    def list_assets(self, limit: int = 100) -> list[dict]:
        with self.store.sessions() as s:
            return [self._metadata(x) for x in s.scalars(select(NativeBIAssetRow).order_by(NativeBIAssetRow.created_at.desc()).limit(min(200, max(1, limit))))]

    def asset(self, asset_id: str) -> dict:
        row = self._asset(asset_id)
        path = self._path(row.id)
        if not path.exists() or path.stat().st_size != row.file_bytes:
            raise NativeBIError("原生 BI 快照文件缺失或文件长度异常，请检查存储")
        return {**self._metadata(row), "head_version": self._head(row.id)}

    def verify(self, asset_id: str) -> dict:
        row = self._asset(asset_id)
        path = self._path(row.id)
        if not path.is_file():
            return {"asset_id": asset_id, "valid": False, "actual_sha256": None,
                    "expected_sha256": row.sha256, "reason": "snapshot_missing"}
        actual = sha256_file(path)
        return {"asset_id": asset_id, "valid": actual == row.sha256, "actual_sha256": actual,
                "expected_sha256": row.sha256, "reason": None if actual == row.sha256 else "checksum_mismatch"}

    def _connection(self):
        db = require_duckdb()
        con = db.connect(":memory:")
        memory_mb = min(4096, max(128, int(os.getenv("TIMEPILOT_BI_MEMORY_MB", "512"))))
        threads = min(8, max(1, int(os.getenv("TIMEPILOT_BI_THREADS", "2"))))
        tmp = self.home / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET memory_limit='{memory_mb}MB'")
        con.execute(f"SET threads={threads}")
        safe_tmp = str(tmp).replace("'", "''")
        con.execute(f"SET temp_directory='{safe_tmp}'")
        con.execute("SET preserve_insertion_order=false")
        return con

    def import_file(self, content: bytes, filename: str, name: str) -> dict:
        if len(content) > self.settings.max_upload_bytes:
            raise NativeBIError("文件超过当前上传限制；大型数据请使用管理员本地文件导入命令")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise NativeBIError("原生 BI 仅允许 CSV、Parquet 和 XLSX 文件")
        with tempfile.NamedTemporaryFile(suffix=suffix, dir=self.home, delete=False) as out:
            out.write(content)
            temp_path = Path(out.name)
        try:
            return self.import_from_path(temp_path, name, {"type": "upload", "filename": Path(filename).name})
        finally:
            temp_path.unlink(missing_ok=True)

    def _persist_asset_metadata(self, record: NativeBIAssetRow) -> None:
        """Atomically persist a Parquet asset and its mutable head pointer.

        SQLAlchemy cannot infer unit-of-work ordering from FK columns alone when
        no ORM relationship connects these objects. Explicitly flush the parent
        first; SQLite with PRAGMA foreign_keys=ON otherwise rejects a head row
        whose asset has not yet been inserted.
        """
        with self.store.sessions.begin() as session:
            session.add(record)
            session.flush()  # Parent native_bi_assets row must exist first.
            session.add(NativeBIAssetHeadRow(asset_id=record.id, version_id=None))
            session.flush()  # Fail within the same transaction if FK is broken.

    def import_from_path(self, source: Path, name: str, lineage: dict | None = None) -> dict:
        """Trusted CLI/internal path. NEVER map an untrusted HTTP path to this method."""
        require_duckdb()
        source = Path(source).resolve(strict=True)
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise NativeBIError("不支持的文件格式")
        name = name.strip()
        if not 1 <= len(name) <= 120 or any(ord(c) < 32 for c in name):
            raise NativeBIError("数据集名称不合法")
        asset_id = str(uuid4())
        target_dir = self.home / asset_id
        target_dir.mkdir(parents=True, exist_ok=False)
        temp_target = target_dir / "data.building"
        target = target_dir / "data.parquet"
        try:
            with self._connection() as con:
                source_path = str(source).replace("'", "''")
                if source.suffix.lower() == ".csv":
                    # All strings by default: protects product IDs like 00123 and timestamps from unsafe coercion.
                    read_expr = f"read_csv('{source_path}', header=true, all_varchar=true)"
                elif source.suffix.lower() == ".parquet":
                    read_expr = f"read_parquet('{source_path}')"
                else:
                    import pandas as pd
                    frame = pd.read_excel(source, dtype="string")
                    if len(frame) > 250_000:
                        raise NativeBIError("大型 XLSX 请先转换为 CSV/Parquet，再通过流式方式导入")
                    if any(str(c) == "__tp_rowid" for c in frame.columns):
                        raise NativeBIError("文件包含保留字段 __tp_rowid")
                    con.register("__tp_excel_import", frame)
                    read_expr = "__tp_excel_import"
                # Keep the original file untouched. Infer conservatively from a bounded
                # sample, then validate every candidate cast against the FULL dataset.
                desc = con.execute(f"DESCRIBE SELECT * FROM {read_expr}").fetchall()
                fields = [str(x[0]) for x in desc]
                if not fields or len(fields) > MAX_COLUMNS or "__tp_rowid" in fields:
                    raise NativeBIError("字段为空、超过 256 列或包含系统保留字段 __tp_rowid")
                if len(set(fields)) != len(fields):
                    raise NativeBIError("数据存在重复字段名")
                sample = con.execute(f"SELECT * FROM {read_expr} LIMIT 3000").fetchall() if source.suffix.lower() in {'.csv', '.xlsx'} else []
                suggestions = infer_text_schema(fields, sample) if sample else {}
                type_overrides = {}
                if suggestions:
                    # Validate every typed candidate against ALL rows, but scan the
                    # source once. Per-column full scans would scale as N × fields.
                    candidates = [(field, info['physical_type']) for field, info in suggestions.items()
                                  if info['physical_type'] != 'VARCHAR']
                    if candidates:
                        tests = []
                        for i, (field, kind) in enumerate(candidates):
                            q = quote_identifier(field)
                            tests.append(f"COALESCE(SUM(CASE WHEN {q} IS NOT NULL "
                                         f"AND TRIM(CAST({q} AS VARCHAR)) != '' "
                                         f"AND TRY_CAST({q} AS {kind}) IS NULL "
                                         f"THEN 1 ELSE 0 END), 0) AS __bad_{i}")
                            # A code such as 00123 may first occur after the
                            # bounded schema sample. Casting it to BIGINT or
                            # DOUBLE would silently erase source information.
                            # The extra aggregate is evaluated in the SAME
                            # full scan as the cast validation above.
                            tests.append(f"COALESCE(SUM(CASE WHEN {q} IS NOT NULL "
                                         f"AND regexp_matches(TRIM(CAST({q} AS VARCHAR)), "
                                         f"'^[+-]?0[0-9]+') THEN 1 ELSE 0 END), 0) AS __zero_{i}")
                        checks = con.execute('SELECT ' + ', '.join(tests) + ' FROM ' + read_expr).fetchone()
                        for i, (field, kind) in enumerate(candidates):
                            bad, padded = checks[2*i:2*i+2]
                            if bad or padded:
                                cause = (f'发现 {padded} 个前导零文本，禁止数值转换'
                                         if padded else f'全量校验发现 {bad} 个不兼容值')
                                suggestions[field] = {'physical_type':'VARCHAR', 'semantic_type':'category',
                                                      'reason':cause+'；保留原文'}
                            else:
                                type_overrides[field] = kind
                select_fields = ', '.join(
                    f"TRY_CAST({quote_identifier(field)} AS {type_overrides[field]}) AS {quote_identifier(field)}"
                    if field in type_overrides else quote_identifier(field) for field in fields
                )
                path_out = str(temp_target).replace("'", "''")
                con.execute(f"COPY (SELECT ROW_NUMBER() OVER () - 1 AS __tp_rowid, "
                            f"{select_fields} FROM {read_expr}) "
                            f"TO '{path_out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
                count = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(temp_target)]).fetchone()[0]
                result = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(temp_target)]).fetchall()
                columns = [{"name": x[0], "physical_type": x[1],
                            "semantic_type": suggestions.get(x[0], {}).get('semantic_type', 'unknown'),
                            "inference_reason": suggestions.get(x[0], {}).get('reason', '源数据类型已保留')}
                           for x in result if x[0] != "__tp_rowid"]
            os.replace(temp_target, target)
            record = NativeBIAssetRow(id=asset_id, name=name, row_count=int(count),
                                      columns_json=json.dumps(columns, ensure_ascii=False),
                                      lineage_json=json.dumps(lineage or {"type": "file_import"}, ensure_ascii=False),
                                      sha256=sha256_file(target), file_bytes=target.stat().st_size)
            self._persist_asset_metadata(record)
            # Optimization after committing authoritative metadata. A CAS or
            # filesystem error never invalidates a fully published asset.
            try:
                self.blobs.adopt(target,expected=record.sha256,apply=True)
            except (OSError,RuntimeError) as exc:
                import logging
                logging.getLogger(__name__).warning('Parquet CAS dedup skipped for %s: %s',asset_id,exc)
            return self._metadata(record)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    def derive_types(self, asset_id: str, *, expected_version: str, changes: dict[str, str],
                     name: str | None = None) -> dict:
        """Create a separately versioned typed asset; never mutate source Parquet.

        Full-data validation forbids silent TRY_CAST -> NULL information loss.
        """
        meta = self.asset(asset_id)
        if not changes or len(changes) > 20:
            raise NativeBIError('一次请选择 1–20 个需要转换的字段')
        if self._head(asset_id) != expected_version:
            raise NativeBIError('源数据版本已更新，请刷新并重新确认转换')
        self._version(asset_id, expected_version)
        known = {c['name'] for c in meta['columns']}
        if not set(changes).issubset(known):
            raise NativeBIError('类型转换包含不存在的字段')
        try:
            conversions = {key:valid_type(value) for key, value in changes.items()}
        except ValueError as exc:
            raise NativeBIError(str(exc)) from exc
        cols = ', '.join(
            f'TRY_CAST({quote_identifier(item["name"])} AS {conversions[item["name"]]}) AS {quote_identifier(item["name"])}'
            if item['name'] in conversions else quote_identifier(item['name'])
            for item in meta['columns']
        )
        path = self.home / 'tmp' / (str(uuid4()) + '.parquet')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as con:
                self._install_effective_view(con, asset_id, expected_version, meta['columns'])
                for field, kind in conversions.items():
                    q = quote_identifier(field)
                    bad = con.execute(f'SELECT COUNT(*) FROM __tp_effective WHERE {q} IS NOT NULL '
                                      f'AND TRY_CAST({q} AS {kind}) IS NULL').fetchone()[0]
                    if bad:
                        raise NativeBIError(f'字段 {field} 有 {bad} 个无法转换的值；源文件保持不变')
                out = str(path).replace("'", "''")
                con.execute(f"COPY (SELECT {cols} FROM __tp_effective ORDER BY __tp_rowid) "
                            f"TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            return self.import_from_path(path, name or meta['name']+'（类型转换）',
                                         {'type':'semantic_cast', 'source_asset_id':asset_id,
                                          'source_version':expected_version, 'casts':conversions})
        finally:
            path.unlink(missing_ok=True)

    def migrate_legacy(self, bi_service, table_id: str) -> dict:
        # A process-local lock avoids creating duplicate Parquet snapshots when
        # multiple user clicks migrate the same legacy asset concurrently.
        with self._lock:
            legacy = bi_service.table(table_id)
            legacy_file = bi_service._path(table_id)
            if not legacy_file.is_file() or sha256_file(legacy_file) != legacy["sha256"]:
                raise NativeBIError("旧 BI 原始文件校验失败，禁止迁移")
            # Scan all recorded assets, not only the newest 200. Enterprise
            # multi-process idempotence needs a DB unique source revision key.
            with self.store.sessions() as session:
                for row in session.scalars(select(NativeBIAssetRow)):
                    lineage = json.loads(row.lineage_json)
                    if lineage.get("legacy_table_id") == table_id and lineage.get("legacy_sha256") == legacy["sha256"]:
                        return self._metadata(row)
            return self.import_from_path(legacy_file, legacy["name"],
                                         {"type": "legacy_migration", "legacy_table_id": table_id,
                                          "legacy_sha256": legacy["sha256"]})

    def query(self, spec: QuerySpec) -> dict:
        meta = self.asset(spec.asset_id)
        names = [c["name"] for c in meta["columns"]]
        version_id = spec.version_id or self._head(spec.asset_id)
        self._version(spec.asset_id, version_id)
        sql, params, count_sql = compile_query(spec, names, source_sql="__tp_effective")
        t0 = time.perf_counter()
        with self._connection() as con:
            max_seconds = min(MAX_QUERY_TIMEOUT, max(1, int(os.getenv("TIMEPILOT_BI_QUERY_TIMEOUT", "15"))))
            timer = threading.Timer(max_seconds, con.interrupt)
            try:
                timer.start()
                self._install_effective_view(con, spec.asset_id, version_id, meta['columns'])
                rows_cur = con.execute(sql, [*params, spec.limit, spec.offset])
                columns = [x[0] for x in rows_cur.description]
                rows = [dict(zip(columns, safe_json(r))) for r in rows_cur.fetchall()]
                total = None
                if spec.include_total:
                    total = con.execute(count_sql, params).fetchone()[0]
            except Exception as exc:
                if time.perf_counter() - t0 >= max_seconds:
                    raise NativeBIError(f"BI 查询已超过 {max_seconds} 秒的时间限制") from exc
                if isinstance(exc, NativeBIError):
                    raise
                raise NativeBIError(f"BI 查询失败：{str(exc)[:300]}") from exc
            finally:
                timer.cancel()
        return {"asset_id": spec.asset_id, "version_id": version_id,
                "columns": columns, "rows": rows, "returned": len(rows),
                "total": int(total) if total is not None else None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                "limit": spec.limit, "offset": spec.offset, "execution": "duckdb_versioned_parquet_pushdown",
                "result_truncated": (len(rows) == spec.limit)}

    def profile(self, asset_id: str, sample_rows: int = 5000, version_id: str | None = None) -> dict:
        meta = self.asset(asset_id)
        sample_rows = min(10000, max(100, int(sample_rows)))
        fields = meta["columns"]
        # Sample only a bounded number of rows; the result is explicitly labelled as sample-based.
        sql = f"SELECT * EXCLUDE (__tp_rowid) FROM __tp_effective LIMIT {sample_rows}"
        import pandas as pd
        with self._connection() as con:
            self._install_effective_view(con, asset_id, version_id or self._head(asset_id), meta["columns"])
            frame = con.execute(sql).df()
        result = []
        for field in fields:
            name = field["name"]
            values = frame[name]
            valid = values.dropna()
            sample = valid.head(3).astype(str).tolist()
            numeric = pd.to_numeric(valid.astype(str), errors="coerce")
            numeric_ratio = float(numeric.notna().mean()) if len(valid) else 0
            result.append({"name": name, "physical_type": field["physical_type"],
                           "null_ratio_sample": round(float(values.isna().mean()), 5),
                           "distinct_sample": int(valid.nunique()), "numeric_ratio_sample": round(numeric_ratio, 4),
                           "examples": sample})
        return {"asset_id": asset_id, "version_id": version_id or self._head(asset_id), "rows_sampled": int(len(frame)), "row_count": meta["row_count"],
                "is_sample_estimate": len(frame) < meta["row_count"], "fields": result}

    def to_timeseries(self, asset_id: str, *, name: str, frequency: str, timestamp_col: str,
                      target_col: str, entity_col: str, available_at_col: str,
                      source_timezone: str, unit: str) -> dict:
        """Bridge to the existing validated TimePilot DatasetManifest.

        Current training ingestion has a 200k-row in-memory contract. This bridge
        deliberately refuses larger assets rather than silently truncating data.
        """
        from timepilot.data import create_dataset
        meta = self.asset(asset_id)
        columns = {x['name'] for x in meta['columns']}
        chosen = [entity_col, timestamp_col, target_col, available_at_col]
        if len(set(chosen)) != len(chosen):
            raise NativeBIError('实体、时间、目标和可用时间字段必须分别选择，不能重复')
        if not all(col in columns for col in chosen):
            raise NativeBIError('转预测任务需要实体、时间、目标和可用时间字段')
        if meta['row_count'] > 200000:
            raise NativeBIError('现有预测训练数据协议最多支持 200,000 行；请先生成已确认的较小时间序列数据集')
        safe_columns = ', '.join(quote_identifier(col) for col in chosen)
        with self._connection() as con:
            self._install_effective_view(con, asset_id, self._head(asset_id), meta['columns'])
            df = con.execute(f'SELECT {safe_columns} FROM __tp_effective ORDER BY "__tp_rowid"').df()
        content = df.to_csv(index=False).encode('utf-8')
        if len(content) > self.settings.max_upload_bytes:
            raise NativeBIError('预测数据超出当前数据集导入字节上限，请先聚合或分批处理')
        manifest = create_dataset(self.store, self.settings, content, f'{asset_id}.csv',
                                  name=name, frequency=frequency, timestamp_col=timestamp_col,
                                  target_col=target_col, entity_col=entity_col,
                                  available_at_col=available_at_col, unit=unit,
                                  source_timezone=source_timezone)
        return manifest.model_dump(mode='json')

    def preview_arrow(self, asset_id: str, limit: int = 5000, version_id: str | None = None) -> bytes:
        """Limited Arrow IPC payload for the Perspective client-side sample mode.

        Large assets must use the service-side query interface (or a separately secured virtual server).
        """
        meta = self.asset(asset_id)
        limit = min(10000, max(1, int(limit)))
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise NativeBIDependencyError("Arrow 预览需要 pyarrow") from exc
        with self._connection() as con:
            self._install_effective_view(con, asset_id, version_id or self._head(asset_id), meta["columns"])
            table = con.execute(f"SELECT * EXCLUDE (__tp_rowid) FROM __tp_effective LIMIT {limit}").to_arrow_table()
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue().to_pybytes()

    def query_arrow(self, spec: QuerySpec) -> bytes:
        """DuckDB filters/aggregates full source, returns bounded Arrow for Perspective.

        Every query is validated by compile_query and limited to MAX_PREVIEW_ROWS.
        A chart of 200 groups may therefore aggregate millions of source rows.
        """
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise NativeBIDependencyError("Arrow 查询需要 pyarrow") from exc
        meta = self.asset(spec.asset_id)
        names = [c['name'] for c in meta['columns']]
        sql, params, _ = compile_query(spec, names, source_sql="__tp_effective")
        with self._connection() as con:
            max_seconds = min(MAX_QUERY_TIMEOUT, max(1, int(os.getenv('TIMEPILOT_BI_QUERY_TIMEOUT', '15'))))
            timer = threading.Timer(max_seconds, con.interrupt)
            try:
                timer.start()
                self._install_effective_view(con, spec.asset_id, spec.version_id or self._head(spec.asset_id), meta['columns'])
                table = con.execute(sql, [*params, spec.limit, spec.offset]).to_arrow_table()
            finally:
                timer.cancel()
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue().to_pybytes()

    def save_view(self, spec: ViewSpec) -> dict:
        meta = self.asset(spec.asset_id)
        # New Perspective workspaces have a fixed, immutable source revision.
        # Older generic BI view payloads are kept readable for compatibility.
        if spec.config.get('format') == 'timepilot-perspective-v2':
            source = spec.config.get('source')
            workspace = spec.config.get('workspace')
            if not isinstance(source, dict) or not isinstance(workspace, dict) or not workspace.get('panels'):
                raise NativeBIError('Perspective 视图缺少有效的数据来源或面板配置')
            if source.get('kind') not in {'sample', 'query', 'virtual'}:
                raise NativeBIError('Perspective 视图数据来源不合法')
            version = source.get('version_id')
            if not isinstance(version, str) or not version:
                raise NativeBIError('Perspective 视图必须绑定明确的数据版本')
            self._version(spec.asset_id, version)
            if source['kind'] == 'virtual' and source.get('asset_id') != spec.asset_id:
                raise NativeBIError('原生分析工作区的数据资产不一致')
            if source['kind'] == 'sample' and not 1 <= source.get('limit', 0) <= 10000:
                raise NativeBIError('有限样本的行数范围不正确')
            root_name = spec.config.get('base_table_name', 'timepilot_active')
            if not isinstance(root_name, str) or not root_name or len(root_name) > 128:
                raise NativeBIError('Perspective 基础表名称无效')
            derived = workspace.get('derived_tables') or {}
            panels = workspace.get('panels')
            if (not isinstance(derived, dict) or not isinstance(panels, dict) or not panels
                    or len(derived) > 32 or len(panels) > 24 or root_name in derived):
                raise NativeBIError('Perspective 面板或派生表配置无效')
            allowed = set(derived) | {root_name}
            for record in list(panels.values()) + list(derived.values()):
                if not isinstance(record, dict) or record.get('table') not in allowed:
                    raise NativeBIError('Perspective 工作区引用了未授权的表或无效派生关系')
            # Prevent recursive derived-table graphs from becoming a broken saved view.
            visited, visiting = set(), set()
            def check_parent(name):
                if name == root_name or name in visited:
                    return
                if name in visiting:
                    raise NativeBIError('Perspective 派生表存在循环引用')
                visiting.add(name)
                check_parent(derived[name]['table'])
                visiting.remove(name)
                visited.add(name)
            for name in derived:
                check_parent(name)
            if source['kind'] == 'query':
                try:
                    q = QuerySpec.model_validate(source.get('query'))
                except Exception as exc:
                    raise NativeBIError('保存的视图 QuerySpec 无效') from exc
                if q.asset_id != spec.asset_id or q.version_id != version:
                    raise NativeBIError('视图查询的数据资产或版本不一致')
                compile_query(q, [c['name'] for c in meta['columns']], source_sql='__tp_effective')
        raw = json.dumps(spec.config, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 65536:
            raise NativeBIError("视图配置超出 64KB 上限")
        view_id = str(uuid4())
        row = NativeBISavedViewRow(id=view_id, asset_id=spec.asset_id, name=spec.name, config_json=raw)
        with self.store.sessions.begin() as s:
            s.add(row)
        return self.get_view(view_id)

    def get_view(self, view_id: str) -> dict:
        with self.store.sessions() as s:
            row = s.get(NativeBISavedViewRow, view_id)
            if row is None:
                raise KeyError("保存的 BI 视图不存在")
            return {"view_id": row.id, "asset_id": row.asset_id, "name": row.name,
                    "config": json.loads(row.config_json), "created_at": row.created_at.isoformat() if row.created_at else None}

    def list_views(self, asset_id: str) -> list[dict]:
        self.asset(asset_id)
        with self.store.sessions() as s:
            rows = list(s.scalars(select(NativeBISavedViewRow).where(NativeBISavedViewRow.asset_id == asset_id)
                                  .order_by(NativeBISavedViewRow.created_at.desc()).limit(100)))
            return [{"view_id": x.id, "name": x.name, "asset_id": x.asset_id} for x in rows]
