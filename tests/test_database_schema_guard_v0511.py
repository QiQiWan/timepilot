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
        count = conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name='datasets' AND type='table'"))
        assert count.scalar_one() == 1
    engine.dispose()


def test_store_accepts_complete_fresh_schema(tmp_path: Path):
    url = f'sqlite:///{tmp_path / "fresh.sqlite3"}'
    store = Store(url)
    try:
        assert store.engine is not None
    finally:
        store.engine.dispose()
