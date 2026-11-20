"""Single-host external worker leases and operational visibility.

A worker role owns a short SQLite lease with a generation fence. A different
process may acquire the role only after expiry. A separate heartbeat thread
keeps long-running *leased* jobs visible; each business queue must continue to
fence its own artifact writes. SQLite-backed coordination is single-host only.
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from statistics import median
from uuid import uuid4

LOG = logging.getLogger(__name__)
ROLES = ('forecast', 'agent', 'upload', 'publication', 'feature')


class WorkerLeaseBusy(RuntimeError):
    """A live worker already owns the selected role."""


class WorkerLeaseLost(RuntimeError):
    """This process may no longer execute another job for the role."""


class WorkerRegistry:
    def __init__(self, home: str | Path, *, lease_seconds: int = 30):
        if not 10 <= lease_seconds <= 180:
            raise ValueError('Worker lease must be between 10 and 180 seconds')
        self.home = Path(home).expanduser().resolve()
        self.lease_seconds = lease_seconds
        base = self.home / 'operations'
        base.mkdir(parents=True, exist_ok=True)
        try:
            base.chmod(0o700)
        except OSError:
            pass
        self.path = base / 'worker_status.sqlite3'
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS role_lease (
                role TEXT PRIMARY KEY, owner TEXT NOT NULL, generation INTEGER NOT NULL,
                lease_until REAL NOT NULL, updated_at REAL NOT NULL,
                status TEXT NOT NULL, claimed_cycles INTEGER NOT NULL DEFAULT 0,
                error_cycles INTEGER NOT NULL DEFAULT 0,
                last_error_type TEXT);
              CREATE TABLE IF NOT EXISTS role_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL,
                recorded_at REAL NOT NULL, duration_ms REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS role_activity_recent
                ON role_activity(role, recorded_at DESC);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=6, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA busy_timeout=6000')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def claim(self, role: str, owner: str) -> int | None:
        if role not in ROLES or not owner or len(owner) > 120:
            raise ValueError('Invalid worker role/owner')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            old = db.execute('SELECT * FROM role_lease WHERE role=?', (role,)).fetchone()
            if old and old['owner'] != owner and old['lease_until'] > now:
                return None
            generation = int(old['generation']) + 1 if old else 1
            if old:
                db.execute('''UPDATE role_lease SET owner=?,generation=?,lease_until=?,
                    updated_at=?,status='idle',last_error_type=NULL WHERE role=?''',
                    (owner, generation, now + self.lease_seconds, now, role))
            else:
                db.execute('''INSERT INTO role_lease
                    (role,owner,generation,lease_until,updated_at,status)
                    VALUES (?,?,?,?,?,'idle')''',
                    (role, owner, generation, now + self.lease_seconds, now))
            return generation

    def renew(self, role: str, owner: str, generation: int, *, status: str = 'idle') -> bool:
        if status not in {'idle','busy','memory_paused'}:
            raise ValueError('Invalid worker status')
        now = time.time()
        with self._connect() as db:
            updated = db.execute('''UPDATE role_lease SET lease_until=?,updated_at=?,status=?
                WHERE role=? AND owner=? AND generation=? AND lease_until>?''',
                (now + self.lease_seconds, now, status, role, owner, generation, now))
            return updated.rowcount == 1

    def record(self, role: str, owner: str, generation: int,
               *, claimed: bool, duration_ms: float, error_type: str | None = None) -> bool:
        """Count attempts, not successful job completions (which each queue tracks)."""
        if not 0 <= duration_ms <= 86400000:
            raise ValueError('Invalid cycle duration')
        now = time.time()
        error_name = error_type[:64] if error_type else None
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''UPDATE role_lease SET
                claimed_cycles=claimed_cycles+?,error_cycles=error_cycles+?,
                last_error_type=CASE WHEN ? IS NOT NULL THEN ? ELSE last_error_type END
                WHERE role=? AND owner=? AND generation=? AND lease_until>?''',
                (int(bool(claimed)), int(bool(error_name)), error_name, error_name,
                 role, owner, generation, now))
            if row.rowcount != 1:
                return False
            if claimed:
                db.execute('INSERT INTO role_activity(role,recorded_at,duration_ms) VALUES(?,?,?)',
                           (role, now, duration_ms))
                # Bound persistent event volume per role, independent of service uptime.
                db.execute('''DELETE FROM role_activity WHERE role=? AND id NOT IN
                    (SELECT id FROM role_activity WHERE role=? ORDER BY id DESC LIMIT 200)''',
                    (role, role))
            return True

    def release(self, role: str, owner: str, generation: int) -> bool:
        with self._connect() as db:
            return bool(db.execute('''UPDATE role_lease SET owner='',lease_until=0,
                updated_at=?,status='stopped' WHERE role=? AND owner=? AND generation=?''',
                (time.time(), role, owner, generation)).rowcount)

    def snapshot(self) -> dict:
        now = time.time()
        with self._connect() as db:
            rows = {x['role']: x for x in db.execute('SELECT * FROM role_lease')}
            samples = {role: [r[0] for r in db.execute('''SELECT duration_ms FROM role_activity
                     WHERE role=? ORDER BY id DESC LIMIT 100''', (role,))] for role in ROLES}
        roles = {}
        for role in ROLES:
            row = rows.get(role)
            alive = bool(row and row['owner'] and row['lease_until'] > now)
            vals = sorted(samples[role]); n = len(vals)
            roles[role] = {
                'online': alive,
                'status': row['status'] if alive else 'offline',
                'age_seconds': round(max(0, now - row['updated_at']), 2) if row else None,
                'lease_remaining_seconds': round(max(0, row['lease_until'] - now), 2) if alive else 0,
                'claimed_cycles': row['claimed_cycles'] if row else 0,
                'error_cycles': row['error_cycles'] if row else 0,
                'last_error_type': row['last_error_type'] if row else None,
                'latency_sample_count': n,
                'p50_ms': round(median(vals), 2) if n else None,
                'p95_ms': round(vals[math.ceil(n * .95) - 1], 2) if n else None,
            }
        return {'roles': roles, 'online_count': sum(v['online'] for v in roles.values()),
                'required_count': len(ROLES), 'single_host_only': True}


class WorkerSupervisor:
    """Run exactly one business role per OS process; never auto-spawn API servers."""
    def __init__(self, registry: WorkerRegistry, role: str, action, *, poll_seconds: float = .75,
                 max_rss_mib: float | None = None, parent_pid: int | None = None):
        if role not in ROLES or not callable(action):
            raise ValueError('Invalid role/action')
        if not .1 <= poll_seconds <= 60:
            raise ValueError('poll_seconds must be between 0.1 and 60')
        if max_rss_mib is not None and not 128 <= max_rss_mib <= 1048576:
            raise ValueError('max_rss_mib must be >=128 MiB')
        if parent_pid is not None and (type(parent_pid) is not int or parent_pid < 1):
            raise ValueError('parent_pid must be a positive process ID')
        self.registry, self.role, self.action = registry, role, action
        self.poll_seconds, self.max_rss_mib = poll_seconds, max_rss_mib
        self.parent_pid = parent_pid
        self.owner = f'{os.getpid()}:{uuid4().hex}'

    def _parent_alive(self) -> bool:
        if self.parent_pid is None:
            return True
        if os.name != 'nt' and os.getppid() != self.parent_pid:
            return False
        try:
            import psutil
            return psutil.pid_exists(self.parent_pid)
        except ImportError:
            # Unix is still protected by getppid. Windows require psutil for
            # pool parent monitoring (psutil is in the runtime requirements).
            return os.name != 'nt'

    def _memory_exceeded(self) -> bool:
        if self.max_rss_mib is None:
            return False
        try:
            import psutil
            return psutil.Process().memory_info().rss > self.max_rss_mib * 1024**2
        except ImportError as exc:
            raise RuntimeError('Memory budget requires psutil') from exc

    def run(self, stop: threading.Event | None = None, *, max_cycles: int | None = None) -> int:
        stop = stop or threading.Event()
        if max_cycles is not None and max_cycles < 1:
            raise ValueError('max_cycles must be >=1')
        epoch = self.registry.claim(self.role, self.owner)
        if epoch is None:
            raise WorkerLeaseBusy(f'Role {self.role} already has an active worker')
        lost = threading.Event()
        busy = threading.Event()
        memory_paused = threading.Event()
        def pulse():
            interval = max(1.0, min(3.0, self.registry.lease_seconds / 4))
            while not stop.wait(interval):
                if not self._parent_alive():
                    LOG.error('Worker pool parent lost; stopping role %s', self.role)
                    stop.set();return
                status = 'busy' if busy.is_set() else 'memory_paused' if memory_paused.is_set() else 'idle'
                try:
                    if not self.registry.renew(self.role, self.owner, epoch, status=status):
                        lost.set();stop.set();return
                except (OSError,sqlite3.Error):
                    # A transient SQLite error should not immediately reclaim the role.
                    LOG.exception('Worker heartbeat database error')
        heart = threading.Thread(target=pulse, name=f'worker-{self.role}-heartbeat', daemon=True)
        heart.start()
        cycles = 0
        try:
            while not stop.is_set() and not lost.is_set():
                if max_cycles is not None and cycles >= max_cycles:
                    break
                if not self._parent_alive():
                    stop.set();break
                if self._memory_exceeded():
                    memory_paused.set()
                    stop.wait(self.poll_seconds)
                    continue
                memory_paused.clear()
                if not self.registry.renew(self.role, self.owner, epoch, status='busy'):
                    lost.set();break
                busy.set()
                start = time.perf_counter()
                did_work = False; error_type = None
                try:
                    did_work = bool(self.action())
                except Exception as exc:
                    error_type = type(exc).__name__
                    LOG.exception('Worker role %s cycle failed', self.role)
                finally:
                    busy.clear()
                    duration = (time.perf_counter() - start) * 1000
                    if not self.registry.record(self.role, self.owner, epoch,
                                                claimed=did_work, duration_ms=duration,
                                                error_type=error_type):
                        lost.set()
                cycles += 1
                if not did_work and not stop.is_set():
                    stop.wait(self.poll_seconds)
            if lost.is_set():
                raise WorkerLeaseLost(f'Worker role {self.role} lost its process-level lease')
            return cycles
        finally:
            stop.set()
            heart.join(timeout=2)
            self.registry.release(self.role, self.owner, epoch)


def queue_summary(projects) -> dict:
    """Bounded aggregate state only; never read project rows, file paths or payloads."""
    tables = ('feature_jobs', 'project_import_jobs', 'project_publication_jobs')
    result = {}
    with projects._db() as db:
        for table in tables:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                result[table] = {'available': False, 'counts': {}}
                continue
            rows = db.execute(f'SELECT status,COUNT(*) AS n FROM {table} GROUP BY status').fetchall()
            result[table] = {'available': True, 'counts': {str(r['status']): int(r['n']) for r in rows}}
    return result
