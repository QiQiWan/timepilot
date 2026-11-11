"""Data ingestion, immutable snapshots and point-in-time validation."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy.exc import IntegrityError

from timepilot.config import Settings
from timepilot.contracts import DatasetManifest, FieldSpec, ValidationIssue
from timepilot.db import DatasetRow, Store
from timepilot.storage import atomic_json, atomic_write_bytes, load_json, sha256_bytes, sha256_file


class DataValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{x.code}: {x.message}" for x in issues))


def issue(code: str, message: str, *, severity="blocker", column=None, action=None):
    return ValidationIssue(code=code, severity=severity, message=message, column=column, suggested_action=action)


def _read_file(content: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(io.BytesIO(content), dtype="string")  # Preserve IDs such as 00123
    if suffix == ".xlsx":
        return pd.read_excel(io.BytesIO(content), engine="openpyxl")
    if suffix == ".parquet":
        return pd.read_parquet(io.BytesIO(content))
    raise DataValidationError([issue("DATA-FORMAT-001", "Only CSV, XLSX and Parquet are supported.")])


def normalize_frame(
    df: pd.DataFrame,
    *,
    timestamp_col: str = "timestamp",
    target_col: str = "target",
    entity_col: str | None = "series_id",
    available_at_col: str | None = "available_at",
    publication_lag_days: int | None = None,
    frequency: str = "MS",
    source_timezone: str = "UTC",
    max_rows: int = 200_000,
) -> tuple[pd.DataFrame, list[ValidationIssue]]:
    """Normalize to [series_id,timestamp,target,available_at]; never silently impute targets."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    required = [timestamp_col, target_col]
    if entity_col:
        required.append(entity_col)
    if available_at_col:
        required.append(available_at_col)
    for name in required:
        if name not in df:
            if name == available_at_col and publication_lag_days is not None:
                continue
            errors.append(issue("DATA-COLUMN-001", f"Required field '{name}' is missing.", column=name,
                                action="Map the correct field or declare an explicit publication lag."))
    if errors:
        raise DataValidationError(errors)
    if df.empty or len(df) > max_rows:
        raise DataValidationError([issue("DATA-SIZE-001", f"Rows must be between 1 and {max_rows}.")])
    frame = pd.DataFrame()
    frame["series_id"] = (df[entity_col].astype("string") if entity_col else pd.Series("series_1", index=df.index))
    try:
        tz = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise DataValidationError([issue("DATA-TZ-001", f"Unknown source timezone: {source_timezone}")]) from exc
    if frequency in {"MS", "D"}:
        # Period labels retain the business calendar date; do not shift it across days on UTC conversion.
        period_labels = df[timestamp_col].astype("string").str.slice(0, 10)
        frame["timestamp"] = pd.to_datetime(period_labels, errors="coerce", utc=True)
    else:
        raw_time = pd.to_datetime(df[timestamp_col], errors="coerce", utc=False)
        if raw_time.dtype == "object":
            raise DataValidationError([issue("DATA-TZ-MIXED", "Mixed timezone offsets or date formats in timestamp; normalize source timestamps first.")])
        if isinstance(raw_time.dtype, pd.DatetimeTZDtype):
            frame["timestamp"] = raw_time.dt.tz_convert("UTC")
        else:
            frame["timestamp"] = raw_time.dt.tz_localize(source_timezone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    frame["target"] = pd.to_numeric(df[target_col], errors="coerce")
    if available_at_col and available_at_col in df:
        raw_avail = pd.to_datetime(df[available_at_col], errors="coerce", utc=False)
        if raw_avail.dtype == "object":
            raise DataValidationError([issue("DATA-TZ-MIXED", "Mixed timezone offsets or date formats in available_at; normalize source timestamps first.")])
        if isinstance(raw_avail.dtype, pd.DatetimeTZDtype):
            frame["available_at"] = raw_avail.dt.tz_convert("UTC")
        else:
            frame["available_at"] = raw_avail.dt.tz_localize(source_timezone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    else:
        if publication_lag_days is None or publication_lag_days < 0:
            raise DataValidationError([issue("DATA-AVAIL-001", "available_at is required to establish historical data availability.")])
        frame["available_at"] = frame["timestamp"] + pd.to_timedelta(publication_lag_days, unit="D")
        warnings.append(issue("DATA-AVAIL-ASSUMED", "Data availability was derived from a declared lag; independently verify publication timestamps.",severity="warning",column="available_at"))
    invalid = frame[["series_id", "timestamp", "target", "available_at"]].isna().any(axis=1)
    invalid = invalid | frame["series_id"].str.strip().eq("").fillna(True)
    if invalid.any():
        raise DataValidationError([issue("DATA-NULL-001", f"{int(invalid.sum())} rows contain invalid required values.",
                                         action="Correct invalid dates, identifiers, or missing targets before importing.")])
    if (~frame["target"].map(lambda x: pd.notna(x) and abs(float(x)) != float("inf"))).any():
        raise DataValidationError([issue("DATA-NUMBER-001", "Targets must be finite numbers.")])
    if (frame["timestamp"].dt.year < 1970).any() or (frame["timestamp"].dt.year > 2100).any():
        raise DataValidationError([issue("DATA-DATE-RANGE-001", "Timestamp outside 1970-2100; check Unix/Excel date parsing and time units.")])
    duplicates = frame.duplicated(["series_id", "timestamp"], keep=False)
    if duplicates.any():
        raise DataValidationError([issue("DATA-DUP-001", f"{int(duplicates.sum())} duplicate series/timestamp rows.",
                                         action="Resolve duplicate observations using a documented business rule.")])
    if (frame["target"] < 0).any():
        warnings.append(issue("DATA-NEG-001", "Negative target values exist; confirm they are valid for this business definition.", severity="warning", column="target"))
    if (frame["available_at"] < frame["timestamp"]).any():
        warnings.append(issue("DATA-AVAIL-EARLY", "Some observations appear available before their timestamp; review date semantics.", severity="warning",column="available_at"))
    frame = frame.sort_values(["series_id", "timestamp"]).reset_index(drop=True)
    # Keep UTC in the canonical store; CSV serialization is deliberately stable and readable.
    for col in ["timestamp", "available_at"]:
        frame[col] = frame[col].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return frame, warnings


def create_dataset(
    store: Store,
    settings: Settings,
    content: bytes,
    filename: str,
    *,
    name: str,
    timestamp_col: str = "timestamp",
    target_col: str = "target",
    entity_col: str | None = "series_id",
    available_at_col: str | None = "available_at",
    publication_lag_days: int | None = None,
    frequency: str = "MS",
    unit: str = "unit",
    source_timezone: str = "UTC",
) -> DatasetManifest:
    if frequency not in {"MS", "D", "h"}:
        raise DataValidationError([issue("DATA-FREQ-001", f"Unsupported frequency: {frequency}")])
    if len(content) > settings.max_upload_bytes:
        raise DataValidationError([issue("DATA-UPLOAD-001", "File exceeds upload size limit.")])
    if not name.strip():
        raise DataValidationError([issue("DATA-NAME-001", "Dataset name must be provided.")])
    frame, warnings = normalize_frame(_read_file(content, filename), timestamp_col=timestamp_col,
                                      target_col=target_col, entity_col=entity_col, available_at_col=available_at_col,
                                      publication_lag_days=publication_lag_days, frequency=frequency,
                                      source_timezone=source_timezone)
    normalized_bytes = frame.to_csv(index=False, float_format="%.15g").encode("utf-8")
    dataset_id = str(uuid4())
    manifest = DatasetManifest(
        dataset_id=dataset_id, name=name.strip(), source_name=Path(filename).name,
        source_sha256=sha256_bytes(content), snapshot_sha256=sha256_bytes(normalized_bytes),
        created_at=datetime.now(timezone.utc), frequency=frequency, source_timezone=source_timezone,
        timestamp_semantics="period_start" if frequency in {"MS", "D"} else "instant", unit=unit,
        record_count=len(frame), series_count=frame["series_id"].nunique(),
        columns=[FieldSpec(name="series_id", role="entity"), FieldSpec(name="timestamp", role="timestamp", dtype="datetime"),
                 FieldSpec(name="target", role="target", dtype="float", unit=unit),
                 FieldSpec(name="available_at", role="available_at", dtype="datetime")],
        warnings=warnings,
    )
    root = settings.home / "datasets" / dataset_id
    atomic_write_bytes(root / "observations.csv", normalized_bytes)
    atomic_json(root / "manifest.json", manifest.model_dump(mode="json"))
    try:
        with store.sessions.begin() as s:
            s.add(DatasetRow(id=dataset_id, name=name.strip(), manifest_json=manifest.model_dump_json()))
    except IntegrityError as exc:
        # Avoid orphaned user-facing records; old snapshots remain immutable on disk.
        raise DataValidationError([issue("DATA-NAME-002", f"Dataset name '{name}' already exists.")]) from exc
    return manifest


def get_dataset(store: Store, settings: Settings, dataset: str) -> tuple[DatasetManifest, pd.DataFrame]:
    row = store.dataset_by_name_or_id(dataset)
    if row is None:
        raise KeyError(f"Dataset '{dataset}' does not exist.")
    manifest = DatasetManifest.model_validate_json(row.manifest_json)
    path = settings.home / "datasets" / manifest.dataset_id / "observations.csv"
    if sha256_file(path) != manifest.snapshot_sha256:
        raise DataValidationError([issue("DATA-CHECKSUM-001", "Immutable dataset snapshot checksum mismatch.")])
    frame = pd.read_csv(path, dtype={"series_id": "string"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True)
    return manifest, frame



def create_modeling_dataset(store: Store, settings: Settings, frame: pd.DataFrame, *,
                            name: str, source_sha256: str, source_name: str,
                            frequency: str, unit: str, task_kind: str,
                            feature_columns: list[str], modeling_contract: dict) -> DatasetManifest:
    """Publish an already validated industrial snapshot without dropping features.

    The selected raw input SHA-256 is separate from the final normalized snapshot
    checksum. The latter protects the exact bytes used by the engine.
    """
    if frequency not in {'MS','D','h'} or task_kind not in {'continuous_forecast','classification'}:
        raise ValueError('不支持的数据频率或任务类型')
    required={'series_id','timestamp','target','available_at'}
    if not required.issubset(frame.columns) or not set(feature_columns).issubset(frame.columns):
        raise ValueError('规范化数据缺少必须字段')
    if frame.empty or len(frame)>200_000:
        raise ValueError('训练数据规模不在 1—200000 行范围内')
    if len(set(feature_columns))!=len(feature_columns):raise ValueError('特征名不能重复')
    for column in ['timestamp','available_at',*(['label_available_at'] if task_kind=='classification' else [])]:
        if column not in frame or pd.to_datetime(frame[column],utc=True,errors='coerce').isna().any():
            raise ValueError(f'{column} 存在不可解析的时间')
    numeric=['target',*feature_columns]
    import numpy as np
    for column in numeric:
        if pd.to_numeric(frame[column],errors='coerce').isna().any() or not np.isfinite(pd.to_numeric(frame[column]).to_numpy(dtype=float)).all():
            raise ValueError('数值字段包含缺失或非有限值：'+column)
    if task_kind=='classification':
        if set(pd.to_numeric(frame['target']).unique())-{0,1}:raise ValueError('二分类标签必须归一化为 0/1')
    elif frame.duplicated(['series_id','timestamp']).any():
        raise ValueError('预测序列存在重复实体时间点')
    contents=frame.to_csv(index=False,float_format='%.15g').encode('utf-8')
    if len(contents)>settings.max_upload_bytes:raise ValueError('规范化快照超过系统存储上限')
    dataset_id=str(uuid4())
    specs=[FieldSpec(name='series_id',role='entity'),FieldSpec(name='timestamp',role='timestamp',dtype='datetime'),
           FieldSpec(name='target',role='target',dtype='float',unit=unit),
           FieldSpec(name='available_at',role='available_at',dtype='datetime')]
    specs.extend(FieldSpec(name=c,role='observed',dtype='float') for c in feature_columns)
    if task_kind=='classification':specs.append(FieldSpec(name='label_available_at',role='available_at',dtype='datetime'))
    manifest=DatasetManifest(dataset_id=dataset_id,name=name,source_name=source_name,
        source_sha256=source_sha256,snapshot_sha256=sha256_bytes(contents),
        created_at=datetime.now(timezone.utc),frequency=frequency,source_timezone='UTC',
        timestamp_semantics='instant',unit=unit,record_count=len(frame),
        series_count=frame['series_id'].nunique(),columns=specs,task_kind=task_kind,
        feature_columns=feature_columns,modeling_contract=modeling_contract)
    root=settings.home/'datasets'/dataset_id
    atomic_write_bytes(root/'observations.csv',contents)
    atomic_json(root/'manifest.json',manifest.model_dump(mode='json'))
    try:
        with store.sessions.begin() as session:
            session.add(DatasetRow(id=dataset_id,name=name,manifest_json=manifest.model_dump_json()))
    except IntegrityError as exc:
        raise ValueError('训练集名称已存在') from exc
    return manifest
