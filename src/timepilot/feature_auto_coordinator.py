"""Durable, bounded server-side continuation of approved FeatureLab sessions.

Only progresses *existing* analysis and previously human-approved materialization.
Never selects candidates, creates a FeatureSet, approves, publishes, or bypasses
source/contract validation. Jobs remain owned by FeatureJobEngine's leased queue.

For enterprise mode, each step revalidates the original user's active identity
and current editor authorization. A revoked user cannot be serviced by this worker.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from timepilot.enterprise_security import CURRENT_PRINCIPAL, Principal, SecurityError

LOG = logging.getLogger(__name__)
ACTIVE = frozenset({'profiling', 'previewing', 'materializing', 'evaluating'})
FINISHED = frozenset({'succeeded', 'failed_retryable', 'failed_terminal', 'cancelled'})


class FeatureAutoCoordinator:
    def __init__(self, analysis):
        self.analysis = analysis
        self.projects = analysis.projects
        self.jobs = analysis.jobs
        self._cursor = 0
        self.owner = socket.gethostname()+'-'+str(os.getpid())+'-'+uuid4().hex
        self.lease_seconds=120

    @contextmanager
    def _owner_context(self, project_id: str, owner_id: str):
        security = self.projects.security
        if security is None:
            if owner_id != 'local':
                raise SecurityError('An enterprise task cannot be resumed in local mode')
            yield
            return
        with security._db() as db:
            user = db.execute('SELECT user_id,tenant_id,username,tenant_role,active '
                              'FROM users WHERE user_id=?', (owner_id,)).fetchone()
        if user is None or not user['active']:
            raise SecurityError('The analysis owner is no longer active')
        principal = Principal(user['user_id'], user['tenant_id'], user['username'],
                              user['tenant_role'], 'internal-feature-auto-worker', '')
        security.allowed_project(principal, project_id, required='editor')
        token = CURRENT_PRINCIPAL.set(principal)
        try:
            yield
        finally:
            CURRENT_PRINCIPAL.reset(token)

    def _job_ids(self, state):
        stage = state.get('stage')
        if stage == 'profile':
            return [state['profile_job_id']] if state.get('profile_job_id') else []
        if stage == 'preview':
            return list(state.get('preview_job_ids') or [])
        if stage == 'materializing':
            return [state['materialize_job_id']] if state.get('materialize_job_id') else []
        if stage == 'evaluation':
            return [state['evaluation_job_id']] if state.get('evaluation_job_id') else []
        return []

    def _ready(self, pid, state):
        ids = self._job_ids(state)
        if not ids:
            return False
        return all(self.jobs.get_job(pid, jid)['status'] in FINISHED for jid in ids)

    def _claim_session(self,sid):
        token=self.owner+':'+uuid4().hex
        at=datetime.now(timezone.utc)
        expires=(at+timedelta(seconds=self.lease_seconds)).isoformat()
        with self.projects._lock,self.projects._db() as db:
            changed=db.execute("""UPDATE feature_analysis_sessions
                SET coordinator_owner=?,coordinator_lease_until=?
                WHERE session_id=? AND (coordinator_lease_until IS NULL OR coordinator_lease_until<?)""",
                (token,expires,sid,at.isoformat()))
            return token if changed.rowcount==1 else None

    def _renew_session(self,sid,token):
        expires=(datetime.now(timezone.utc)+timedelta(seconds=self.lease_seconds)).isoformat()
        with self.projects._lock,self.projects._db() as db:
            changed=db.execute("""UPDATE feature_analysis_sessions SET coordinator_lease_until=?
                WHERE session_id=? AND coordinator_owner=?""",(expires,sid,token))
            return changed.rowcount==1

    def _release_session(self,sid,token):
        with self.projects._lock,self.projects._db() as db:
            db.execute("""UPDATE feature_analysis_sessions SET coordinator_owner=NULL,
                coordinator_lease_until=NULL WHERE session_id=? AND coordinator_owner=?""",(sid,token))

    def work_once(self):
        """Advance at most one ready session. Never waits on a queued worker job.

        Durable SQLite state and idempotent job request IDs survive process exits.
        An optimistic revision conflict simply defers to the next worker pass.
        """
        with self.projects._db() as db:
            rows = db.execute('''SELECT project_id,owner_id,session_id,state_json
                FROM feature_analysis_sessions
                WHERE json_extract(state_json, '$.status') IN
                    ('profiling','previewing','materializing','evaluating')
                ORDER BY updated_at ASC''').fetchall()
        if not rows:
            return False
        # Rotate the scan so many waiting sessions cannot starve a ready one.
        start = self._cursor % len(rows)
        self._cursor = (start + 1) % len(rows)
        for row in list(rows[start:]) + list(rows[:start]):
            pid, sid = row['project_id'], row['session_id']
            try:
                token=self._claim_session(sid)
                if not token:continue
                stop=threading.Event()
                def keepalive():
                    while not stop.wait(20):
                        if not self._renew_session(sid,token):return
                pulse=threading.Thread(target=keepalive,daemon=True,name='feature-auto-lease')
                pulse.start()
                try:
                    with self._owner_context(pid, row['owner_id']):
                        state = self.analysis.get(pid, sid)
                        if state['stale'] or state['status'] not in ACTIVE or not self._ready(pid, state):
                            continue
                        next_state = self.analysis.advance(pid, sid)
                        if next_state['revision'] != state['revision']:
                            return True
                finally:
                    stop.set();pulse.join(timeout=1)
                    self._release_session(sid,token)
            except (SecurityError, KeyError) as exc:
                # Auth revocation or missing resources must never be bypassed.
                LOG.warning('Feature auto session %s cannot resume: %s', sid, type(exc).__name__)
            except ValueError as exc:
                if 'updated elsewhere' not in str(exc) and 'revision conflict' not in str(exc):
                    LOG.warning('Feature auto session %s deferred: %s', sid, str(exc)[:180])
            except Exception:
                LOG.exception('Feature auto session %s failed to resume', sid)
        return False
