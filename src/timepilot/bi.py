"""Local BI workspace: immutable tables, schema inspection, recipes, joins and reports.

Operations are structured and whitelisted. Arbitrary SQL/Python execution is not exposed.
Every transformation materializes a new snapshot with recorded lineage.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from timepilot.config import Settings
from timepilot.db import BIReportRow, BITableRow, BIWorkflowPreviewRow, Store
from timepilot.storage import atomic_write_bytes, sha256_file

MAX_ROWS = 250_000
MAX_COLS = 250
MAX_REPORT_ROWS = 500
MAX_STEPS = 20
MAX_BATCH_EDITS = 200
MAX_PIVOT_COLUMNS = 30
MAX_XLSX_EXPORT_ROWS = 50_000
MAX_WORKFLOW_ACTIONS = 6
NAME_PATTERN = re.compile(r"^[^\x00-\x1f]{1,120}$")


class BIError(ValueError):
    pass


class MetricSpec(BaseModel):
    column: str | None = None
    operation: str = "count"  # count, sum, mean, min, max, nunique


class AggregateSpec(BaseModel):
    group_by: list[str] = Field(default_factory=list, max_length=3)
    metric: MetricSpec = Field(default_factory=MetricSpec)
    order: str = "desc"
    sort_by: str = "metric"
    limit: int = Field(default=100, ge=1, le=MAX_REPORT_ROWS)


class PivotSpec(BaseModel):
    group_by: list[str] = Field(default_factory=list, min_length=1, max_length=2)
    pivot_column: str
    metric: MetricSpec = Field(default_factory=MetricSpec)
    limit: int = Field(default=100, ge=1, le=MAX_REPORT_ROWS)


class WorkflowStep(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)


class WorkflowSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=MAX_WORKFLOW_ACTIONS)


class ReportSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    table_id: str
    chart_type: str = "bar"
    aggregate: AggregateSpec = Field(default_factory=AggregateSpec)
    pivot: PivotSpec | None = None


class BIService:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        (settings.home / "bi").mkdir(parents=True, exist_ok=True)

    def _row(self, table_id: str) -> BITableRow:
        with self.store.sessions() as session:
            row = session.get(BITableRow, table_id)
            if row is None:
                raise KeyError(f"BI 表不存在：{table_id}")
            return row

    @staticmethod
    def _normal_name(name: str) -> str:
        name = name.strip()
        if not NAME_PATTERN.fullmatch(name):
            raise BIError("名称不能为空、超过 120 字或包含控制字符")
        return name

    @staticmethod
    def _check_frame(df: pd.DataFrame) -> None:
        if not len(df.columns) or len(df.columns) > MAX_COLS:
            raise BIError(f"字段数量必须在 1～{MAX_COLS} 之间")
        if len(df) > MAX_ROWS:
            raise BIError(f"一次 BI 处理最多 {MAX_ROWS:,} 行，请拆分数据或通过企业仓库适配器连接")
        if any(not str(c).strip() or str(c).startswith("Unnamed:") for c in df.columns):
            raise BIError("存在空列名或未命名列，请先修正字段表头")
        if len(set(map(str, df.columns))) != len(df.columns):
            raise BIError("发现重复字段名，请重命名后导入")

    def _meta(self, row: BITableRow) -> dict:
        return {"table_id": row.id, "name": row.name, "row_count": row.row_count,
                "columns": json.loads(row.columns_json), "lineage": json.loads(row.lineage_json),
                "sha256": row.sha256, "created_at": row.created_at.isoformat() if row.created_at else None}

    def list_tables(self) -> list[dict]:
        with self.store.sessions() as session:
            rows = list(session.scalars(select(BITableRow).order_by(BITableRow.created_at.desc())))
            return [self._meta(row) for row in rows]

    def table(self, table_id: str) -> dict:
        return self._meta(self._row(table_id))

    def _path(self, table_id: str) -> Path:
        return self.settings.home / "bi" / table_id / "data.csv"

    def _frame(self, table_id: str) -> pd.DataFrame:
        row = self._row(table_id)
        lineage = json.loads(row.lineage_json)
        if lineage.get('type') == 'data_asset_reference':
            return self._linked_asset_frame(row, lineage)
        path = self._path(table_id)
        if not path.is_file() or sha256_file(path) != row.sha256:
            raise BIError("BI 数据快照校验失败，源文件缺失或已经被修改")
        # Use string columns to preserve identifiers and leading zeros during joins.
        return pd.read_csv(path, dtype="string", keep_default_na=True)

    def _linked_asset_frame(self, row: BITableRow, lineage: dict) -> pd.DataFrame:
        """Resolve a pinned DataAsset with whitelisted filters; no copied CSV."""
        from timepilot.data_assets import import_id_for
        from timepilot.smart_import import SmartImportService, CSV_COLUMNS, SmartImportError
        from timepilot.smart_workbench import ViewSelection
        try:
            asset_id=lineage['asset_id']
            version_id=lineage['version_id']
            selection=ViewSelection.model_validate(lineage['selection'])
            imports=SmartImportService(self.settings.home)
            import_id=import_id_for(asset_id)
            source=imports.version(import_id,version_id)
            if source['sha256']!=row.sha256 or lineage.get('source_sha256')!=row.sha256:
                raise BIError('关联 BI 数据版本 SHA256 不匹配，已拒绝读取')
            path=imports.version_file(import_id,version_id)
        except (SmartImportError,KeyError,ValueError) as exc:
            raise BIError(f'关联 BI 数据资产无法验证：{exc}') from exc
        # Never accept arbitrary paths or SQL from stored lineage.
        filters={key:set(values) for key,values in selection.canonical()['filters'].items()}
        blocks=[]
        count=0
        try:
            for chunk in pd.read_csv(path,dtype='string',keep_default_na=True,
                                     chunksize=4096,encoding='utf-8'):
                if list(chunk.columns)!=list(CSV_COLUMNS):
                    raise BIError('关联 BI 数据资产字段结构不匹配，已拒绝读取')
                mask=pd.Series(True,index=chunk.index)
                for field,values in filters.items():
                    mask=mask & chunk[field].fillna('').isin(values)
                if selection.date_from:
                    mask=mask & chunk['timestamp'].fillna('').str[:7].ge(selection.date_from)
                if selection.date_to:
                    mask=mask & chunk['timestamp'].fillna('').str[:7].le(selection.date_to)
                selected=chunk.loc[mask]
                if selected.empty:
                    continue
                count+=len(selected)
                if count>MAX_ROWS:
                    raise BIError('关联 BI 数据超过单次内存处理上限，请先缩小筛选条件')
                blocks.append(selected)
        except (OSError,UnicodeError) as exc:
            raise BIError(f'读取关联 BI 数据失败：{exc}') from exc
        if count != row.row_count:
            raise BIError('关联 BI 数据筛选结果数量变化，已停止使用该版本')
        df=pd.concat(blocks,ignore_index=True) if blocks else pd.DataFrame(columns=CSV_COLUMNS)
        self._check_frame(df)
        # Propagate the immutable origin to structured BI operations; numerical
        # aggregation must never silently add incompatible scope/source totals.
        df.attrs['timepilot_linked_source']=True
        return df

    @staticmethod
    def _check_linked_numeric_scope(df: pd.DataFrame, operation: str) -> None:
        if not df.attrs.get('timepilot_linked_source') or operation not in {'sum','mean','min','max'}:
            return
        if 'statistical_scope' not in df or 'source_name' not in df:
            raise BIError('关联数据源缺少统计口径或来源字段，拒绝数值聚合')
        if df['statistical_scope'].nunique(dropna=False)>1 or df['source_name'].nunique(dropna=False)>1:
            raise BIError('存在多个统计口径或数据来源，请在统一资产工作台先筛选后再进行数值聚合')

    def _save(self, name: str, df: pd.DataFrame, lineage: dict) -> dict:
        self._check_frame(df)
        table_id = str(uuid4())
        name = self._normal_name(name)
        df.columns = [str(c) for c in df.columns]
        import hashlib
        payload = df.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
        self._path(table_id).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self._path(table_id), payload)
        record = BITableRow(id=table_id, name=name, row_count=len(df),
                            columns_json=json.dumps(self._describe_columns(df), ensure_ascii=False),
                            lineage_json=json.dumps(lineage, ensure_ascii=False),
                            sha256=hashlib.sha256(payload).hexdigest())
        with self.store.sessions.begin() as session:
            session.add(record)
        return self._meta(record)

    @staticmethod
    def _describe_columns(df: pd.DataFrame) -> list[dict]:
        descriptions = []
        for key in df.columns:
            series = df[key]
            valid = series.dropna()
            nonempty = valid.astype("string").str.strip()
            valid = valid[nonempty.ne("").fillna(False)]
            numeric = pd.to_numeric(valid, errors="coerce")
            is_number = len(valid) > 0 and numeric.notna().mean() > .98
            # Avoid silently converting zero-padded business IDs into numeric values.
            zero_id = valid.astype(str).str.match(r"^0\d+$").any()
            is_number = is_number and not zero_id
            sample = valid.astype(str).head(3).tolist()
            descriptions.append({"name": str(key), "type": "numeric" if is_number else "text",
                                 "null_count": int(series.isna().sum()), "sample": sample})
        return descriptions

    def import_file(self, content: bytes, filename: str, name: str) -> dict:
        if len(content) > self.settings.max_upload_bytes:
            raise BIError("文件超过系统上传上限")
        suffix = Path(filename).suffix.lower()
        stream = io.BytesIO(content)
        try:
            if suffix == ".csv":
                try:
                    df = pd.read_csv(stream, dtype="string")
                except UnicodeDecodeError:
                    stream.seek(0)
                    df = pd.read_csv(stream, dtype="string", encoding="gb18030")
            elif suffix == ".xlsx":
                df = pd.read_excel(stream, dtype="string")
            elif suffix == ".parquet":
                df = pd.read_parquet(stream).astype("string")
            else:
                raise BIError("仅支持 CSV、XLSX 和 Parquet")
        except (ValueError, ImportError, pd.errors.ParserError) as exc:
            raise BIError(f"无法解析文件：{exc}") from exc
        return self._save(name, df, {"type": "upload", "filename": Path(filename).name})

    def sql_sources(self) -> dict:
        """Expose only configured, explicitly allowed database tables; never URLs or passwords."""
        import os
        url = os.getenv("TIMEPILOT_BI_SOURCE_URL", "").strip()
        allowed = [x.strip() for x in os.getenv("TIMEPILOT_BI_ALLOWED_TABLES", "").split(",") if x.strip()]
        if not url:
            return {"configured": False, "tables": [], "hint": "管理员可在环境中配置只读数据库连接和允许访问的表名"}
        if not allowed:
            return {"configured": True, "tables": [], "hint": "请配置 TIMEPILOT_BI_ALLOWED_TABLES 白名单"}
        from sqlalchemy import create_engine, inspect
        engine = create_engine(url, pool_pre_ping=True)
        try:
            found = set(inspect(engine).get_table_names())
            return {"configured": True, "tables": sorted(found.intersection(allowed)),
                    "hint": "仅允许导入白名单中的数据库表；导入后生成不可变快照"}
        finally:
            engine.dispose()

    def import_sql(self, table_name: str, name: str) -> dict:
        import os
        from sqlalchemy import MetaData, Table, create_engine, inspect, select
        url = os.getenv("TIMEPILOT_BI_SOURCE_URL", "").strip()
        allowed = {x.strip() for x in os.getenv("TIMEPILOT_BI_ALLOWED_TABLES", "").split(",") if x.strip()}
        if not url or table_name not in allowed:
            raise BIError("数据库未配置或指定表不在允许访问的白名单中")
        engine = create_engine(url, pool_pre_ping=True)
        try:
            if table_name not in set(inspect(engine).get_table_names()):
                raise BIError("数据库中不存在指定表")
            table = Table(table_name, MetaData(), autoload_with=engine)
            with engine.connect() as con:
                data = pd.read_sql(select(table).limit(MAX_ROWS + 1), con)
            if len(data) > MAX_ROWS:
                raise BIError("数据库表超过单次快照行数上限，请先在源数据库准备受控视图")
            return self._save(name, data.astype("string"),
                              {"type": "sql_snapshot", "source": "configured_readonly", "table_name": table_name})
        finally:
            engine.dispose()

    def import_timeseries(self, dataset_id: str, name: str) -> dict:
        from timepilot.data import get_dataset
        manifest, df = get_dataset(self.store, self.settings, dataset_id)
        for c in ("timestamp", "available_at"):
            df[c] = df[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._save(name, df, {"type": "timeseries", "dataset_id": dataset_id,
                                     "source_sha256": manifest.snapshot_sha256})

    def preview(self, table_id: str, page: int = 1, page_size: int = 30,
                search: str = "", sort_by: str = "", sort_dir: str = "asc",
                filter_column: str = "", filter_operator: str = "contains",
                filter_value: str = "") -> dict:
        """Return a bounded, paginated spreadsheet view with original row indexes.

        Query strings never become Python expressions or SQL. All columns must exist.
        """
        if not 1 <= page <= 10000 or not 1 <= page_size <= 100:
            raise BIError("分页参数超出范围")
        if any(len(v) > 160 for v in (search, filter_value)):
            raise BIError("搜索或筛选文本超过长度上限")
        df = self._frame(table_id)
        if search:
            matched = df.astype("string").apply(
                lambda col: col.str.contains(search, case=False, na=False, regex=False)
            ).any(axis=1)
            df = df.loc[matched]
        if filter_column:
            self._columns(df, [filter_column])
            if filter_operator not in {"contains", "eq", "ne", "is_null", "not_null", "gt", "gte", "lt", "lte"}:
                raise BIError("不支持的筛选条件")
            field = df[filter_column]
            if filter_operator == "is_null":
                mask = field.isna() | field.astype("string").str.strip().eq("").fillna(False)
            elif filter_operator == "not_null":
                mask = field.notna() & field.astype("string").str.strip().ne("").fillna(False)
            elif filter_operator == "contains":
                mask = field.astype("string").str.contains(filter_value, case=False, regex=False, na=False)
            elif filter_operator in {"gt", "gte", "lt", "lte"}:
                try:
                    value = float(filter_value)
                except ValueError as exc:
                    raise BIError("数值比较需要填写数字") from exc
                numeric = pd.to_numeric(field, errors="coerce")
                ops = {"gt": numeric.gt, "gte": numeric.ge, "lt": numeric.lt, "lte": numeric.le}
                mask = ops[filter_operator](value)
            else:
                mask = field.eq(filter_value) if filter_operator == "eq" else field.ne(filter_value)
            df = df.loc[mask.fillna(False)]
        if sort_by:
            self._columns(df, [sort_by])
            if sort_dir not in {"asc", "desc"}:
                raise BIError("排序方向错误")
            numeric = pd.to_numeric(df[sort_by], errors="coerce")
            key = numeric if numeric.notna().sum() == df[sort_by].notna().sum() and numeric.notna().any() else df[sort_by]
            ordered_indexes = key.sort_values(ascending=sort_dir == "asc", kind="stable", na_position="last").index
            df = df.loc[ordered_indexes]
        count = len(df)
        chunk = df.iloc[(page - 1) * page_size:page * page_size]
        return {"table": self.table(table_id), "total": count, "page": page,
                "page_size": page_size, "page_count": max(1, (count + page_size - 1) // page_size),
                "row_indexes": [int(i) for i in chunk.index],
                "columns": [str(c) for c in chunk.columns],
                "rows": self._to_rows(chunk),
                "query": {"search": search, "sort_by": sort_by, "sort_dir": sort_dir,
                          "filter_column": filter_column, "filter_operator": filter_operator, "filter_value": filter_value}}

    @staticmethod
    def _to_rows(df: pd.DataFrame) -> list[dict]:
        clean = df.replace({np.nan: None}).astype(object).where(pd.notna(df), None)
        return [{str(k): (None if pd.isna(v) else v.item() if hasattr(v, "item") else v)
                 for k, v in record.items()} for record in clean.to_dict(orient="records")]

    def profile(self, table_id: str) -> dict:
        df = self._frame(table_id)
        result = []
        for entry in self._describe_columns(df):
            col = df[entry["name"]]
            value_counts = col.dropna().astype(str).value_counts().head(5)
            numeric = pd.to_numeric(col, errors="coerce") if entry["type"] == "numeric" else None
            result.append({**entry, "distinct": int(col.nunique(dropna=True)),
                           "null_rate": round(float(col.isna().mean()) if len(df) else 0., 4),
                           "top_values": [{"value": k, "count": int(v)} for k, v in value_counts.items()],
                           "min": float(numeric.min()) if numeric is not None and numeric.notna().any() else None,
                           "max": float(numeric.max()) if numeric is not None and numeric.notna().any() else None})
        return {"table": self.table(table_id), "fields": result,
                "rows": len(df), "columns": len(df.columns),
                "missing_cells": int(df.isna().sum().sum())}

    @staticmethod
    def _columns(df: pd.DataFrame, columns: list[str]) -> None:
        if any(x not in df.columns for x in columns):
            raise BIError(f"字段不存在：{', '.join(x for x in columns if x not in df.columns)}")

    def _apply_steps(self, df: pd.DataFrame, steps: list[dict]) -> pd.DataFrame:
        if not 1 <= len(steps) <= MAX_STEPS:
            raise BIError("每条处理配方需要 1～20 个步骤")
        df = df.copy()
        for idx, step in enumerate(steps):
            try:
                operation = step["type"]
                column = step.get("column")
                if operation == "batch_edit":
                    changes = step.get("changes")
                    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_BATCH_EDITS:
                        raise BIError(f"批量编辑必须包含 1～{MAX_BATCH_EDITS} 个单元格")
                    seen = set()
                    for change in changes:
                        if not isinstance(change, dict):
                            raise BIError("批量编辑记录格式错误")
                        key = (change.get("row_index"), change.get("column"))
                        if key in seen:
                            raise BIError("批量编辑中存在重复单元格")
                        seen.add(key)
                        self._columns(df, [change.get("column")])
                        row_index = change.get("row_index")
                        if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index not in df.index:
                            raise BIError("批量编辑行号无效或已在前序筛选中删除")
                        value = change.get("value")
                        df.at[row_index, change["column"]] = pd.NA if value is None else str(value)
                elif operation == "calculate":
                    left_col = step.get("left_column")
                    right_col = step.get("right_column")
                    self._columns(df, [left_col])
                    op = step.get("operator")
                    dest = self._normal_name(str(step.get("new_name", "")))
                    if dest in df.columns:
                        raise BIError("计算列名称已存在")
                    if op not in {"add", "subtract", "multiply", "divide", "percent", "concat"}:
                        raise BIError("只允许加减乘除、百分比和文本拼接")
                    left = df[left_col]
                    if right_col:
                        self._columns(df, [right_col]); right = df[right_col]
                    else:
                        if "right_value" not in step:
                            raise BIError("请指定第二个字段或常量")
                        right = pd.Series([step["right_value"]] * len(df), index=df.index)
                    if op == "concat":
                        df[dest] = left.fillna("").astype("string") + right.fillna("").astype("string")
                    else:
                        a = pd.to_numeric(left, errors="coerce")
                        b = pd.to_numeric(right, errors="coerce")
                        for series, converted in ((left, a), (right, b)):
                            if (series.notna() & series.astype("string").str.strip().ne("").fillna(False) & converted.isna()).any():
                                raise BIError("计算列包含无法转换为数值的内容")
                        if op == "add": result = a + b
                        elif op == "subtract": result = a - b
                        elif op == "multiply": result = a * b
                        elif op in {"divide", "percent"}:
                            result = a.div(b.mask(b.eq(0)))
                            if op == "percent": result = result * 100
                        df[dest] = result.replace([np.inf, -np.inf], np.nan).round(8)
                elif operation == "trim":
                    self._columns(df, [column])
                    df[column] = df[column].astype("string").str.strip()
                elif operation == "replace":
                    self._columns(df, [column])
                    old = str(step.get("old", ""))
                    if not old:
                        raise BIError("查找文本不能为空")
                    df[column] = df[column].astype("string").str.replace(old, str(step.get("new", "")), regex=False)
                elif operation == "set_cell":
                    self._columns(df, [column])
                    row_index = step.get("row_index")
                    if isinstance(row_index, bool) or not isinstance(row_index, int) or not 0 <= row_index < len(df):
                        raise BIError("单元格行号无效")
                    if row_index not in df.index:
                        raise BIError("该行已经被前面的筛选步骤删除")
                    df.loc[row_index, column] = str(step.get("value", "")) if step.get("value") is not None else pd.NA
                elif operation == "rename":
                    self._columns(df, [column])
                    dest = self._normal_name(str(step.get("new_name", "")))
                    if dest != column and dest in df.columns:
                        raise BIError("重命名字段已存在")
                    df = df.rename(columns={column: dest})
                elif operation == "drop_nulls":
                    cols = step.get("columns") or [column]
                    self._columns(df, cols)
                    df = df.dropna(subset=cols)
                elif operation == "drop_column":
                    self._columns(df, [column])
                    if len(df.columns) <= 1:
                        raise BIError("至少需要保留一个字段")
                    df = df.drop(columns=[column])
                elif operation == "fill_null":
                    self._columns(df, [column]); df[column] = df[column].fillna(str(step.get("value", "")))
                elif operation == "cast":
                    self._columns(df, [column])
                    kind = step.get("dtype")
                    if kind == "number":
                        converted = pd.to_numeric(df[column], errors="coerce")
                        if converted.notna().sum() != df[column].notna().sum():
                            raise BIError("存在无法转换为数值的非空数据")
                        df[column] = converted
                    elif kind == "date":
                        converted = pd.to_datetime(df[column], errors="coerce", utc=True)
                        if converted.notna().sum() != df[column].notna().sum():
                            raise BIError("存在无法转换为日期的非空数据")
                        df[column] = converted.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    elif kind == "text":
                        df[column] = df[column].astype("string")
                    else:
                        raise BIError("仅支持数值、日期和文本类型")
                elif operation == "filter":
                    self._columns(df, [column])
                    op = step.get("operator")
                    value = step.get("value")
                    if op == "contains":
                        mask = df[column].astype("string").str.contains(str(value), regex=False, na=False)
                    elif op in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                        lhs = df[column]
                        if op in {"gt", "gte", "lt", "lte"}:
                            lhs = pd.to_numeric(lhs, errors="coerce")
                            try:
                                value = float(value)
                            except (TypeError, ValueError) as exc:
                                raise BIError("比较值必须是数字") from exc
                        mask = {"eq": lambda: lhs == str(value), "ne": lambda: lhs != str(value),
                                "gt": lambda: lhs > value, "gte": lambda: lhs >= value,
                                "lt": lambda: lhs < value, "lte": lambda: lhs <= value}[op]()
                    else:
                        raise BIError("不支持的筛选操作")
                    df = df[mask.fillna(False)]
                elif operation == "deduplicate":
                    cols = step.get("columns") or [column]
                    self._columns(df, cols)
                    df = df.drop_duplicates(subset=cols, keep="last")
                elif operation == "select":
                    cols = step.get("columns", [])
                    if not cols:
                        raise BIError("请至少选择一个字段")
                    self._columns(df, cols)
                    df = df[cols].copy()
                elif operation == "derive_date":
                    self._columns(df, [column])
                    part = step.get("part")
                    if part not in {"year", "month", "day", "hour"}:
                        raise BIError("日期派生仅支持 year/month/day/hour")
                    dest = self._normal_name(str(step.get("new_name", "")))
                    if dest in df.columns:
                        raise BIError("派生字段已存在")
                    dates = pd.to_datetime(df[column], utc=True, errors="coerce")
                    df[dest] = getattr(dates.dt, part)
                else:
                    raise BIError(f"不支持的数据处理操作：{operation}")
                self._check_frame(df)
            except (KeyError, TypeError, ValueError) as exc:
                raise BIError(f"步骤 {idx+1} 失败：{exc}") from exc
        return df

    def transform(self, table_id: str, name: str, steps: list[dict], *, preview_only: bool = False) -> dict:
        raw = self._frame(table_id)
        before = self._frame_summary(raw)
        df = self._apply_steps(raw, steps)
        if preview_only:
            return self._preview_frame(table_id, before, df, len(steps))
        return self._save(name, df.reset_index(drop=True),
                          {"type": "transform", "source_table_id": table_id, "steps": steps})

    @staticmethod
    def _frame_summary(df: pd.DataFrame) -> dict:
        return {"rows": len(df), "columns": len(df.columns), "missing_cells": int(df.isna().sum().sum())}

    def _preview_frame(self, table_id: str, before: dict, df: pd.DataFrame, count: int) -> dict:
        view = df.head(30)
        return {"preview_only": True, "source_table_id": table_id, "before": before,
                "after": self._frame_summary(df), "columns": list(df.columns),
                "rows": self._to_rows(view), "row_indexes": [int(i) for i in view.index], "steps": count}

    @staticmethod
    def _workflow_digest(spec: WorkflowSpec) -> str:
        import hashlib
        return hashlib.sha256(spec.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()

    def stage_workflow(self, table_id: str, spec: WorkflowSpec) -> dict:
        """Preview is registered with an expiring, one-use capability tied to a snapshot."""
        import secrets
        result = self.workflow(table_id, spec, preview_only=True)
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        with self.store.sessions.begin() as session:
            session.add(BIWorkflowPreviewRow(
                token=token, table_id=table_id,
                plan_digest=self._workflow_digest(spec),
                source_sha256=self._row(table_id).sha256,
                expires_at=expires, used=False))
        return {**result, "preview_token": token, "expires_at": expires.isoformat()}

    def execute_staged_workflow(self, table_id: str, spec: WorkflowSpec, token: str) -> dict:
        if not token or len(token) > 100:
            raise BIError("缺少有效的预览凭证，请先预览分析步骤")
        with self.store.sessions.begin() as session:
            row = session.get(BIWorkflowPreviewRow, token)
            if row is None or row.table_id != table_id:
                raise BIError("预览凭证不存在或与当前数据表不匹配")
            expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
            if datetime.now(timezone.utc) >= expires:
                raise BIError("预览已过期，请重新检查操作后再保存")
            if row.plan_digest != self._workflow_digest(spec) or row.source_sha256 != self._row(table_id).sha256:
                raise BIError("分析计划或数据版本已变化，请重新预览")
            # Atomic claim prevents two simultaneous or repeated requests from creating two copies.
            claimed = session.execute(update(BIWorkflowPreviewRow)
                       .where(BIWorkflowPreviewRow.token == token, BIWorkflowPreviewRow.used.is_(False))
                       .values(used=True))
            if claimed.rowcount != 1:
                raise BIError("该预览已保存过，不能重复提交")
        return self.workflow(table_id, spec, preview_only=False)

    def join(self, left_id: str, right_id: str, *, name: str, keys: list[str], how: str) -> dict:
        if how not in {"left", "inner"} or not 1 <= len(keys) <= 3:
            raise BIError("关联仅支持 LEFT/INNER，且需要 1～3 个关联字段")
        left = self._frame(left_id); right = self._frame(right_id)
        self._columns(left, keys); self._columns(right, keys)
        if right.duplicated(keys).any():
            raise BIError("右侧关联字段存在重复值。请先去重或聚合，防止多对多关联导致记录膨胀")
        merged = left.merge(right, on=keys, how=how, suffixes=("", "_right"), validate="many_to_one")
        return self._save(name, merged, {"type": "join", "left_table_id": left_id,
                                         "right_table_id": right_id, "keys": keys, "how": how})

    def _aggregate_frame(self, df: pd.DataFrame, spec: AggregateSpec) -> dict:
        self._check_linked_numeric_scope(df, spec.metric.operation)
        group_by = spec.group_by
        self._columns(df, group_by)
        if spec.metric.operation not in {"count", "sum", "mean", "min", "max", "nunique"}:
            raise BIError("仅支持 count/sum/mean/min/max/nunique 聚合")
        col = spec.metric.column
        if spec.metric.operation != "count":
            if col is None:
                raise BIError("请指定需要聚合的数值字段")
            self._columns(df, [col])
        metric_name = f"{spec.metric.operation}_{col or 'rows'}"
        if spec.metric.operation in {"sum", "mean", "min", "max"}:
            df = df.copy()
            metric_col = "__tp_metric_value__"
            while metric_col in df.columns:
                metric_col += "_"
            df[metric_col] = pd.to_numeric(df[col], errors="coerce")
        else:
            metric_col = col
        if group_by:
            grouped = df.groupby(group_by, dropna=False, observed=True)
            if spec.metric.operation == "count":
                result = grouped.size().reset_index(name=metric_name)
            elif spec.metric.operation == "nunique":
                result = grouped[metric_col].nunique().reset_index(name=metric_name)
            else:
                result = grouped[metric_col].agg(spec.metric.operation).reset_index(name=metric_name)
        else:
            if spec.metric.operation == "count":
                value = len(df)
            elif spec.metric.operation == "nunique":
                value = df[metric_col].nunique()
            else:
                value = getattr(df[metric_col], spec.metric.operation)()
            result = pd.DataFrame([{metric_name: value}])
        if spec.order not in {"asc", "desc"} or spec.sort_by not in {"metric", "group"}:
            raise BIError("排序方式必须为 metric/group 和 asc/desc")
        sort_columns = group_by if spec.sort_by == "group" and group_by else [metric_name]
        result = result.sort_values(sort_columns, ascending=spec.order == "asc", na_position="last")
        total = len(result)
        return {"group_by": group_by, "value_field": metric_name,
                "total_groups": total, "rows": self._to_rows(result.head(spec.limit))}

    def aggregate(self, table_id: str, spec: AggregateSpec) -> dict:
        return {"table_id": table_id, **self._aggregate_frame(self._frame(table_id), spec)}

    def _pivot_frame(self, df: pd.DataFrame, spec: PivotSpec) -> dict:
        self._check_linked_numeric_scope(df, spec.metric.operation)
        self._columns(df, [*spec.group_by, spec.pivot_column])
        if spec.pivot_column in spec.group_by:
            raise BIError("透视列不能同时作为行分组字段")
        metric = spec.metric
        if metric.operation not in {"count", "sum", "mean", "min", "max", "nunique"}:
            raise BIError("不支持的透视统计方式")
        if metric.operation != "count":
            if not metric.column:
                raise BIError("该统计方式必须选择数值或计数字段")
            self._columns(df, [metric.column])
        values = df[spec.pivot_column].fillna("(空值)").astype("string")
        categories = sorted(values.unique().tolist())
        if len(categories) > MAX_PIVOT_COLUMNS:
            raise BIError(f"透视字段超过 {MAX_PIVOT_COLUMNS} 个不同值，请先筛选或合并类别")
        working = df.copy()
        working[spec.pivot_column] = values
        if metric.operation in {"sum", "mean", "min", "max"}:
            numbers = pd.to_numeric(working[metric.column], errors="coerce")
            invalid = working[metric.column].notna() & numbers.isna()
            if invalid.any():
                raise BIError("透视指标包含无法转换为数值的内容")
            value_column = "__tp_pivot_metric__"
            while value_column in working.columns:
                value_column += "_"
            working[value_column] = numbers
            aggfunc = metric.operation
        elif metric.operation == "nunique":
            value_column = metric.column
            aggfunc = "nunique"
        else:
            value_column = "__tp_pivot_metric__"
            while value_column in working.columns:
                value_column += "_"
            working[value_column] = 1
            aggfunc = "sum"
        grouped = working.groupby([*spec.group_by, spec.pivot_column], dropna=False, observed=True)[value_column].agg(aggfunc)
        result = grouped.unstack(spec.pivot_column)
        if len(result) > MAX_REPORT_ROWS:
            raise BIError(f"透视结果超过 {MAX_REPORT_ROWS} 行，请先筛选分组范围")
        total = len(result)
        result = result.head(spec.limit).reset_index()
        result.columns = [str(c) for c in result.columns]
        result = result.replace([np.inf, -np.inf], np.nan)
        return {"group_by": spec.group_by, "pivot_column": spec.pivot_column,
                "metric": metric.model_dump(), "columns": list(result.columns),
                "total_groups": total, "rows": self._to_rows(result)}

    def pivot(self, table_id: str, spec: PivotSpec) -> dict:
        return {"table_id": table_id, **self._pivot_frame(self._frame(table_id), spec)}

    def workflow(self, table_id: str, spec: WorkflowSpec, *, preview_only: bool = True) -> dict:
        raw = self._frame(table_id)
        df = raw
        summaries = []
        change_count = 0
        # Preview is fully in-memory; no file or database writes happen until confirmed execution.
        for i, action in enumerate(spec.steps):
            if action.tool == "bi.transform":
                steps = action.arguments.get("steps")
                if not isinstance(steps, list):
                    raise BIError(f"第 {i+1} 步没有有效的处理配方")
                change_count += len(steps)
                if change_count > MAX_STEPS:
                    raise BIError(f"整个工作流最多执行 {MAX_STEPS} 个数据处理操作")
                df = self._apply_steps(df, steps)
                summaries.append({"step": i+1, "tool": action.tool, "result": self._frame_summary(df)})
            elif action.tool == "bi.profile":
                # Return bounded, non-sensitive metadata, not raw data content.
                summaries.append({"step": i+1, "tool": action.tool, "result": {**self._frame_summary(df),
                    "fields": self._describe_columns(df)}})
            elif action.tool == "bi.aggregate":
                result = self._aggregate_frame(df, AggregateSpec.model_validate(action.arguments))
                summaries.append({"step": i+1, "tool": action.tool, "result": result})
            elif action.tool == "bi.pivot":
                result = self._pivot_frame(df, PivotSpec.model_validate(action.arguments))
                summaries.append({"step": i+1, "tool": action.tool, "result": result})
            else:
                raise BIError(f"第 {i+1} 步包含未经允许的工具：{action.tool}")
        detail = {"source_table_id": table_id, "before": self._frame_summary(raw),
                  "after": self._frame_summary(df), "steps": summaries,
                  "preview": self._to_rows(df.head(12)), "columns": list(df.columns),
                  "writes_data": change_count > 0}
        if preview_only:
            return {"preview_only": True, **detail}
        if change_count == 0:
            raise BIError("本流程只有只读分析，不需要保存数据；请直接查看预览")
        saved = self._save(spec.name, df.reset_index(drop=True),
                           {"type": "workflow", "source_table_id": table_id,
                            "steps": [step.model_dump() for step in spec.steps]})
        return {"preview_only": False, "saved_table": saved, **detail}

    def save_report(self, spec: ReportSpec) -> dict:
        if spec.chart_type not in {"bar", "line", "pie", "table", "pivot"}:
            raise BIError("图表类型仅支持柱状、折线、饼图、数据表和透视表")
        if spec.chart_type == "pivot":
            if spec.pivot is None:
                raise BIError("保存透视报表前需要提供透视配置")
            self.pivot(spec.table_id, spec.pivot)
        else:
            self.aggregate(spec.table_id, spec.aggregate)
        rid = str(uuid4())
        row = BIReportRow(id=rid, name=self._normal_name(spec.name), table_id=spec.table_id,
                          spec_json=spec.model_dump_json())
        with self.store.sessions.begin() as session:
            session.add(row)
        return self._report_meta(row)

    @staticmethod
    def _report_meta(row: BIReportRow) -> dict:
        return {"report_id": row.id, "name": row.name, "table_id": row.table_id,
                "spec": json.loads(row.spec_json),
                "created_at": row.created_at.isoformat() if row.created_at else None}

    def list_reports(self) -> list[dict]:
        with self.store.sessions() as s:
            return [self._report_meta(r) for r in s.scalars(select(BIReportRow).order_by(BIReportRow.created_at.desc()))]

    def report(self, report_id: str) -> dict:
        with self.store.sessions() as session:
            row = session.get(BIReportRow, report_id)
            if row is None:
                raise KeyError("报表不存在")
            meta = self._report_meta(row)
        spec = ReportSpec.model_validate(meta["spec"])
        result = self.pivot(spec.table_id, spec.pivot) if spec.chart_type == "pivot" and spec.pivot else self.aggregate(spec.table_id, spec.aggregate)
        return {**meta, "result": result}

    def export_csv(self, table_id: str) -> bytes:
        df = self._frame(table_id)
        if len(df) > MAX_ROWS:
            raise BIError("超出导出上限")
        # Spreadsheet formula injection protection for exported text fields.
        for col in df.columns:
            texts = df[col].astype("string")
            danger = texts.str.match(r"^[\s]*[=+@-]", na=False) & ~texts.str.match(r"^\s*-?\d+(\.\d+)?$", na=False)
            df.loc[danger, col] = "'" + texts[danger]
        return ("\ufeff" + df.to_csv(index=False)).encode("utf-8")

    @staticmethod
    def _xlsx_bytes(columns: list[str], rows: list[dict], *, source: str, types: dict | None = None) -> bytes:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from math import isfinite
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("数据表")
        ws.append(columns)
        types = types or {}
        for record in rows:
            cells = []
            for name in columns:
                value = record.get(name)
                if value is None or (not isinstance(value, str) and pd.isna(value)):
                    cells.append(None)
                    continue
                text = str(value)
                if types.get(name) == "numeric":
                    try:
                        number = float(text)
                    except ValueError:
                        number = None
                    if number is not None and isfinite(number):
                        cells.append(number)
                        continue
                cell = WriteOnlyCell(ws, value=text)
                # An untrusted value beginning with '=' must remain literal text.
                cell.data_type = "s"
                cells.append(cell)
            ws.append(cells)
        note = wb.create_sheet("来源说明")
        note.append(["数据来源", source])
        note.append(["说明", "导出文件基于 TimePilot 已冻结数据快照；请按数据字典核对时间和单位。"])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def export_xlsx(self, table_id: str) -> bytes:
        df = self._frame(table_id)
        if len(df) > MAX_XLSX_EXPORT_ROWS:
            raise BIError(f"Excel 导出最多 {MAX_XLSX_EXPORT_ROWS:,} 行；请使用 CSV 导出完整数据")
        columns = [str(x) for x in df.columns]
        types = {column["name"]: column["type"] for column in self.table(table_id)["columns"]}
        return self._xlsx_bytes(columns, self._to_rows(df), source=table_id, types=types)

    def export_report_xlsx(self, report_id: str) -> bytes:
        report = self.report(report_id)
        result = report["result"]
        rows = result["rows"]
        columns = result.get("columns") or (list(rows[0]) if rows else [])
        return self._xlsx_bytes(columns, rows, source=f"report:{report_id}")

    def to_timeseries(self, table_id: str, *, name: str, frequency: str,
                      timestamp_col: str, target_col: str, entity_col: str,
                      available_at_col: str, source_timezone: str, unit: str) -> dict:
        from timepilot.data import create_dataset
        origin=json.loads(self._row(table_id).lineage_json)
        if origin.get('type')=='data_asset_reference':
            raise BIError('统一数据资产必须经时序工作台检查发布时间、重复记录和统计口径后生成训练数据集')
        df = self._frame(table_id)
        self._columns(df, [timestamp_col, target_col, entity_col, available_at_col])
        payload = df.to_csv(index=False).encode("utf-8")
        manifest = create_dataset(self.store, self.settings, payload, f"{table_id}.csv", name=name,
                                  frequency=frequency, timestamp_col=timestamp_col, target_col=target_col,
                                  entity_col=entity_col, available_at_col=available_at_col,
                                  source_timezone=source_timezone, unit=unit)
        return manifest.model_dump(mode="json")
