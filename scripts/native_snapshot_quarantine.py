"""Explicit OFFLINE forensic quarantine for an unregistered native snapshot.

Never deletes data, never adopts unknown Parquet as a registered asset, and
never runs automatically. Stop API and all workers first, back up the home,
run read-only native_snapshot_audit.py, then supply the exact file SHA256 and
case UUID. Quarantine remains outside the active native_bi inventory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.native_bi_recovery import INTENT, read_intent
from timepilot.project_migrations import catalog_startup_lock


def _sha(path: Path):
    sha = hashlib.sha256()
    with path.open('rb') as reader:
        for part in iter(lambda: reader.read(1024 * 1024), b''):
            sha.update(part)
    return sha.hexdigest()


def _assert_unregistered(metadata: Path, asset_id: str):
    if metadata.is_symlink() or not metadata.is_file():
        raise RuntimeError('Existing local metadata.sqlite3 is required; no new DB will be created')
    uri = 'file:' + quote(str(metadata), safe='/:') + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=10) as db:
        good = db.execute('PRAGMA quick_check(1)').fetchone()
        if not good or good[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed')
        row = db.execute('SELECT 1 FROM native_bi_assets WHERE id=?', (asset_id,)).fetchone()
        if row:
            raise RuntimeError('Snapshot is registered in metadata; cannot quarantine')


def _assert_no_active_worker_leases(home: Path) -> None:
    registry = home / 'operations' / 'worker_status.sqlite3'
    if not registry.exists():
        return
    if registry.is_symlink() or not registry.is_file():
        raise RuntimeError('Worker registry path is invalid')
    uri = 'file:' + quote(str(registry), safe='/:') + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True, timeout=6) as db:
            active = db.execute("SELECT COUNT(*) FROM role_lease WHERE owner<>'' AND lease_until>?",
                                (time.time(),)).fetchone()[0]
            if active:
                raise RuntimeError(f'{active} worker roles still hold valid leases; stop workers first')
    except sqlite3.DatabaseError as exc:
        raise RuntimeError('Cannot verify worker leases; no quarantine is permitted') from exc


def quarantine(home: Path, asset_id: str, expected_sha256: str,
               case_id: str, reason: str, *, services_stopped: bool,
               allow_unknown_intent: bool = False) -> dict:
    if not services_stopped:
        raise RuntimeError('API and all workers must be stopped; explicit offline confirmation is required')
    if str(UUID(asset_id)) != asset_id or str(UUID(case_id)) != case_id:
        raise ValueError('asset_id and case_id must be canonical UUIDs')
    if len(expected_sha256) != 64 or any(ch not in '0123456789abcdef' for ch in expected_sha256):
        raise ValueError('expected_sha256 must be the confirmed lowercase SHA256')
    reason = reason.strip()
    if len(reason) < 16 or len(reason) > 300:
        raise ValueError('A 16-300 character investigation reason is required')
    home = Path(home).expanduser().resolve(strict=True)
    parent = home / 'native_bi'
    # Reject redirection of either source or destination through a symlink.
    # The application is single-host; a cooperating writer must also honor
    # the maintenance lock while the operator is reconciling assets.
    if parent.is_symlink() or not parent.is_dir():
        raise RuntimeError('Native BI root is missing or redirected through a symlink')
    quarantine_root = home / 'native_bi_quarantine'
    if quarantine_root.is_symlink() or (quarantine_root.exists() and not quarantine_root.is_dir()):
        raise RuntimeError('Quarantine root is unsafe or redirected through a symlink')
    candidate = parent / asset_id
    data = candidate / 'data.parquet'
    metadata = home / 'metadata.sqlite3'
    if candidate.is_symlink() or data.is_symlink() or not candidate.is_dir() or not data.is_file():
        raise RuntimeError('Unregistered physical snapshot is absent, unsafe, or a symlink')
    destination = home / 'native_bi_quarantine' / case_id / asset_id
    with catalog_startup_lock(parent / '.native-recovery-maintenance.lock', timeout_seconds=12):
        if parent.is_symlink() or quarantine_root.is_symlink():
            raise RuntimeError('Native BI or quarantine root changed during maintenance')
        _assert_no_active_worker_leases(home)
        _assert_unregistered(metadata, asset_id)
        if destination.exists():
            raise RuntimeError('Case already contains this asset; manual reconciliation required')
        intent = read_intent(candidate)
        if (intent is None or intent['state'] != 'snapshot_ready') and not allow_unknown_intent:
            raise RuntimeError('Snapshot intent is absent/incomplete; independent review required (--allow-unknown-intent)')
        before = data.stat()
        actual = _sha(data)
        after = data.stat()
        signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if signature(before) != signature(after) or actual != expected_sha256:
            raise RuntimeError('Snapshot changed or SHA256 differs; refusing quarantine')
        if intent and intent.get('state') == 'snapshot_ready' and \
                (intent['snapshot']['sha256'] != actual or intent['snapshot']['file_bytes'] != before.st_size):
            raise RuntimeError('Snapshot contradicts recovery intent; independent investigation required')
        _assert_no_active_worker_leases(home)
        _assert_unregistered(metadata, asset_id)
        receipt = {
            'format': 1, 'case_id': case_id, 'asset_id': asset_id, 'sha256': actual,
            'file_bytes': before.st_size, 'reason': reason,
            'recovery_intent_verified': bool(intent and intent.get('state') == 'snapshot_ready'),
            'operator_confirmed_services_stopped': True,
            'utc': datetime.now(timezone.utc).isoformat(),
        }
        # Prepare an evidence receipt before moving the whole immutable folder:
        # a crash after the move still leaves its original evidence intact.
        preparing = candidate / 'quarantine-receipt.json'
        with preparing.open('x', encoding='utf-8') as writer:
            json.dump(receipt, writer, ensure_ascii=False, sort_keys=True, indent=2)
            writer.write('\n')
            writer.flush()
            os.fsync(writer.fileno())
        if data.is_symlink() or candidate.is_symlink() or parent.is_symlink() or quarantine_root.is_symlink():
            raise RuntimeError('Snapshot path changed during maintenance')
        final_stat = data.stat()
        if signature(final_stat) != signature(after):
            raise RuntimeError('Snapshot changed after hashing; refusing quarantine')
        destination.parent.mkdir(parents=True, exist_ok=False)
        os.rename(candidate, destination)  # Same filesystem, one atomic rename.
        if os.name == 'posix':
            for folder in (parent, destination.parent):
                descriptor = os.open(str(folder), os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        receipt['quarantine_path'] = str(destination)
        receipt['status'] = 'quarantined_preserved_not_deleted'
        return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--asset-id', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--confirm-services-stopped', action='store_true')
    parser.add_argument('--allow-unknown-intent', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = quarantine(args.home, args.asset_id, args.expected_sha256,
                            args.case_id, args.reason,
                            services_stopped=args.confirm_services_stopped,
                            allow_unknown_intent=args.allow_unknown_intent)
        code = 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        result = {'status': 'blocked', 'reason': type(exc).__name__,
                  'details': str(exc)[:240], 'data_deleted': False}
        code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
