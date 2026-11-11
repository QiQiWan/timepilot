"""Integrity-checked, per-worksheet scan checkpoints for project ingestion.

Only immutable *structural discovery* is cached. Conversion, user approvals and
source bytes are never inferred from a checkpoint. Every cache entry is tied
to the actual member SHA-256 and the scanner revision; a bad cache is rescanned.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SCANNER_REVISION = 3


def _serialize(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')


class WorksheetCheckpoints:
    def __init__(self, root: Path | None):
        self.root = Path(root) if root is not None else None
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, file_id: str, index: int):
        if self.root is None:
            return None
        # Only generated import member identities, never supplied filenames.
        if len(file_id) != 5 or not file_id.startswith('f') or not file_id[1:].isdigit():
            raise ValueError('非法的工作表缓存编号')
        return self.root / f'{file_id}_{index:04d}.json'

    @staticmethod
    def _identity(file_id: str, sha256: str, index: int, title: str):
        return {'scanner_revision': SCANNER_REVISION, 'file_id': file_id,
                'source_sha256': sha256, 'sheet_index': index, 'sheet_name': title}

    def get(self, *, file_id: str, sha256: str, index: int, title: str):
        path = self._path(file_id, index)
        if path is None or not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text(encoding='utf-8'))
            payload = envelope['payload']
            if envelope['digest'] != hashlib.sha256(_serialize(payload)).hexdigest():
                return None
            expected = self._identity(file_id, sha256, index, title)
            if any(payload.get(k) != v for k, v in expected.items()):
                return None
            candidates = payload['candidates']
            if not isinstance(candidates, list) or len(candidates) > 500:
                return None
            return candidates
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return None

    def put(self, *, file_id: str, sha256: str, index: int, title: str, candidates: list):
        path = self._path(file_id, index)
        if path is None:
            return
        payload = {**self._identity(file_id, sha256, index, title),
                   'candidates': candidates}
        envelope = {'payload': payload,
                    'digest': hashlib.sha256(_serialize(payload)).hexdigest()}
        tmp = path.with_suffix('.writing')
        with tmp.open('w', encoding='utf-8') as out:
            json.dump(envelope, out, ensure_ascii=False, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)

    def summary(self):
        """Return only progress metadata; never expose sampled source row data."""
        if self.root is None or not self.root.exists():
            return []
        results = []
        for path in sorted(self.root.glob('f????_*.json')):
            try:
                envelope = json.loads(path.read_text(encoding='utf-8'))
                payload = envelope['payload']
                if envelope['digest'] != hashlib.sha256(_serialize(payload)).hexdigest():
                    continue
                results.append({'file_id': payload['file_id'],
                                'sheet_index': payload['sheet_index'],
                                'sheet': payload['sheet_name'],
                                'candidate_count': len(payload['candidates']),
                                'sha256_verified': True})
            except (OSError, KeyError, ValueError, TypeError):
                continue
        return results