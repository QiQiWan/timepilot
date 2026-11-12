"""SQLAlchemy metadata store: SQLite for one local worker; PostgreSQL for scaling."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, event, select, update, inspect as sqlalchemy_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DatasetRow(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True)
    task_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(60), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class BITableRow(Base):
    __tablename__ = "bi_tables"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    columns_json: Mapped[str] = mapped_column(Text)
    lineage_json: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BIReportRow(Base):
    __tablename__ = "bi_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    table_id: Mapped[str] = mapped_column(ForeignKey("bi_tables.id"))
    spec_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BIWorkflowPreviewRow(Base):
    __tablename__ = "bi_workflow_previews"
    token: Mapped[str] = mapped_column(String(100), primary_key=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("bi_tables.id"), index=True)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NativeBIAssetRow(Base):
    __tablename__ = "native_bi_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    columns_json: Mapped[str] = mapped_column(Text)
    lineage_json: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    file_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NativeBISavedViewRow(Base):
    __tablename__ = "native_bi_views"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("native_bi_assets.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)




class NativeBIAssetHeadRow(Base):
    """Mutable pointer; all data, revisions and stored deltas remain immutable."""
    __tablename__ = "native_bi_asset_heads"
    asset_id: Mapped[str] = mapped_column(ForeignKey("native_bi_assets.id"), primary_key=True)
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NativeBIEditVersionRow(Base):
    __tablename__ = "native_bi_edit_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("native_bi_assets.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    previous_head_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    delta_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edit_count: Mapped[int] = mapped_column(Integer, default=0)
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    comment: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NativeBIEditPreviewRow(Base):
    __tablename__ = "native_bi_edit_previews"
    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("native_bi_assets.id"), index=True)
    based_on_version: Mapped[str] = mapped_column(String(36), nullable=False)
    edits_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSettingRow(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def check_existing_sqlite_metadata(db_url: str) -> dict:
    """Inspect an existing local store before SQLAlchemy create_all writes DDL."""
    from sqlalchemy.engine import make_url
    url = make_url(db_url)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        return {'status': 'non_file_sqlite_or_external'}
    if url.query.get('uri') == 'true' or url.database.startswith('file:'):
        return {'status': 'sqlite_uri_not_preinspected'}
    path = Path(url.database).expanduser().resolve()
    if not path.exists():
        return {'status': 'new', 'path': str(path)}
    if not path.is_file():
        raise RuntimeError(f'TimePilot metadata database path is not a file: {path}')
    uri = 'file:' + quote(str(path), safe='/:') + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True, timeout=15) as db:
            checked = db.execute('PRAGMA quick_check(1)').fetchone()
            if not checked or checked[0] != 'ok':
                raise RuntimeError('TimePilot metadata database integrity check failed: ' + str(checked))
            existing = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            expected = Base.metadata.tables
            if existing and not (existing & set(expected)):
                raise RuntimeError(
                    f'TimePilot metadata database is not a recognized TimePilot store: {path}. '
                    'No new tables were created.')
            drift = []
            for name, table in expected.items():
                if name not in existing:
                    continue
                # Name originates from our fixed SQLAlchemy model, never user input.
                columns = {row[1] for row in db.execute('PRAGMA table_info("' + name + '")')}
                missing = {column.name for column in table.columns} - columns
                if missing:
                    drift.append(f'{name}: {", ".join(sorted(missing))}')
            if drift:
                raise RuntimeError(
                    'TimePilot metadata database schema is from a different release; '
                    'missing columns: ' + '; '.join(drift) + '. '
                    'No SQLite DDL was executed. Back up and apply a reviewed migration.')
            return {'status': 'current_or_additive', 'path': str(path),
                    'known_tables': len(existing & set(expected))}
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f'TimePilot metadata SQLite file cannot be inspected: {path}: {exc}') from exc


class Store:
    def __init__(self, db_url: str):
        # create_all() may add tables before it discovers an incompatible old
        # column. Inspect existing user data with a READ-ONLY SQLite handle.
        self.startup_metadata = check_existing_sqlite_metadata(db_url)
        kw = {"pool_pre_ping": True}
        if db_url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(db_url, **kw)
        if db_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        Base.metadata.create_all(self.engine)  # Local bootstrap only; never mutates existing columns.
        # create_all() does not apply ALTER TABLE to an older SQLite database.
        # Fail with one readable schema error instead of surfacing random SQL
        # OperationalError in unrelated endpoints after the server has started.
        inspector = sqlalchemy_inspect(self.engine)
        drift = []
        for table in Base.metadata.sorted_tables:
            present = {column['name'] for column in inspector.get_columns(table.name)}
            missing = {column.name for column in table.columns} - present
            if missing:
                drift.append(f'{table.name}: {", ".join(sorted(missing))}')
        if drift:
            self.engine.dispose()
            raise RuntimeError(
                'TimePilot metadata database schema is from a different release; '
                'missing columns: ' + '; '.join(drift) + '. '
                'Your data was not deleted or auto-migrated. Back up metadata.sqlite3 '
                'and apply a reviewed schema migration before startup.')
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def dataset_by_name_or_id(self, value: str) -> DatasetRow | None:
        with self.sessions() as s:
            return s.scalar(select(DatasetRow).where((DatasetRow.id == value) | (DatasetRow.name == value)))

    def run(self, value: str) -> RunRow | None:
        with self.sessions() as s:
            return s.get(RunRow, value)

    def list_runs(self, limit: int = 50) -> list[RunRow]:
        with self.sessions() as s:
            return list(s.scalars(select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)))

    def list_datasets(self) -> list[DatasetRow]:
        with self.sessions() as s:
            return list(s.scalars(select(DatasetRow).order_by(DatasetRow.created_at.desc())))

    def update_run(self, run_id: str, **fields) -> None:
        with self.sessions.begin() as s:
            s.execute(update(RunRow).where(RunRow.id == run_id).values(**fields))

    def enqueue(self, task_json: str, dataset_id: str, task_id: str) -> str:
        run_id = str(uuid4())
        with self.sessions.begin() as s:
            s.add(RunRow(id=run_id, task_json=task_json, dataset_id=dataset_id, task_id=task_id))
        return run_id
