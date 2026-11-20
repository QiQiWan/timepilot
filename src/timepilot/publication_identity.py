"""Stable project publication identity and cross-process writer serialization.

One lock protects an imported region across synchronous, queued, batched and
human-reviewed writes. It is an OS advisory lock, not a stale sentinel. After
an unexpected death the next writer must still inspect native metadata and
unregistered snapshots before deciding whether a new import is safe.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json

from timepilot.project_migrations import catalog_startup_lock
from timepilot.smart_import import SmartImportError


def publication_key(project_id: str, import_id: str, candidate_id: str,
                    identity_sha256: str) -> str:
    """Names are deliberately excluded: renames cannot create a new dataset."""
    if len(identity_sha256) != 64 or any(c not in '0123456789abcdef' for c in identity_sha256):
        raise SmartImportError('发布来源版本指纹不合法')
    payload = json.dumps([project_id, import_id, candidate_id, identity_sha256],
                         ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@contextmanager
def region_publication_lock(catalog, project_id: str, import_id: str,
                            candidate_id: str, *, timeout_seconds: float = 60):
    """Serialize external writes across processes without holding a SQLite tx."""
    path_identity = hashlib.sha256(json.dumps(
        [project_id, import_id, candidate_id], ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    path = catalog.root / '.publication-locks' / (path_identity + '.lock')
    try:
        with catalog_startup_lock(path, timeout_seconds=timeout_seconds):
            yield
    except TimeoutError as exc:
        raise SmartImportError('同一区域已有发布或核对操作正在执行，请查看任务状态后重试') from exc
