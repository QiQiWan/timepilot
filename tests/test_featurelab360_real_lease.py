"""OS child exit + elapsed lease wall time; no manually edited lease clock."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from timepilot.feature_execution import FeatureJobEngine


class Database:
    def __init__(self, file):
        self.file=file
        self._lock=threading.RLock()

    def _db(self):
        db=sqlite3.connect(self.file,timeout=10)
        db.row_factory=sqlite3.Row
        return db


def make_worker(path, ident):
    worker=FeatureJobEngine.__new__(FeatureJobEngine)
    worker.projects=Database(path)
    worker.worker_id=ident
    worker.lease_seconds=2
    return worker


def test_os_process_exits_and_lease_is_reclaimed_after_real_time(tmp_path):
    path=tmp_path/'durable_jobs.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE feature_jobs(
            job_id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,status TEXT,
            lease_owner TEXT,lease_until TEXT,lease_epoch INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,progress REAL DEFAULT 0,
            result_json TEXT,error_code TEXT,error_text TEXT,created_at TEXT,updated_at TEXT);
        ''')
        now=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO feature_jobs(job_id,project_id,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   ('test-job','project-A','profile','queued',now,now))
    root=Path(__file__).resolve().parents[1]
    child_code='''
import os,sys,sqlite3,threading
from timepilot.feature_execution import FeatureJobEngine
class Store:
 def __init__(self,file): self.file=file; self._lock=threading.RLock()
 def _db(self):
  db=sqlite3.connect(self.file,timeout=10);db.row_factory=sqlite3.Row;return db
w=FeatureJobEngine.__new__(FeatureJobEngine)
w.projects=Store(sys.argv[1]);w.worker_id='dead-worker';w.lease_seconds=2
j=w._claim()
assert j and j['job_id']=='test-job'
print(j['_lease_token']+'|'+str(j['_lease_epoch']),flush=True)
os._exit(113)
'''
    child=subprocess.run([sys.executable,'-c',child_code,str(path)],capture_output=True,text=True,
        timeout=15,cwd=root,env={**os.environ,'PYTHONPATH':str(root/'src')})
    assert child.returncode==113, child.stderr
    old_token,old_epoch=child.stdout.strip().split('|')
    worker=make_worker(path,'live-worker')
    assert worker._claim() is None, 'another worker must not steal a live lease'
    beginning=time.monotonic()
    reclaimed=None
    deadline=beginning+7
    while time.monotonic()<deadline:
        reclaimed=worker._claim()
        if reclaimed:break
        time.sleep(.15)
    assert reclaimed is not None, 'expired OS lease was never reclaimed'
    assert reclaimed['job_id']=='test-job'
    assert reclaimed['_lease_epoch']==int(old_epoch)+1
    assert time.monotonic()-beginning<6
    assert not worker.heartbeat('test-job',old_token,int(old_epoch))
    assert worker._claim() is None, 'third worker must not steal the new lease'
    worker._finish({**reclaimed,'_lease_token':old_token,'_lease_epoch':int(old_epoch)},
                   'succeeded',{'profile_id':'stale'})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM feature_jobs WHERE job_id='test-job'").fetchone()[0]=='running'
    worker._finish(reclaimed,'succeeded',{'profile_id':'new'})
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT status,attempts,lease_epoch,result_json FROM feature_jobs WHERE job_id='test-job'").fetchone()
    assert row[0]=='succeeded' and row[1]==2 and row[2]==2
    assert '"new"' in row[3]
    assert worker._claim() is None
