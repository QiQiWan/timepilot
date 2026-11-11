"""Automatic, idempotent SQLite snapshots before a release may add tables.

Run by TIMEPILOT.sh before doctor and before constructing app services. Never
mutates original databases. Existing raw CSV/Parquet is never rewritten.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.runtime_backup import digest_file,RuntimeBackupError
from timepilot.build_lock import exclusive_build_lock


def prepare(home:Path,version:str):
    home=home.expanduser().resolve()
    for part in version.split('.'):
        if not part.isdigit():raise RuntimeBackupError('版本号格式错误')
    backup_dir=home/'.schema_backups'/('before_'+version.replace('.','_'))
    manifest=backup_dir/'snapshot.json'
    if manifest.is_file():
        doc=json.loads(manifest.read_text(encoding='utf-8'))
        for name,sha in doc.get('files',{}).items():
            file=backup_dir/name
            if not file.is_file() or digest_file(file)!=sha:
                raise RuntimeBackupError('数据库升级前快照缺失或被修改：'+name)
        return {'prepared':True,'reused':True,'databases':len(doc['files']),'path':str(backup_dir)}
    home.mkdir(parents=True,exist_ok=True)
    backup_dir.mkdir(parents=True,exist_ok=True)
    sources={'metadata.sqlite3':home/'metadata.sqlite3',
             'projects/catalog.sqlite3':home/'projects'/'catalog.sqlite3'}
    recorded={}
    for name,source in sources.items():
        if not source.is_file():continue
        with sqlite3.connect(f'file:{source}?mode=ro',uri=True,timeout=30) as original:
            if original.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeBackupError('源数据库未通过完整性校验，已停止升级：'+name)
            out=backup_dir/name
            out.parent.mkdir(parents=True,exist_ok=True)
            temp=out.with_suffix(out.suffix+'.staging')
            try:
                with sqlite3.connect(temp) as replica:
                    original.backup(replica,pages=1000,sleep=.05)
                    if replica.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                        raise RuntimeBackupError('数据库备份完整性检查失败：'+name)
                os.replace(temp,out)
            finally:temp.unlink(missing_ok=True)
        recorded[name]=digest_file(out)
    payload={'version':version,'created_at':datetime.now(timezone.utc).isoformat(),'files':recorded,
             'note':'Pre-release SQLite snapshot. Raw files remain in the original runtime.'}
    temp=manifest.with_suffix('.staging')
    temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,manifest)
    return {'prepared':True,'reused':False,'databases':len(recorded),'path':str(backup_dir)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--home',default=os.environ.get('TIMEPILOT_HOME',str(ROOT/'runtime')))
    a=p.parse_args()
    import tomllib
    version=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    home=Path(a.home)
    with exclusive_build_lock(home/'.schema_backups'/'.lock',timeout=120):
        result=prepare(home,version)
    print('[DB SNAPSHOT] '+json.dumps(result,ensure_ascii=False),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
