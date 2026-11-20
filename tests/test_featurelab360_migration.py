"""v0.36.0 read-only guard, forward-only SQLite catalog migration tests.

No production data source or application directory is opened by these tests.
"""
import sqlite3

import pytest

from timepilot.project_migrations import (
    CatalogSchemaError, TARGET_SCHEMA, inspect_catalog, migrate, require_supported_catalog,
)


def test_catalog_refuses_newer_schema_without_changing_the_file(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)')
        db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        db.execute('INSERT INTO project_schema VALUES (?)', (TARGET_SCHEMA + 1,))
    contents = path.read_bytes()
    state = inspect_catalog(path)
    assert state['status'] == 'newer'
    with pytest.raises(CatalogSchemaError):
        require_supported_catalog(path)
    assert path.read_bytes() == contents


def test_incomplete_nonempty_catalog_is_not_reset(tmp_path):
    path=tmp_path / 'catalog.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE business_records (id TEXT PRIMARY KEY)')
        db.execute("INSERT INTO business_records VALUES ('keep-this')")
    assert inspect_catalog(path)['status'] == 'unrecognized'
    with pytest.raises(CatalogSchemaError):
        require_supported_catalog(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id FROM business_records').fetchone()[0] == 'keep-this'


def test_legacy_project_schema_migrates_additively_without_losing_rows():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,tenant_id TEXT)')
    db.execute("INSERT INTO projects VALUES ('project-legacy','retained','local')")
    db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
    db.execute('INSERT INTO project_schema VALUES (4)')
    # Minimal catalog seed: only persistent project row, no business files.
    migrate(db)
    assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA
    assert db.execute('SELECT name FROM projects WHERE id=?', ('project-legacy',)).fetchone()[0] == 'retained'
    assert db.execute("SELECT name FROM sqlite_master WHERE name='feature_analysis_sessions'").fetchone()
    assert db.execute("SELECT name FROM sqlite_master WHERE name='feature_report_artifacts'").fetchone()
    migrate(db)
    assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 1
