"""Read-only project catalog status and recovery hints (no schema writes)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.project_migrations import inspect_catalog
from timepilot.db import check_existing_sqlite_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', default=os.getenv('TIMEPILOT_HOME') or str(Path.home() / '.timepilot'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    home = Path(args.home).expanduser().resolve()
    report = inspect_catalog(home / 'projects' / 'catalog.sqlite3')
    report['timepilot_home'] = str(home)
    db_url = os.getenv('TIMEPILOT_DB_URL') or f"sqlite:///{(home / 'metadata.sqlite3').as_posix()}"
    try:
        report['metadata'] = check_existing_sqlite_metadata(db_url)
    except (RuntimeError, OSError) as exc:
        report['metadata'] = {'status': 'blocked', 'reason': str(exc)}
    report['metadata_path'] = report['metadata'].get('path') or (
        'configured external database' if os.getenv('TIMEPILOT_DB_URL') else str(home / 'metadata.sqlite3'))
    report['backup_dir'] = str(home / '.schema_backups')
    report['synchronized_folder_warning'] = any(
        part in str(home).lower() for part in ('onedrive', 'cloudstorage', 'dropbox', 'icloud'))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('[DB CHECK]', report['status'], '| schema', report['current_version'],
              '->', report['target_version'])
        print('TIMEPILOT_HOME:', report['timepilot_home'])
        print('Catalog:', report['path'])
        print('Metadata:', report['metadata_path'], '|', report['metadata']['status'])
        if report['metadata']['status'] == 'blocked':
            print('[BLOCKED] Metadata:', report['metadata']['reason'])
        print('Detail:', report['reason'])
        if report['synchronized_folder_warning']:
            print('[WARN] Active SQLite storage is in a cloud-synced directory. '
                  'Stop TimePilot, back up and relocate the runtime to a local directory.')
        if report['status'] == 'new':
            print('[NEXT] Start TimePilot normally; both database files are created automatically.')
        elif report['status'] == 'upgrade':
            print('[NEXT] Start TimePilot; the existing catalog is backed up and migrated in-place.')
        elif report['status'] not in {'current', 'empty'}:
            print('[BLOCKED] No automatic reset. Keep the file and use an appropriate release or verified backup.')
    return 0 if (report['status'] in {'new', 'empty', 'current', 'upgrade'} and report['metadata']['status'] != 'blocked') else 2


if __name__ == '__main__':
    raise SystemExit(main())
