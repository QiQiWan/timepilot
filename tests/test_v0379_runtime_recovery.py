"""v0.37.9: fail-closed disaster-recovery operations, without real user data."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from timepilot.runtime_backup import (
    RuntimeBackupError, create_backup, restore_backup, verify_backup,
)


def fixture_runtime(tmp_path):
    home = tmp_path / 'original'
    home.mkdir()
    (home / 'raw').mkdir()
    (home / 'raw' / 'sample.parquet').write_bytes(b'PAR1fake-test-byte-content')
    with sqlite3.connect(home / 'metadata.db') as db:
        db.execute('create table products (id integer primary key, quantity integer)')
        db.executemany('insert into products(quantity) values (?)', [(10,), (20,)])
    return home


def test_restore_to_new_path_preserves_original_and_sqlite_integrity(tmp_path):
    home = fixture_runtime(tmp_path)
    old = {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in home.rglob('*') if p.is_file()}
    backup = tmp_path / 'snap.zip'
    created = create_backup(home, backup)
    assert created['verified']
    assert verify_backup(backup)['files'] == 2
    recovered = tmp_path / 'recovered'
    result = restore_backup(backup, recovered)
    assert result['status'] == 'restored_to_new_directory'
    assert result['sqlite_table_inventory'][0]['sqlite_tables'] == 1
    with sqlite3.connect(recovered / 'metadata.db') as db:
        assert db.execute('select sum(quantity) from products').fetchone()[0] == 30
        assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
    assert (recovered / 'raw' / 'sample.parquet').read_bytes() == (home / 'raw' / 'sample.parquet').read_bytes()
    assert old == {p.relative_to(home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in home.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeBackupError, match='禁止覆盖'):
        restore_backup(backup, home)
    with pytest.raises(RuntimeBackupError, match='禁止覆盖'):
        restore_backup(backup, recovered)


def test_failed_restore_leaves_no_partial_destination(tmp_path):
    home = fixture_runtime(tmp_path)
    backup = tmp_path / 'snap.zip'
    create_backup(home, backup)
    bad = tmp_path / 'modified.zip'
    with zipfile.ZipFile(backup) as original, zipfile.ZipFile(bad, 'w') as changed:
        for name in original.namelist():
            content = original.read(name)
            if name.endswith('sample.parquet'):
                content = b'corrupted-file'
            changed.writestr(name, content)
    dest = tmp_path / 'recovered'
    with pytest.raises(RuntimeBackupError, match='长度|校验'):
        restore_backup(bad, dest)
    assert not dest.exists()
    assert not list(tmp_path.glob('.tp-restore-*'))


@pytest.mark.parametrize('escape_name', ['../escape', '/absolute', 'a/../../b', 'a\\b', 'C:/evil', 'a//b', 'a/./b'])
def test_restore_blocks_unsafe_archive_paths(tmp_path, escape_name):
    backup = tmp_path / 'malicious.zip'
    payload = b'test'
    meta = {'format': 1, 'created_at': '2026-11-17T00:00:00Z', 'sqlite_count': 0,
            'files': {escape_name: {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
                                    'sqlite_snapshot': False}}}
    with zipfile.ZipFile(backup, 'w') as z:
        z.writestr('BACKUP_MANIFEST.json', json.dumps(meta))
        z.writestr('data/' + escape_name, payload)
    with pytest.raises(RuntimeBackupError):
        restore_backup(backup, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()
    assert not (tmp_path.parent / 'escape').exists()


def test_backup_rejects_archive_inside_runtime(tmp_path):
    home = fixture_runtime(tmp_path)
    with pytest.raises(RuntimeBackupError):
        create_backup(home, home / 'archive.zip')


def test_backup_never_replaces_prior_archive(tmp_path):
    home = fixture_runtime(tmp_path)
    output = tmp_path / 'snapshot.zip'
    output.write_bytes(b'prior-important-backup')
    with pytest.raises(RuntimeBackupError, match='拒绝覆盖'):
        create_backup(home, output)
    assert output.read_bytes() == b'prior-important-backup'


def test_sqlite_backup_with_reserved_uri_characters(tmp_path):
    home = fixture_runtime(tmp_path)
    database = home / 'metadata.db'
    database.rename(home / 'metadata #?.db')
    output = tmp_path / 'snapshot.zip'
    created = create_backup(home, output)
    assert created['sqlite_snapshots'] == 1
    restored = tmp_path / 'restored'
    result = restore_backup(output, restored)
    assert result['sqlite_snapshots'] == 1
    with sqlite3.connect(restored / 'metadata #?.db') as db:
        assert db.execute('select count(*) from products').fetchone()[0] == 2
