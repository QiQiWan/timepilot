"""Crash evidence for immutable native BI snapshots.

A sidecar is written before Parquet creation and advanced after the file is
atomically renamed, before SQL metadata is committed. It is a provenance hint,
never sufficient proof for automatic adoption or for bypassing physical checks.
This module can inspect a damaged store without DuckDB or SQLAlchemy.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

INTENT = 'publication-intent.json'
FORMAT_VERSION = 1
REGION_KEYS = ('project_id', 'import_id', 'candidate_id', 'version_id')
PROVENANCE_KEYS = (*REGION_KEYS, 'type', 'source_sha256', 'data_sha256', 'publication_key')


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as reader:
        for piece in iter(lambda: reader.read(1024 * 1024), b''):
            sha.update(piece)
    return sha.hexdigest()


def _atomic_json(path: Path, document: dict) -> None:
    temp = path.with_name('.intent-' + uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as writer:
            json.dump(document, writer, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            writer.write('\n')
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temp, path)
        # Directory fsync ensures rename durability on POSIX. Some Windows
        # filesystems disallow opening a directory descriptor; the file fsync
        # remains in force and subsequent recovery treats absence as uncertain.
        if os.name == 'posix':
            descriptor = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


def create_intent(folder: Path, asset_id: str, lineage: dict | None) -> None:
    folder = Path(folder)
    if folder.is_symlink() or str(UUID(asset_id)) != asset_id or folder.name != asset_id:
        raise ValueError('Native snapshot folder must be a canonical UUID directory')
    values = {key: lineage[key] for key in PROVENANCE_KEYS if lineage and key in lineage}
    if any(not isinstance(value, str) or not value or len(value) > 160 for value in values.values()):
        raise ValueError('Invalid native snapshot lineage identifier')
    if values.get('type') == 'project_generic_table_v016':
        if any(key not in values for key in REGION_KEYS):
            raise ValueError('Project snapshot requires complete source identifiers')
    record = {'format': FORMAT_VERSION, 'asset_id': asset_id, 'lineage': values,
              'state': 'preparing', 'created_utc': datetime.now(timezone.utc).isoformat()}
    _atomic_json(folder / INTENT, record)


def snapshot_ready(folder: Path, *, sha256: str, file_bytes: int, row_count: int) -> None:
    folder = Path(folder)
    document = read_intent(folder)
    if document is None or document['state'] != 'preparing':
        raise ValueError('Cannot finalize an unknown or already-finalized snapshot')
    if (not isinstance(sha256, str) or len(sha256) != 64 or
            any(letter not in '0123456789abcdef' for letter in sha256) or
            not isinstance(file_bytes, int) or file_bytes <= 0 or
            not isinstance(row_count, int) or row_count < 0):
        raise ValueError('Invalid snapshot evidence')
    data = folder / 'data.parquet'
    if data.is_symlink() or not data.is_file() or data.stat().st_size != file_bytes:
        raise ValueError('Snapshot file is missing or has a different size')
    document['state'] = 'snapshot_ready'
    document['snapshot'] = {'sha256': sha256, 'file_bytes': file_bytes, 'row_count': row_count}
    _atomic_json(folder / INTENT, document)


def read_intent(folder: Path) -> dict | None:
    path = Path(folder) / INTENT
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        return None
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('format') != FORMAT_VERSION:
            return None
        if record.get('asset_id') != folder.name or str(UUID(folder.name)) != folder.name:
            return None
        values = record.get('lineage')
        if not isinstance(values, dict) or record.get('state') not in {'preparing', 'snapshot_ready'}:
            return None
        if not values.get('type') or any(
                key not in PROVENANCE_KEYS or not isinstance(value, str) or
                not value or len(value) > 160 for key, value in values.items()):
            return None
        if values.get('type') == 'project_generic_table_v016' and any(
                key not in values for key in REGION_KEYS):
            return None
        if record['state'] == 'snapshot_ready':
            info = record.get('snapshot')
            if not isinstance(info, dict) or not isinstance(info.get('file_bytes'), int) or \
                    info['file_bytes'] <= 0 or not isinstance(info.get('row_count'), int) or \
                    info['row_count'] < 0 or not isinstance(info.get('sha256'), str) or \
                    len(info['sha256']) != 64 or any(c not in '0123456789abcdef' for c in info['sha256']):
                return None
        return record
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def scan_orphan_snapshots(root: Path, known_ids: set[str], *,
                          expected: dict | None = None, max_scan: int = 100_000,
                          include_evidence: bool = False,
                          max_hash_bytes: int = 4 * 1024 ** 3) -> dict:
    """Count unregistered Parquet snapshots without hiding unknown legacy files.

    Incomplete or malformed/missing intents remain globally ambiguous, so a
    different project's publication cannot treat them as proof of absence.
    Valid intents from unrelated regions are counted separately. The scope
    only decides which known-provenance orphan blocks this publication.
    """
    root = Path(root)
    target = other = ambiguous = builds = examined = visited = 0
    bytes_hashed = 0
    hash_budget_exhausted = False
    complete = True
    previews: list[dict] = []
    if max_scan < 1 or max_hash_bytes < 1:
        raise ValueError('scan and hash budgets must be positive')
    if root.is_symlink() or not root.is_dir():
        return {'complete': False, 'unregistered_snapshots': 1,
                'scoped_unregistered_snapshots': 0,
                'unregistered_other_regions': 0, 'ambiguous_orphans': 1,
                'invalid_orphans': 0, 'incomplete_builds': 0,
                'hashed_bytes': 0, 'hash_budget_exhausted': False,
                'filesystem_entries_examined': 0, 'filesystem_entries_scanned': 0,
                'orphan_previews': [{'scope':'ambiguous','reason':'unsafe_native_root'}],
                'source':'provenance_intents_and_filesystem'}
    invalid = 0
    try:
        for folder in root.iterdir():
            visited += 1
            if visited > max_scan:
                complete = False
                break
            if not folder.is_dir() and not folder.is_symlink():
                continue
            if folder.name in known_ids:
                continue
            # Other root subdirectories (tmp, CAS, etc.) are not snapshots.
            snapshot = folder / 'data.parquet'
            building = folder / 'data.building'
            if not snapshot.exists() and not building.exists():
                continue
            examined += 1
            if not snapshot.exists():
                builds += 1
                continue  # A partial .building is never a committed Parquet.
            record = None if folder.is_symlink() or snapshot.is_symlink() or not snapshot.is_file() else read_intent(folder)
            if record is None:
                ambiguous += 1
                complete = False
                if len(previews) < 25:
                    previews.append({'asset_id': folder.name[:64], 'scope': 'ambiguous',
                                     'reason': 'missing_or_invalid_intent'})
                continue
            # An intent is provenance evidence, not proof the file still has
            # the recorded bytes. Size changes are cheap to detect on every
            # scan; full-hash scans additionally reject silent same-size edits.
            integrity_issue = None
            actual_sha = None
            if record['state'] == 'snapshot_ready':
                info = record['snapshot']
                try:
                    before_stat = snapshot.stat()
                    if before_stat.st_size != info['file_bytes']:
                        integrity_issue = 'recorded_size_mismatch'
                    elif include_evidence and bytes_hashed + before_stat.st_size > max_hash_bytes:
                        integrity_issue = 'hash_budget_exceeded'
                        hash_budget_exhausted = True
                    elif include_evidence:
                        actual_sha = _digest(snapshot)
                        bytes_hashed += before_stat.st_size
                        after_stat = snapshot.stat()
                        signature = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
                        if signature(before_stat) != signature(after_stat):
                            integrity_issue = 'changed_during_hash'
                        elif actual_sha != info['sha256']:
                            integrity_issue = 'recorded_sha256_mismatch'
                except OSError:
                    integrity_issue = 'snapshot_unreadable'
            if integrity_issue:
                ambiguous += 1
                invalid += 1
                complete = False
                if len(previews) < 25:
                    previews.append({'asset_id': folder.name[:64], 'scope': 'ambiguous',
                                     'reason': integrity_issue,
                                     'hash_matches': False if include_evidence else None,
                                     'actual_sha256': actual_sha if include_evidence else None})
                continue
            lineage = record['lineage']
            related = expected is None or all(lineage.get(k) == v for k, v in expected.items())
            # An explicit unrecognized lineage cannot prove that a project
            # snapshot belongs elsewhere. Only supported non-project asset
            # classes are safely unrelated.
            unrelated = (lineage.get('type') in {'upload', 'file_import'} or
                         lineage.get('type') == 'project_generic_table_v016')
            if not unrelated:
                ambiguous += 1
                complete = False
                continue
            if related:
                target += 1
            else:
                other += 1
            if len(previews) < 25 and (related or include_evidence):
                detail = {'asset_id': folder.name, 'scope': 'target' if related else 'other',
                          'state': record['state'], 'file_bytes': snapshot.stat().st_size}
                if record['state'] == 'snapshot_ready':
                    info = record['snapshot']
                    detail['recorded_sha256'] = info['sha256']
                    detail['recorded_size_matches'] = (snapshot.stat().st_size == info['file_bytes'])
                    if include_evidence:
                        detail['actual_sha256'] = actual_sha
                        detail['hash_matches'] = actual_sha == info['sha256']
                previews.append(detail)
    except OSError:
        complete = False
    return {'complete': complete, 'unregistered_snapshots': target + ambiguous,
            'scoped_unregistered_snapshots': target,
            'unregistered_other_regions': other, 'ambiguous_orphans': ambiguous,
            'invalid_orphans': invalid, 'hashed_bytes': bytes_hashed,
            'hash_budget_exhausted': hash_budget_exhausted,
            'incomplete_builds': builds, 'filesystem_entries_examined': examined,
            'filesystem_entries_scanned': visited,
            'orphan_previews': previews, 'source': 'provenance_intents_and_filesystem'}


def audit_registered_snapshots(root: Path, rows, *, max_assets: int = 100_000,
                               max_hash_bytes: int = 4 * 1024 ** 3) -> dict:
    """Bounded, read-only verification of *registered* immutable file bytes.

    This does not parse Parquet, require DuckDB, or grant permission to repair
    data. A bad/missing/unstable asset always blocks the audit from asserting
    integrity; budget exhaustion is reported as incomplete. ``rows`` contains
    SQLite metadata tuples (asset_id, expected_sha256, expected_size).
    """
    if max_assets < 1 or max_hash_bytes < 0:
        raise ValueError('registered verification budgets must be positive')
    root = Path(root)
    counters = {'checked': 0, 'verified': 0, 'invalid': 0,
                'missing': 0, 'hashed_bytes': 0, 'exceeded_budget': False}
    previews = []
    if root.is_symlink() or not root.is_dir():
        return {**counters, 'complete': False, 'safe': False,
                'issues': [{'reason': 'unsafe_native_root'}]}
    complete = True
    for asset_id, expected_sha, expected_bytes in rows:
        if counters['checked'] >= max_assets:
            counters['exceeded_budget'] = True
            complete = False
            break
        counters['checked'] += 1
        reason = None
        data = None
        try:
            if (not isinstance(asset_id, str) or str(UUID(asset_id)) != asset_id or
                    not isinstance(expected_sha, str) or len(expected_sha) != 64 or
                    any(c not in '0123456789abcdef' for c in expected_sha) or
                    not isinstance(expected_bytes, int) or expected_bytes < 0):
                reason = 'invalid_metadata'
            else:
                folder = root / asset_id
                data = folder / 'data.parquet'
                if folder.is_symlink() or data.is_symlink():
                    reason = 'unsafe_symlink'
                elif not folder.is_dir() or not data.is_file():
                    reason = 'snapshot_missing'
                    counters['missing'] += 1
                else:
                    before = data.stat()
                    if before.st_size != expected_bytes:
                        reason = 'file_size_mismatch'
                    elif counters['hashed_bytes'] + expected_bytes > max_hash_bytes:
                        counters['exceeded_budget'] = True
                        complete = False
                        break
                    else:
                        actual = _digest(data)
                        counters['hashed_bytes'] += expected_bytes
                        after = data.stat()
                        signature = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
                        if signature(before) != signature(after):
                            reason = 'changed_during_hash'
                        elif actual != expected_sha:
                            reason = 'file_sha256_mismatch'
        except (ValueError, TypeError):
            reason = 'invalid_metadata'
        except OSError:
            reason = 'snapshot_unreadable'
        if reason:
            counters['invalid'] += 1
            complete = False
            if len(previews) < 25:
                previews.append({'asset_id': str(asset_id)[:64], 'reason': reason})
        else:
            counters['verified'] += 1
    return {**counters, 'complete': complete,
            'safe': bool(complete and counters['invalid'] == 0),
            'issues': previews}
