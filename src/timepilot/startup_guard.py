"""Cross-process, crash-safe bootstrap mutex for a single TIMEPILOT_HOME.

A fresh deployment can launch API and five workers at once. Multiple catalog
DDL migrations check schema then ALTER TABLE; such checks are unsafe without
serialization. A SQLite BEGIN IMMEDIATE transaction on a *separate* lock DB
protects the whole application-service initialization (not normal API traffic).
Crashes release the SQLite lock without stale lock-file breakage. Single host.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def startup_guard(home: str | Path, *, timeout_seconds: int = 90):
    if not 10 <= timeout_seconds <= 180:
        raise ValueError('Startup lock timeout must be between 10 and 180 seconds')
    base = Path(home).expanduser().resolve() / 'operations'
    base.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        base.chmod(0o700)
    lock_file = base / 'startup_lock.sqlite3'
    with sqlite3.connect(str(lock_file), timeout=timeout_seconds,
                         isolation_level=None) as conn:
        if os.name != 'nt':
            lock_file.chmod(0o600)
        conn.execute(f'PRAGMA busy_timeout={int(timeout_seconds * 1000)}')
        # No DDL/DML occurs in this dedicated lock database. A write-reserved
        # transaction is sufficient to serialize all other startup_guard uses.
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield
        finally:
            conn.execute('ROLLBACK')
