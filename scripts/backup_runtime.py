"""Explicit, verified local backup / empty-directory recovery entrypoint.

Original TIMEPILOT_HOME remains untouched. No restore during app startup.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import zipfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.runtime_backup import create_backup, restore_backup, verify_backup, RuntimeBackupError


def default_home() -> str:
    configured = os.environ.get('TIMEPILOT_HOME')
    if configured:
        return configured
    legacy = ROOT / 'runtime'
    if legacy.is_dir() and any(legacy.iterdir()):
        return str(legacy)
    return str(Path.home() / '.timepilot')


def main() -> int:
    p = argparse.ArgumentParser(description='TimePilot runtime backup, verify and safe restore')
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path, help='Create a ZIP backup outside the runtime')
    mode.add_argument('--verify', type=Path, help='Read-only validation of an existing ZIP')
    mode.add_argument('--restore', type=Path, help='Restore an existing ZIP to a NEW empty path')
    p.add_argument('--home', default=default_home())
    p.add_argument('--destination', type=Path, help='Required for --restore; must not exist')
    args = p.parse_args()
    try:
        if args.verify:
            report = verify_backup(args.verify)
        elif args.restore:
            if not args.destination:
                p.error('--restore requires --destination (new directory)')
            home = Path(args.home).expanduser().resolve()
            supplied = args.destination.expanduser().absolute()
            if supplied.is_symlink():
                raise RuntimeBackupError('恢复目标是符号链接，拒绝操作')
            dest = supplied.resolve(strict=False)
            if dest == home or home.is_relative_to(dest) or dest.is_relative_to(home):
                raise RuntimeBackupError('恢复目标不得与现有 TIMEPILOT_HOME 相同或嵌套')
            report = restore_backup(args.restore, dest)
        else:
            print('[BACKUP] Stop the service for a stable non-SQLite snapshot.', flush=True)
            report = create_backup(Path(args.home), args.output)
    except (RuntimeBackupError, OSError, ValueError, KeyError, json.JSONDecodeError,
            zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
        print('[BACKUP BLOCKED] ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
