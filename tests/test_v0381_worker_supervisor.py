"""v0.38.1 real SQLite, process fencing, routing and failure-injection QA."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from timepilot.worker_supervisor import (
    ROLES, WorkerLeaseBusy, WorkerRegistry, WorkerSupervisor, queue_summary,
)


def test_concurrent_fresh_sqlite_metadata_bootstrap(tmp_path):
    """Five separate processes must initialize the same new SQLite DB safely."""
    root = Path(__file__).resolve().parents[1]
    gate = tmp_path / 'start.flag'
    arrivals = tmp_path / 'ready'
    arrivals.mkdir()
    url = 'sqlite:///' + (tmp_path/'fresh.sqlite3').as_posix()
    script = r'''import os, sys, time
from pathlib import Path
from timepilot.db import Store
url, gate, arrivals = sys.argv[1:]
(Path(arrivals) / str(os.getpid())).touch()
deadline=time.monotonic()+20
while not Path(gate).is_file() and time.monotonic()<deadline: time.sleep(.025)
if not Path(gate).is_file(): raise RuntimeError('start barrier missing')
s=Store(url)
assert len(s.list_datasets())==0
s.engine.dispose()
'''
    env = dict(os.environ, PYTHONPATH=str(root/'src'))
    processes = [subprocess.Popen([sys.executable,'-c',script,url,str(gate),str(arrivals)],
                                  env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                 for _ in range(5)]
    try:
        deadline = time.monotonic()+15
        while len(list(arrivals.iterdir()))<5 and time.monotonic()<deadline:
            time.sleep(.03)
        assert len(list(arrivals.iterdir()))==5, 'not all 5 subprocesses reached startup barrier'
        gate.touch()
        failures=[]
        for child in processes:
            out,err = child.communicate(timeout=25)
            if child.returncode:
                failures.append((child.returncode,(out+err).decode(errors='replace')[-1200:]))
        assert not failures, failures
        with sqlite3.connect(tmp_path/'fresh.sqlite3') as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='datasets'").fetchone()[0]==1
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)


def test_only_one_lease_owner_under_race_and_no_stale_renewal(tmp_path):
    first = WorkerRegistry(tmp_path, lease_seconds=10)
    second = WorkerRegistry(tmp_path, lease_seconds=10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda item: item[0].claim('feature', item[1]),
                               ((first, 'alpha'), (second, 'beta'))))
    assert sorted(x is not None for x in claims) == [False, True]
    winner = 'alpha' if claims[0] else 'beta'
    loser = 'beta' if claims[0] else 'alpha'
    epoch = claims[0] or claims[1]
    assert first.renew('feature', loser, epoch) is False
    assert first.renew('feature', winner, epoch, status='busy')
    assert second.claim('feature', loser) is None
    assert first.release('feature', winner, epoch)
    next_epoch = second.claim('feature', loser)
    assert next_epoch is not None and next_epoch > epoch
    assert first.renew('feature', winner, epoch) is False
    assert not first.release('feature', winner, epoch)
    assert second.release('feature', loser, next_epoch)


def test_expired_role_recovery_is_fenced(tmp_path):
    registry = WorkerRegistry(tmp_path, lease_seconds=10)
    old = registry.claim('upload', 'old')
    with sqlite3.connect(registry.path) as db:
        db.execute('UPDATE role_lease SET lease_until=? WHERE role=?', (time.time()-1, 'upload'))
    fresh = registry.claim('upload', 'new')
    assert fresh > old
    assert not registry.renew('upload', 'old', old)
    assert not registry.record('upload', 'old', old, claimed=True, duration_ms=1)
    assert registry.renew('upload', 'new', fresh)
    assert registry.release('upload', 'new', fresh)


def test_supervisor_records_only_actual_work_and_error_types(tmp_path):
    registry = WorkerRegistry(tmp_path)
    actions = iter([True, False, ValueError('user-data-shall-not-leak')])
    def action():
        result = next(actions)
        if isinstance(result, Exception):
            raise result
        return result
    assert WorkerSupervisor(registry, 'feature', action, poll_seconds=.1).run(max_cycles=3) == 3
    item = registry.snapshot()['roles']['feature']
    assert not item['online']
    assert item['claimed_cycles'] == 1
    assert item['error_cycles'] == 1
    assert item['last_error_type'] == 'ValueError'
    assert 'user-data' not in str(item)
    assert item['p50_ms'] is not None and item['p95_ms'] is not None
    assert item['latency_sample_count'] == 1


def test_supervisor_rejects_existing_owner(tmp_path):
    registry = WorkerRegistry(tmp_path)
    epoch = registry.claim('agent', 'other-live-worker')
    with pytest.raises(WorkerLeaseBusy):
        WorkerSupervisor(registry, 'agent', lambda: False).run(max_cycles=1)
    assert registry.release('agent', 'other-live-worker', epoch)


def test_worker_monitor_is_bounded_and_explicit(tmp_path):
    registry = WorkerRegistry(tmp_path)
    assert registry.snapshot()['online_count'] == 0
    with pytest.raises(ValueError): WorkerRegistry(tmp_path, lease_seconds=0)
    with pytest.raises(ValueError): registry.claim('illegal', 'a')
    with pytest.raises(ValueError): WorkerSupervisor(registry, 'feature', lambda: False, max_rss_mib=1)
    epoch = registry.claim('forecast', 'only-forecast')
    assert registry.snapshot()['online_count'] == 1
    assert registry.snapshot()['required_count'] == len(ROLES)
    assert registry.release('forecast', 'only-forecast', epoch)
    with sqlite3.connect(registry.path) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_real_subprocess_kill_then_reclaim_without_waiting_ttl(tmp_path):
    """Simulate SIGKILL; force lease expiration to avoid a 10-second sleep."""
    script = '''import time
from timepilot.worker_supervisor import WorkerRegistry,WorkerSupervisor
r=WorkerRegistry(%r,lease_seconds=10)
WorkerSupervisor(r,'upload',lambda: time.sleep(.15) or False,poll_seconds=.1).run()
''' % str(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    child = subprocess.Popen([sys.executable, '-c', script], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    registry = WorkerRegistry(tmp_path, lease_seconds=10)
    try:
        deadline = time.monotonic()+8
        while time.monotonic() < deadline and not registry.snapshot()['roles']['upload']['online']:
            time.sleep(.05)
        assert registry.snapshot()['roles']['upload']['online']
        with sqlite3.connect(registry.path) as db:
            old = db.execute('SELECT owner,generation FROM role_lease WHERE role=?', ('upload',)).fetchone()
        child.kill()
        child.wait(timeout=4)
        assert registry.claim('upload', 'replacement') is None  # no unsafe immediate takeover
        with sqlite3.connect(registry.path) as db:
            db.execute('UPDATE role_lease SET lease_until=? WHERE role=?', (time.time()-1, 'upload'))
        epoch = registry.claim('upload', 'replacement')
        assert epoch > old[1]
        assert registry.renew('upload', old[0], old[1]) is False
        assert registry.release('upload', 'replacement', epoch)
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)
        if child.stderr: child.stderr.close()


def test_role_queue_aggregate_uses_no_payload_or_asset_names(tmp_path):
    class Catalog:
        def _db(self):
            db = sqlite3.connect(tmp_path / 'catalog.sqlite3')
            db.row_factory = sqlite3.Row
            return db
    with Catalog()._db() as db:
        db.execute('CREATE TABLE feature_jobs(status TEXT,payload_json TEXT)')
        db.executemany('INSERT INTO feature_jobs VALUES (?,?)',
                       [('queued','sensitive-key'),('queued','source-asset-path'),('running','private')])
    summary = queue_summary(Catalog())
    assert summary['feature_jobs'] == {'available':True,'counts':{'queued':2,'running':1}}
    assert not summary['project_import_jobs']['available']
    assert 'sensitive-key' not in str(summary)


def test_api_embedded_threads_share_registry_and_external_conflict(tmp_path):
    from timepilot.api import create_app
    app = create_app(home=tmp_path, embedded_worker=True)
    with TestClient(app) as browser:
        deadline = time.monotonic()+8
        status = browser.get('/api/v1/system/workers').json()
        while status['online_count'] < 5 and time.monotonic() < deadline:
            time.sleep(.1)
            status = browser.get('/api/v1/system/workers').json()
        assert status['online_count'] == 5, status
        assert status['deployment'] == 'embedded'
        assert all(status['embedded'].values())
        assert status['queues']['feature_jobs']['available']
        with pytest.raises(WorkerLeaseBusy):
            WorkerSupervisor(app.state.worker_registry, 'feature', lambda: False).run(max_cycles=1)
    assert app.state.worker_registry.snapshot()['online_count'] == 0


def test_api_only_readiness_has_real_external_component(tmp_path):
    from timepilot.api import create_app
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app) as client:
        response = client.get('/api/v1/ready')
        assert response.status_code == 503
        detail = response.json()['detail']
        assert detail['components']['external_workers'] is False
        assert detail['external_worker_verified'] is False
        assert '缺失或心跳过期' in ' '.join(detail['problems'])
        statuses = client.get('/api/v1/system/workers').json()
        assert statuses['online_count'] == 0


def test_enterprise_worker_metrics_are_admin_only(tmp_path, monkeypatch):
    from timepilot.api import create_app
    from timepilot.enterprise_security import EnterpriseSecurity
    security = EnterpriseSecurity(tmp_path)
    security.bootstrap_tenant('test-factory', 'Factory', 'admin', 'A!long-private-password-2026')
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE', '0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE', '1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL', 'https://testserver')
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app, base_url='https://testserver') as admin:
        assert admin.get('/api/v1/system/workers').status_code == 401
        result = admin.post('/api/v1/auth/login', json={
            'tenant':'test-factory','username':'admin','password':'A!long-private-password-2026'})
        assert result.status_code == 200
        assert admin.get('/api/v1/system/workers').status_code == 200
        user_id = admin.post('/api/v1/identity/users', json={
            'username':'viewer','password':'Viewer-Secret-2026'},
            headers={'X-TimePilot-CSRF':admin.cookies.get('tp_csrf')}).json()['user_id']
        assert user_id
        with TestClient(app, base_url='https://testserver') as viewer:
            signed = viewer.post('/api/v1/auth/login', json={
                'tenant':'test-factory','username':'viewer','password':'Viewer-Secret-2026'})
            assert signed.status_code == 200
            assert viewer.get('/api/v1/system/workers').status_code == 403


def test_multi_tenant_admin_cannot_read_global_worker_and_log_activity(tmp_path, monkeypatch):
    """Aggregate worker queues and log text are shared across tenants."""
    from timepilot.api import create_app
    from timepilot.enterprise_security import EnterpriseSecurity
    security = EnterpriseSecurity(tmp_path)
    security.bootstrap_tenant('factory-one', 'Factory One', 'owner-one', 'Secret-passphrase-one-2026')
    security.bootstrap_tenant('factory-two', 'Factory Two', 'owner-two', 'Secret-passphrase-two-2026')
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE', '0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE', '1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL', 'https://testserver')
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app, base_url='https://testserver') as browser:
        login = browser.post('/api/v1/auth/login', json={
            'tenant':'factory-one', 'username':'owner-one',
            'password':'Secret-passphrase-one-2026'})
        assert login.status_code == 200
        assert browser.get('/api/v1/system/workers').status_code == 403
        assert browser.get('/api/v1/system/logs').status_code == 403
        assert browser.get('/api/v1/system/logs/download').status_code == 403
        assert browser.get('/api/v1/system/logs/summary').status_code == 403


def test_embedded_api_refuses_live_external_process_role(tmp_path):
    from timepilot.api import create_app
    registry = WorkerRegistry(tmp_path)
    epoch = registry.claim('feature', 'separate-process')
    try:
        app = create_app(home=tmp_path, embedded_worker=True)
        with pytest.raises(RuntimeError, match='角色已由其他进程占用'):
            with TestClient(app):
                pass
    finally:
        registry.release('feature', 'separate-process', epoch)


def test_memory_paused_worker_is_not_readiness_healthy(tmp_path):
    from timepilot.api import create_app
    app = create_app(home=tmp_path, embedded_worker=False)
    leases = []
    for role in ROLES:
        epoch = app.state.worker_registry.claim(role, 'worker-'+role)
        leases.append((role,epoch))
    try:
        assert app.state.worker_registry.renew('feature', 'worker-feature', leases[-1][1],
                                               status='memory_paused')
        with TestClient(app) as browser:
            readiness = browser.get('/api/v1/ready')
            assert readiness.status_code == 503
            assert readiness.json()['detail']['components']['external_workers'] is False
            assert 'feature' in ' '.join(readiness.json()['detail']['problems'])
    finally:
        for role,epoch in leases:
            app.state.worker_registry.release(role, 'worker-'+role, epoch)


def test_pool_managed_worker_checks_parent_death_before_job(tmp_path):
    registry = WorkerRegistry(tmp_path, lease_seconds=10)
    called = []
    worker = WorkerSupervisor(registry, 'feature', lambda: called.append(True),
                              poll_seconds=.1, parent_pid=99999999)
    assert worker.run(max_cycles=1) == 0
    assert called == []
    assert registry.snapshot()['online_count'] == 0
