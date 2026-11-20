"""Read-only forensic inventory of native BI crash leftovers.

Works on the local single-host SQLite deployment, without DuckDB. Never
modifies the database or existing snapshots and never concludes that a
missing/invalid intent means a snapshot is safe to delete or re-publish.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.native_bi_recovery import scan_orphan_snapshots, audit_registered_snapshots


def inventory(home: Path, *, expected: dict | None = None,
              full_hash: bool = False, limit: int = 100_000,
              verify_registered: bool = False,
              max_hash_bytes: int = 4 * 1024 ** 3) -> dict:
    home = home.expanduser().resolve(strict=True)
    metadata = home / 'metadata.sqlite3'
    if metadata.is_symlink() or not metadata.is_file():
        raise RuntimeError('Missing local metadata.sqlite3; this tool does not create a database')
    dburi = 'file:' + quote(str(metadata), safe='/:') + '?mode=ro'
    if limit < 1 or max_hash_bytes < 1:
        raise ValueError('Audit budgets must be positive')
    with sqlite3.connect(dburi, uri=True, timeout=5) as db:
        check = db.execute('PRAGMA quick_check(1)').fetchone()
        if not check or check[0] != 'ok':
            raise RuntimeError('Existing metadata SQLite integrity check failed')
        try:
            registered = {str(row[0]) for row in db.execute('SELECT id FROM native_bi_assets')}
        except sqlite3.Error as exc:
            raise RuntimeError('Native BI metadata table missing or inaccessible') from exc
        if verify_registered:
            rows = db.execute('SELECT id, sha256, file_bytes FROM native_bi_assets ORDER BY id LIMIT ?',
                              (limit + 1,)).fetchall()
        else:
            rows = None
    root = home / 'native_bi'
    if not root.is_dir():
        raise RuntimeError('Native BI snapshot directory does not exist')
    result = scan_orphan_snapshots(root, registered, expected=expected,
                                   max_scan=limit, include_evidence=full_hash,
                                   max_hash_bytes=max_hash_bytes)
    registered_integrity = None
    if verify_registered:
        registered_integrity = audit_registered_snapshots(
            root, rows, max_assets=limit,
            max_hash_bytes=max_hash_bytes - result['hashed_bytes'])
    return {'read_only': True, 'generated_utc': datetime.now(timezone.utc).isoformat(),
            'metadata_registered_assets': len(registered), 'scope': expected,
            'native_home': str(root), **result,
            'registered_integrity': registered_integrity,
            'total_hashed_bytes': result['hashed_bytes'] + (
                registered_integrity['hashed_bytes'] if registered_integrity else 0),
            'hash_budget_bytes': max_hash_bytes}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True, help='Existing TIMEPILOT_HOME')
    parser.add_argument('--project-id')
    parser.add_argument('--import-id')
    parser.add_argument('--candidate-id')
    parser.add_argument('--version-id')
    parser.add_argument('--full-hash', action='store_true', help='Read and hash unregistered snapshots (disk IO)')
    parser.add_argument('--max-scan', type=int, default=100_000)
    parser.add_argument('--verify-registered', action='store_true',
                        help='Independently hash registered asset files (bounded disk IO)')
    parser.add_argument('--max-hash-bytes', type=int, default=4 * 1024 ** 3,
                        help='Maximum bytes read when checking registered assets')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    values = (args.project_id, args.import_id, args.candidate_id, args.version_id)
    if any(x is not None for x in values) and not all(values):
        parser.error('All four source scope identifiers must be provided together')
    expected = dict(zip(('project_id','import_id','candidate_id','version_id'), values)) if all(values) else None
    try:
        result = inventory(args.home, expected=expected, full_hash=args.full_hash, limit=args.max_scan,
                           verify_registered=args.verify_registered, max_hash_bytes=args.max_hash_bytes)
        registered = result['registered_integrity']
        status = ('requires_review' if not result['complete'] or result['unregistered_snapshots'] or
                  (registered is not None and not registered['safe']) else 'scanned')
        report = {'status': status, **result}
        code = 0 if status == 'scanned' else 2  # 2 means unsafe to assert absence.
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        report = {'status': 'blocked', 'read_only': True, 'reason': type(exc).__name__,
                  'details': str(exc)[:240]}
        code = 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
