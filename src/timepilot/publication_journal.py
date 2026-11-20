"""Durable write-ahead publication events shared by synchronous and queued API.

Entries are appended in the same SQLite transaction as the corresponding job
transition. The ledger records receipts/fingerprints, never source row contents.
A local hash chain detects accidental changes; it is not an external signature.
"""
from __future__ import annotations

import hashlib
import json
import time

from timepilot.smart_import import SmartImportError


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


class PublicationJournal:
    # Only allow fields with bounded size and no sensitive row data.
    ALLOWED_RECEIPT = frozenset({
        'version_id', 'source_version_id', 'source_sha256', 'csv_sha256',
        'data_sha256', 'row_count', 'native_asset_id', 'publication_key',
        'decision_id', 'action', 'error_type', 'reason_code',
    })

    @staticmethod
    def initialize(db):
        db.execute('''CREATE TABLE IF NOT EXISTS project_publication_events(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            event_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            created_at REAL NOT NULL)''')
        db.execute('''CREATE INDEX IF NOT EXISTS idx_publication_events_job
            ON project_publication_events(project_id, job_id, sequence)''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS publication_events_no_update
            BEFORE UPDATE ON project_publication_events
            BEGIN SELECT RAISE(ABORT,'publication event immutable'); END''')
        db.execute('''CREATE TRIGGER IF NOT EXISTS publication_events_no_delete
            BEFORE DELETE ON project_publication_events
            BEGIN SELECT RAISE(ABORT,'publication event immutable'); END''')

    @classmethod
    def append(cls, db, *, job_id: str, project_id: str, phase: str,
               status: str, receipt: dict | None = None):
        safe = {}
        for field, value in (receipt or {}).items():
            if field not in cls.ALLOWED_RECEIPT:
                raise SmartImportError('发布回执包含未授权字段：' + str(field))
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise SmartImportError('发布回执字段类型无效：' + field)
            if isinstance(value, str) and len(value) > 160:
                raise SmartImportError('发布回执字段过长：' + field)
            safe[field] = value
        old = db.execute('''SELECT event_hash FROM project_publication_events
            WHERE project_id=? AND job_id=? ORDER BY sequence DESC LIMIT 1''',
            (project_id, job_id)).fetchone()
        prev = old['event_hash'] if old else '0' * 64
        event = {'job_id': job_id, 'project_id': project_id, 'phase': phase,
                 'status': status, 'receipt': safe, 'created_at': time.time()}
        event_hash = _hash({'previous_hash': prev, 'event': event})
        db.execute('''INSERT INTO project_publication_events
            (job_id,project_id,event_json,previous_hash,event_hash,created_at)
            VALUES (?,?,?,?,?,?)''',
            (job_id, project_id, _canonical(event), prev, event_hash, event['created_at']))

    @classmethod
    def read(cls, db, *, job_id: str, project_id: str):
        rows = db.execute('''SELECT event_json,previous_hash,event_hash FROM project_publication_events
            WHERE project_id=? AND job_id=? ORDER BY sequence''', (project_id, job_id)).fetchall()
        expected = '0' * 64
        valid = True
        events = []
        for row in rows:
            event = json.loads(row['event_json'])
            correct = row['previous_hash'] == expected and row['event_hash'] == _hash(
                {'previous_hash': expected, 'event': event})
            valid = valid and correct
            events.append({**event, 'event_hash': row['event_hash'], 'verified': correct})
            expected = row['event_hash']
        return {'job_id': job_id, 'chain_valid': valid, 'events': events,
                'event_count': len(events)}
