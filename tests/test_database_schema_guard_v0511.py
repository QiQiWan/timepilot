"""Avoid late, confusing SQL errors when local SQLite data has an older schema."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from timepilot.db import Store


def test_store_blocks_existing_table_with_missing_columns(tmp_path: Path):
    url = f'sqlite:///{tmp_path / "outdated.sqlite3"}'
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE datasets (id VARCHAR(36) PRIMARY KEY, name VARCHAR(150))'))
    engine.dispose()
    with pytest.raises(RuntimeError, match='schema is from a different release') as error:
        Store(url)
    assert 'datasets: created_at, manifest_json' in str(error.value)
    # Guard must preserve existing records/tables and report missing fields.
    engine = create_engine(url)
    with engine.connect() as conn:
        found = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert found == {'datasets'}  # No unrelated TimePilot tables created before failure.
    engine.dispose()


def test_store_accepts_complete_fresh_schema(tmp_path: Path):
    url = f'sqlite:///{tmp_path / "fresh.sqlite3"}'
    store = Store(url)
    try:
        assert store.engine is not None
    finally:
        store.engine.dispose()


def test_store_rejects_unrelated_metadata_without_modification(tmp_path: Path):
    url = f'sqlite:///{tmp_path / "unknown.sqlite3"}'
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE unrelated_business_data (id INTEGER PRIMARY KEY)'))
        conn.execute(text('INSERT INTO unrelated_business_data VALUES (41)'))
    engine.dispose()
    with pytest.raises(RuntimeError, match='not a recognized TimePilot store'):
        Store(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert names == {'unrelated_business_data'}
        assert conn.execute(text('SELECT id FROM unrelated_business_data')).scalar_one() == 41
    engine.dispose()
