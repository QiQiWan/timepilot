"""v0.26.1: fresh installs and non-destructive project catalog upgrades."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from timepilot.project_migrations import (
    TARGET_SCHEMA, CatalogSchemaError, inspect_catalog,
)
from timepilot.projects import ProjectCatalog


def catalog_at(tmp_path):
    return tmp_path / 'projects' / 'catalog.sqlite3'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fresh_bootstrap_then_reopen_is_idempotent(tmp_path):
    path = catalog_at(tmp_path)
    assert inspect_catalog(path)['status'] == 'new'
    first = ProjectCatalog(tmp_path, None, None)
    assert first.startup_catalog['status'] == 'new'
    assert inspect_catalog(path)['current_version'] == TARGET_SCHEMA
    second = ProjectCatalog(tmp_path, None, None)
    assert second.startup_catalog['status'] == 'current'
    assert second.upgrade_backup is None
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM project_schema').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_empty_sqlite_file_is_initialized(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    path.touch()
    assert inspect_catalog(path)['status'] == 'empty'
    ProjectCatalog(tmp_path, None, None)
    assert inspect_catalog(path)['status'] == 'current'


def test_real_v4_catalog_preserves_project_and_creates_wal_safe_backup(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        db.execute('INSERT INTO project_schema VALUES (4)')
        db.execute('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,
            description TEXT NOT NULL,domain TEXT NOT NULL,status TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        db.execute('''INSERT INTO projects VALUES
            ('old-project','preserved','description','general','active','t','t')''')
    assert inspect_catalog(path)['status'] == 'upgrade'
    svc = ProjectCatalog(tmp_path, None, None)
    backup = svc.upgrade_backup
    assert backup and backup.exists()
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT version FROM project_schema').fetchone()[0] == 4
        assert db.execute('SELECT name FROM projects').fetchone()[0] == 'preserved'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA
        assert db.execute('SELECT name FROM projects').fetchone()[0] == 'preserved'
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert ProjectCatalog(tmp_path, None, None).upgrade_backup is None


def test_future_version_aborts_before_altering_any_table(tmp_path):
    path = catalog_at(tmp_path)
    ProjectCatalog(tmp_path, None, None)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE project_schema SET version=?', (TARGET_SCHEMA + 1,))
    before = sha(path)
    assert inspect_catalog(path)['status'] == 'newer'
    with pytest.raises(CatalogSchemaError, match='newer'):
        ProjectCatalog(tmp_path, None, None)
    assert sha(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA + 1


def test_unrecognized_database_is_not_auto_reset(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE customer_ledger(id INTEGER)')
        db.execute('INSERT INTO customer_ledger VALUES (5)')
    before = sha(path)
    assert inspect_catalog(path)['status'] == 'unrecognized'
    with pytest.raises(CatalogSchemaError):
        ProjectCatalog(tmp_path, None, None)
    assert sha(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id FROM customer_ledger').fetchone()[0] == 5


def test_empty_schema_ledger_with_existing_projects_is_not_inferred(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
    assert inspect_catalog(path)['status'] == 'unrecognized'
    with pytest.raises(CatalogSchemaError):
        ProjectCatalog(tmp_path, None, None)


def test_invalid_bytes_are_not_overwritten(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'not-a-sqlite-database')
    assert inspect_catalog(path)['status'] == 'corrupt'
    with pytest.raises(CatalogSchemaError):
        ProjectCatalog(tmp_path, None, None)
    assert path.read_bytes() == b'not-a-sqlite-database'


def test_concurrent_fresh_init_is_safe(tmp_path):
    with ThreadPoolExecutor(max_workers=3) as executor:
        completed = list(executor.map(lambda _: ProjectCatalog(tmp_path, None, None), range(3)))
    assert all(x.startup_catalog_after['status'] == 'current' for x in completed)
    with sqlite3.connect(catalog_at(tmp_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM project_schema').fetchone()[0] == 1


def test_database_diagnostics_does_not_create_missing_catalog(tmp_path):
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, str(root / 'scripts/inspect_catalog.py'),
                           '--home', str(tmp_path), '--json'], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data['status'] == 'new'
    assert not catalog_at(tmp_path).exists()


def test_actual_api_can_initialize_twice_without_schema_error(tmp_path):
    from timepilot.api import create_app
    first = create_app(home=tmp_path, embedded_worker=False)
    second = create_app(home=tmp_path, embedded_worker=False)
    assert first.state.projects.startup_catalog_after['status'] == 'current'
    assert second.state.projects.startup_catalog['status'] == 'current'


def test_failed_migration_uses_atomic_ddl_and_retains_previous_version(tmp_path, monkeypatch):
    from timepilot import project_migrations
    path = catalog_at(tmp_path)
    ProjectCatalog(tmp_path, None, None)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE project_schema SET version=?', (TARGET_SCHEMA - 1,))
        db.execute('DROP TABLE feature_execution_receipts')
    original = project_migrations.MIGRATIONS[TARGET_SCHEMA]
    monkeypatch.setitem(project_migrations.MIGRATIONS, TARGET_SCHEMA,
                        'CREATE TABLE temp_failed_upgrade(id INTEGER); INVALID SQL FOR ROLLBACK;')
    with pytest.raises(sqlite3.OperationalError):
        ProjectCatalog(tmp_path, None, None)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA - 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='temp_failed_upgrade'").fetchone()
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    monkeypatch.setitem(project_migrations.MIGRATIONS, TARGET_SCHEMA, original)
    assert ProjectCatalog(tmp_path, None, None).startup_catalog_after['status'] == 'current'


def test_release_home_priority_preserves_legacy_then_uses_stable_path(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from launcher import choose_data_home
    root = tmp_path / 'timepilot-source'
    root.mkdir()
    monkeypatch.setenv('HOME', str(tmp_path / 'user-home'))
    home, source = choose_data_home(root)
    assert home == tmp_path / 'user-home' / '.timepilot'
    assert 'stable' in source
    (root / 'runtime' / 'projects').mkdir(parents=True)
    (root / 'runtime' / 'projects' / 'catalog.sqlite3').write_bytes(b'legacy-marker')
    home, source = choose_data_home(root)
    assert home == root / 'runtime'
    assert 'compatibility' in source
    explicit = tmp_path / 'external'
    home, source = choose_data_home(root, configured=str(explicit))
    assert home == explicit
    assert 'explicit' in source


@pytest.mark.parametrize('legacy_version', [1, 2, 3])
def test_legacy_very_old_versions_are_not_rejected(tmp_path, legacy_version):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        db.execute('INSERT INTO project_schema VALUES (?)', (legacy_version,))
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
    ProjectCatalog(tmp_path, None, None)
    assert inspect_catalog(path)['status'] == 'current'


def test_upgrade_backup_captures_committed_wal_rows(tmp_path):
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        connection.execute('INSERT INTO project_schema VALUES (4)')
        connection.execute('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          description TEXT NOT NULL,domain TEXT NOT NULL,status TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        connection.commit()
        connection.execute("INSERT INTO projects VALUES ('wal-row','committed','d','g','active','t','t')")
        connection.commit()
        service = ProjectCatalog(tmp_path, None, None)
        assert service.upgrade_backup is not None
        with sqlite3.connect(service.upgrade_backup) as backup:
            assert backup.execute("SELECT name FROM projects WHERE id='wal-row'").fetchone() == ('committed',)
    finally:
        connection.close()


def test_direct_app_rejects_future_project_catalog_before_metadata_created(tmp_path):
    from timepilot.api import create_app
    path = catalog_at(tmp_path)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE project_schema(version INTEGER NOT NULL)')
        db.execute('INSERT INTO project_schema VALUES (?)', (TARGET_SCHEMA + 1,))
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
    with pytest.raises(CatalogSchemaError, match='newer'):
        create_app(home=tmp_path, embedded_worker=False)
    assert not (tmp_path / 'metadata.sqlite3').exists()


def test_unicode_space_and_uri_special_characters_in_runtime_path(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from preflight_schema import prepare
    home = tmp_path / ('OneDrive-' + '\u4e2a\u4eba #test & data')
    ProjectCatalog(home, None, None)
    report = prepare(home, '0.26.1')
    assert report['prepared'] and report['databases'] >= 1
    assert (home / '.schema_backups' / 'before_0_26_1' / 'projects' / 'catalog.sqlite3').exists()
    assert inspect_catalog(home / 'projects' / 'catalog.sqlite3')['status'] == 'current'
