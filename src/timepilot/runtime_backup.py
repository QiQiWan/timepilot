"""Consistent SQLite + immutable-file backup for local TimePilot runtime.

Backups deliberately require an explicit user command: never rewrite, move,
or garbage-collect the original runtime automatically. Service should be stopped
for a stable snapshot of non-SQLite files. Verification is path-safe/read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class RuntimeBackupError(ValueError):
    pass


def digest_file(path: Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def _safe_paths(home: Path):
    skip_names={'native-bi-build.lock','node_modules','.DS_Store'}
    for base,dirs,files in os.walk(home,followlinks=False):
        dirs[:]=[d for d in dirs if d not in ('__pycache__','.git','node_modules') and
                 not (Path(base)/d).is_symlink()]
        for name in files:
            path=Path(base)/name
            if (name in skip_names or name.endswith(('-wal','-shm','.incomplete','.tmp')) or
                path.is_symlink()):continue
            yield path.relative_to(home).as_posix(),path


def _db_check(path: Path):
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as db:
        checked=db.execute('PRAGMA integrity_check').fetchone()[0]
    if checked!='ok':raise RuntimeBackupError('SQLite integrity_check 失败：'+str(path))


def create_backup(home: Path,output: Path) -> dict:
    home=Path(home).expanduser().resolve();output=Path(output).expanduser().resolve()
    if not home.is_dir():raise RuntimeBackupError('运行数据目录不存在')
    if home==output or output.is_relative_to(home):
        raise RuntimeBackupError('备份文件必须放在运行数据目录之外，以免递归备份')
    if output.exists() or output.is_symlink():
        raise RuntimeBackupError('备份目标文件已存在，拒绝覆盖已有灾备文件')
    items=list(_safe_paths(home))
    if not items:raise RuntimeBackupError('运行数据目录中没有可备份文件')
    output.parent.mkdir(parents=True,exist_ok=True)
    handle,temporary=tempfile.mkstemp(prefix='.timepilot-backup-',suffix='.zip',dir=output.parent)
    os.close(handle)
    entries={};sql_count=0
    try:
        with tempfile.TemporaryDirectory(prefix='timepilot-db-backup-') as temp:
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,
                                 allowZip64=True) as target:
                for name,source in items:
                    if source.suffix.lower() in ('.sqlite3','.sqlite','.db'):
                        _db_check(source)
                        snapshot=Path(temp)/f'snapshot-{sql_count}.sqlite3'
                        with sqlite3.connect(str(source)) as original,sqlite3.connect(str(snapshot)) as replica:
                            original.backup(replica,pages=500,sleep=0.05)
                        _db_check(snapshot)
                        target.write(snapshot,'data/'+name)
                        entries[name]={'bytes':snapshot.stat().st_size,'sha256':digest_file(snapshot),'sqlite_snapshot':True}
                        sql_count+=1
                    else:
                        before=digest_file(source)
                        target.write(source,'data/'+name)
                        after=digest_file(source)
                        if before!=after:raise RuntimeBackupError('备份期间文件发生变化，请停止服务后重新备份：'+name)
                        entries[name]={'bytes':source.stat().st_size,'sha256':after,'sqlite_snapshot':False}
                manifest={'format':1,'created_at':datetime.now(timezone.utc).isoformat(),
                          'source_kind':'local_timepilot_runtime','files':entries,'sqlite_count':sql_count,
                          'recommended_restore':'verify and restore to a new empty runtime directory while server is stopped'}
                target.writestr('BACKUP_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        check=verify_backup(Path(temporary))
        if not check['ok']:raise RuntimeBackupError('备份包校验失败')
        if output.exists() or output.is_symlink():
            raise RuntimeBackupError('备份完成前目标文件已被其他进程创建，拒绝覆盖')
        os.rename(temporary,output)
        return {'archive':str(output),'files':len(entries),'sqlite_snapshots':sql_count,
                'bytes':output.stat().st_size,'sha256':digest_file(output),'verified':True}
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def verify_backup(archive: Path) -> dict:
    archive=Path(archive).expanduser().resolve()
    with zipfile.ZipFile(archive,'r') as source:
        names=source.namelist()
        if not names or source.testzip() is not None:raise RuntimeBackupError('备份压缩包损坏')
        manifest=json.loads(source.read('BACKUP_MANIFEST.json'))
        if manifest.get('format')!=1 or not isinstance(manifest.get('files'),dict):
            raise RuntimeBackupError('备份清单格式无效')
        expected={'data/'+name for name in manifest['files']}|{'BACKUP_MANIFEST.json'}
        if len(names)!=len(set(names)) or set(names)!=expected:
            raise RuntimeBackupError('备份内容与清单不一致')
        for name,item in manifest['files'].items():
            _checked_backup_member(name)
            if not isinstance(item, dict) or type(item.get('bytes')) is not int or item['bytes'] < 0 or not isinstance(item.get('sha256'), str):
                raise RuntimeBackupError('备份元数据格式无效：'+name)
            info=source.getinfo('data/'+name)
            if info.is_dir() or (info.create_system==3 and (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeBackupError('不允许从符号链接或目录 ZIP 条目恢复：'+name)
            if info.file_size != item['bytes']:
                raise RuntimeBackupError('备份 ZIP 文件长度与清单不一致：'+name)
            h=hashlib.sha256();count=0
            with source.open('data/'+name) as f:
                for block in iter(lambda:f.read(1024*1024),b''):
                    h.update(block);count+=len(block)
            if count!=item['bytes'] or h.hexdigest()!=item['sha256']:
                raise RuntimeBackupError('备份内容校验失败：'+name)
        return {'ok':True,'files':len(manifest['files']),
                'sqlite_snapshots':manifest['sqlite_count'],'created_at':manifest['created_at']}


def _checked_backup_member(name: str) -> str:
    """Keep Windows and POSIX paths within a new, empty recovery directory."""
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or '\x00' in name or any(ord(ch) < 32 for ch in name)):
        raise RuntimeBackupError('备份存在不安全的文件名')
    if name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/')):
        raise RuntimeBackupError('备份存在路径遍历或非标准路径')
    return name


def restore_backup(archive: Path, destination: Path) -> dict:
    """Verify and stage a restore; never touch an existing runtime directory.

    The caller must stop the service and supply a *new* destination. The final
    rename occurs only after hashes and SQLite integrity checks pass. Existing
    data, including TIMEPILOT_HOME, are never overwritten by this function.
    """
    import shutil
    import time

    started = time.monotonic()
    archive = Path(archive).expanduser().resolve(strict=True)
    supplied = Path(destination).expanduser().absolute()
    if supplied.is_symlink():
        raise RuntimeBackupError('恢复目标为符号链接，拒绝操作')
    destination = supplied.resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise RuntimeBackupError('恢复目标必须为不存在的新目录，禁止覆盖已有运行数据')
    if destination == destination.parent or not destination.parent.is_dir():
        raise RuntimeBackupError('恢复目标的父目录必须已存在')
    # A backup inside the target cannot occur because target must not exist.
    archive_sha = digest_file(archive)
    snapshot = verify_backup(archive)
    staging = Path(tempfile.mkdtemp(prefix='.tp-restore-', dir=destination.parent))
    entries = []
    try:
        with zipfile.ZipFile(archive, 'r') as source:
            manifest = json.loads(source.read('BACKUP_MANIFEST.json'))
            files = manifest['files']
            for name, meta in sorted(files.items()):
                _checked_backup_member(name)
                target = staging.joinpath(*name.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    raise RuntimeBackupError('恢复目标中出现重复文件：' + name)
                sha = hashlib.sha256()
                written = 0
                with source.open('data/' + name) as reader, target.open('xb') as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        writer.write(block)
                        sha.update(block)
                        written += len(block)
                    writer.flush()
                    os.fsync(writer.fileno())
                if written != meta['bytes'] or sha.hexdigest() != meta['sha256']:
                    raise RuntimeBackupError('恢复文件哈希不一致：' + name)
                if meta.get('sqlite_snapshot'):
                    _db_check(target)
                    with sqlite3.connect(target.resolve().as_uri()+'?mode=ro', uri=True) as db:
                        tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                    entries.append({'path': name, 'sqlite_tables': tables})
        # The archive itself must remain byte-identical throughout recovery.
        if digest_file(archive) != archive_sha:
            raise RuntimeBackupError('恢复期间备份压缩包被修改，已拒绝提交')
        # Keep the destination name unoccupied until all restore checks succeed.
        if destination.exists() or destination.is_symlink():
            raise RuntimeBackupError('恢复期间目标路径被其他进程占用，拒绝替换')
        # POSIX rename and Windows rename both reject existing populated trees.
        staging.rename(destination)
        return {'status': 'restored_to_new_directory', 'destination': str(destination),
                'source_archive_sha256': archive_sha, 'verified_files': snapshot['files'],
                'sqlite_snapshots': snapshot['sqlite_snapshots'],
                'sqlite_table_inventory': entries, 'elapsed_seconds': round(time.monotonic()-started, 3),
                'original_runtime_untouched': True}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
