"""v0.33.4: real child-process termination, durable SQLite lease and fencing.

The test actually launches and terminates a separate Python process. To avoid
sleeping 90 seconds in CI it advances the persisted lease timestamp to the
expired point, simulating elapsed wall-clock time after OS process death.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_conversational_bi_v0231 import _env
from timepilot.feature_execution import FeatureJobEngine


def test_process_crash_reclaimed_and_stale_worker_fenced(tmp_path):
    app, client, pid, aid, _ = _env(tmp_path)
    try:
        job = app.state.feature_jobs.create_profile(pid, aid, 'base', 'real-process-kill-334')
        db_file = app.state.projects.db_file
        root = Path(__file__).resolve().parents[1]
        script = r'''
import sqlite3, os, sys, threading
from timepilot.feature_execution import FeatureJobEngine
class Store:
    def __init__(self,p):self.file=p;self._lock=threading.RLock()
    def _db(self):
        c=sqlite3.connect(self.file,timeout=10)
        c.row_factory=sqlite3.Row
        return c
w=FeatureJobEngine.__new__(FeatureJobEngine)
w.projects=Store(sys.argv[1]);w.worker_id='child-os-process';w.lease_seconds=90
j=w._claim()
if j is None:raise RuntimeError('failed to claim')
print(j['_lease_token']+'|'+str(j['_lease_epoch']),flush=True)
os._exit(137)
'''
        child = subprocess.run([sys.executable, '-c', script, str(db_file)],
                               cwd=str(root), env={**os.environ,
                               'PYTHONPATH':str(root/'src')},capture_output=True,
                               text=True,timeout=18)
        assert child.returncode==137, child.stderr
        old_token,old_epoch=child.stdout.strip().split('|')
        worker=app.state.feature_jobs
        assert worker._claim() is None, 'do not steal a valid lease'
        with app.state.projects._lock, app.state.projects._db() as db:
            db.execute('UPDATE feature_jobs SET lease_until=? WHERE job_id=?',
                ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),job['job_id']))
        reclaimed=worker._claim()
        assert reclaimed is not None
        assert reclaimed['job_id']==job['job_id']
        assert reclaimed['_lease_epoch']==int(old_epoch)+1
        assert reclaimed['_lease_token']!=old_token
        assert worker.heartbeat(job['job_id'],old_token,int(old_epoch)) is False
        # A stale completion cannot commit a second result or move the status.
        old_row={**reclaimed,'_lease_token':old_token,'_lease_epoch':int(old_epoch)}
        worker._finish(old_row,'succeeded',{'profile_id':'stale'})
        assert worker.get_job(pid,job['job_id'])['status']=='running'
        worker._finish(reclaimed,'succeeded',{'profile_id':'fresh'})
        done=worker.get_job(pid,job['job_id'])
        assert done['status']=='succeeded' and done['result']['profile_id']=='fresh'
        assert worker._claim() is None
    finally:
        client.__exit__(None,None,None)


def test_two_worker_threads_only_one_owner_and_one_commit(tmp_path):
    app,client,pid,aid,_ = _env(tmp_path)
    try:
        job=app.state.feature_jobs.create_profile(pid,aid,'base','two-worker-race-334')
        a=app.state.feature_jobs
        b=FeatureJobEngine(app.state.projects,app.state.native_bi,
                           app.state.feature_semantic,app.state.feature_registry)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=[pool.submit(w._claim) for w in (a,b)]
            claimed=[f.result(timeout=8) for f in pending]
        winners=[x for x in claimed if x]
        assert len(winners)==1
        owned=winners[0]
        assert owned['job_id']==job['job_id']
        a._finish(owned,'succeeded',{'profile_id':'one'})
        b._finish(owned,'succeeded',{'profile_id':'duplicate'})
        done=a.get_job(pid,job['job_id'])
        assert done['result']['profile_id']=='one'
        assert done['attempts']==1
    finally:
        client.__exit__(None,None,None)


def test_same_size_source_mutation_stales_old_session(tmp_path,monkeypatch):
    from timepilot.feature_auto_analysis import FeatureAutoAnalysis
    app,client,pid,aid,_=_env(tmp_path)
    try:
        svc=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
        state=svc.create(pid,aid,'base',goal='数据检查',task_hint='quality',
                 request_id='source-byte-edit-334',budget=4)
        verify=lambda _id:{'valid':True}
        monkeypatch.setattr(app.state.native_bi,'verify',verify)
        assert not svc.get(pid,state['session_id'])['stale']
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':False,
            'reason':'checksum_mismatch'})
        assert svc.get(pid,state['session_id'])['stale']
        import pytest
        with pytest.raises(ValueError,match='stale|source|Source|变化|失效'):
            svc.advance(pid,state['session_id'])
    finally:
        client.__exit__(None,None,None)
