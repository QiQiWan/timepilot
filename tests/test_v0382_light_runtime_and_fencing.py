"""v0.38.2 real job queues, expired leases, and role-scoped initialization."""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from timepilot.agent_executor import JobRequest
from timepilot.generic_assets import PublishGeneral
from timepilot.project_import_jobs import ProjectLeaseExpired
from timepilot.projects import ProjectCreate
from timepilot.smart_import import SmartImportError
from timepilot.worker_runtime import build_worker_runtime


def _fresh_project(runtime):
    return runtime.services['projects'].create(ProjectCreate(name='Worker QA'))['id']


def _csv():
    return b'date,temperature,device\n2025-01-01,18,A\n2025-01-02,19,A\n'


def test_role_specific_runtime_never_constructs_web_application(tmp_path):
    for role in ('forecast', 'upload', 'publication', 'feature', 'agent'):
        location = tmp_path / role
        script = '''import sys
from timepilot.worker_runtime import build_worker_runtime
runtime=build_worker_runtime(sys.argv[1],home=sys.argv[2])
assert 'timepilot.api' not in sys.modules, 'HTTP API unexpectedly imported'
assert callable(runtime.action)
assert runtime.role == sys.argv[1]
assert runtime.registry.snapshot()['online_count'] == 0
runtime.close()
'''
        root=Path(__file__).resolve().parents[1]
        env=dict(os.environ, PYTHONPATH=str(root / 'src'))
        done=subprocess.run([sys.executable, '-c', script, role, str(location)],
                            env=env,capture_output=True,text=True,timeout=35)
        assert done.returncode==0, (role,done.stderr[-1200:])
        if role != 'agent':
            assert not (location / 'agent_workflows').exists()


def test_upload_actual_csv_on_external_lightweight_worker(tmp_path):
    runtime=build_worker_runtime('upload',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        job=runtime.services['import_jobs'].submit(pid,[(io.BytesIO(_csv()),'source.csv')])
        result=runtime.action()
        assert result['status']=='succeeded',result
        assert runtime.services['import_jobs'].get(pid,job['job_id'])['status']=='succeeded'
        assert len(runtime.services['projects'].imports_for(pid))==1
        assert runtime.action() is None
    finally:
        runtime.close()


def test_upload_expired_lease_must_not_link_project_or_mark_terminal(tmp_path):
    runtime=build_worker_runtime('upload',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        jobs=runtime.services['import_jobs']
        job=jobs.submit(pid,[(io.BytesIO(_csv()),'source.csv')])
        claimed=jobs._claim()
        jid=job['job_id']; old_owner=claimed['lease_owner']
        # Simulate immutable ingestion having finished before the owning process froze.
        staged=jobs.root/jid/'0000.csv'
        with staged.open('rb') as f:
            jobs.catalog.imports.ingest(f,'source.csv',import_id=jid)
        with jobs.catalog._db() as db:
            db.execute('UPDATE project_import_jobs SET lease_until=? WHERE job_id=?',
                       (time.time()-2,jid))
        with pytest.raises(ProjectLeaseExpired):
            jobs._commit_success(jid,old_owner,pid)
        assert not jobs._heartbeat(jid,old_owner)
        assert not jobs._finish(jid,old_owner,success=False,error='old')
        with jobs.catalog._db() as db:
            assert db.execute('SELECT COUNT(*) FROM project_imports WHERE import_id=?',(jid,)).fetchone()[0]==0
            assert db.execute('SELECT status FROM project_import_jobs WHERE job_id=?',(jid,)).fetchone()[0]=='running'
        # A new owner resumes from the durable, immutable manifest.
        recovered=jobs._claim()
        assert recovered['lease_owner']!=old_owner
        jobs._commit_success(jid,recovered['lease_owner'],pid)
        assert jobs.get(pid,jid)['status']=='succeeded'
        assert len(jobs.catalog.imports_for(pid))==1
    finally:
        runtime.close()


def test_publication_expired_external_stage_requires_reconciliation(tmp_path):
    runtime=build_worker_runtime('publication',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        imports=runtime.services['imports']
        with io.BytesIO(_csv()) as stream:
            manifest=imports.ingest(stream,'source.csv')
        runtime.services['projects'].attach_import(pid,manifest['import_id'])
        candidate=manifest['candidates'][0]['candidate_id']
        jobs=runtime.services['publication_jobs']
        request=PublishGeneral(name='源数据',roles={},confirm=True)
        job=jobs.submit(pid,manifest['import_id'],candidate,request)
        jid=job['job_id']
        with jobs.catalog._db() as db:
            db.execute('''UPDATE project_publication_jobs SET status='running',
                stage='registering_bi',lease_owner='former',lease_until=?,attempts=1 WHERE job_id=?''',
                (time.time()-5,jid))
        assert jobs._claim() is None
        state=jobs.get(pid,jid)
        assert state['status']=='failed'
        assert state['stage']=='needs_reconciliation'
        assert state['reconciliation_required'] is True
        assert not jobs._beat(jid,'former')
        with pytest.raises(SmartImportError, match='人工核对'):
            jobs.retry(pid,jid)
        assert jobs._claim() is None
    finally:
        runtime.close()


def test_publication_expired_verification_can_reclaim(tmp_path):
    runtime=build_worker_runtime('publication',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        with io.BytesIO(_csv()) as stream:
            manifest=runtime.services['imports'].ingest(stream,'source.csv')
        runtime.services['projects'].attach_import(pid,manifest['import_id'])
        job=runtime.services['publication_jobs'].submit(pid,manifest['import_id'],
            manifest['candidates'][0]['candidate_id'],
            PublishGeneral(name='预检查',roles={},confirm=True))
        jobs=runtime.services['publication_jobs']; jid=job['job_id']
        with jobs.catalog._db() as db:
            db.execute('''UPDATE project_publication_jobs SET status='running',
                stage='verifying',lease_owner='former',lease_until=?,attempts=1 WHERE job_id=?''',
                (time.time()-5,jid))
        reclaimed=jobs._claim()
        assert reclaimed and reclaimed['lease_owner']!='former'
        assert not jobs._beat(jid,'former')
        assert jobs.get(pid,jid)['reconciliation_required'] is False
    finally:
        runtime.close()


def test_agent_expired_owner_cannot_set_succeeded(tmp_path):
    runtime=build_worker_runtime('agent',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        executor=runtime.services['agent_executor']
        doc=executor.create(JobRequest(goal='catalog',project_id=pid))
        jid=doc['job_id']
        assert executor._claim()==jid
        with executor._db() as db:
            db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-1,jid))
        executor._update(jid,status='succeeded',result={'fake':True})
        assert executor.status(jid)['status']=='running'
        assert executor._claim()==jid  # expired read-only step safely queued/reclaimed
        executor._update(jid,status='failed',error='test cleanup')
        assert executor.status(jid)['status']=='failed'
    finally:
        runtime.close()


def test_agent_tool_execution_still_completes_without_api(tmp_path):
    runtime=build_worker_runtime('agent',home=tmp_path)
    try:
        pid=_fresh_project(runtime)
        executor=runtime.services['agent_executor']
        jid=executor.create(JobRequest(goal='catalog',project_id=pid))['job_id']
        for _ in range(3):
            runtime.action()
        state=executor.status(jid)
        assert state['status']=='succeeded',state
        assert state['result']['analysis_only'] is True
    finally:
        runtime.close()
