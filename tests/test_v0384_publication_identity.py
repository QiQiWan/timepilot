"""v0.38.4: real SQLite, cross-process locks and physical identity invariants.

No DuckDB substitution is claimed here; the disk test double deliberately
persists content and lineage for two independently spawned Python processes.
"""
from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from timepilot.generic_assets import PublishGeneral
from timepilot.project_publication import ProjectPublicationJobs
from timepilot.projects import ProjectCreate
from timepilot.publication_identity import publication_key
from timepilot.smart_import import SmartImportError
from timepilot.worker_runtime import build_worker_runtime


class DiskNative:
    """Shared on-disk fake, verifying bytes/lineage without requiring DuckDB."""
    def __init__(self, home, *, sleep_seconds=0.0, corrupt=False):
        self.home=Path(home)/'fake_native'
        self.home.mkdir(exist_ok=True)
        self.sleep_seconds=sleep_seconds
        self.corrupt=corrupt

    def _docs(self):
        path=self.home/'meta.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def find_published_candidates(self,pid,iid,cid,vid):
        docs=self._docs()
        found=[aid for aid,record in docs.items()
               if all(record['lineage'].get(k)==v for k,v in
                      {'project_id':pid,'import_id':iid,'candidate_id':cid,'version_id':vid}.items())]
        return {'asset_ids':found,'scanned':len(docs),'complete':True,
                'unregistered_snapshots':0,'source':'shared_disk_fake'}

    def asset(self,aid):
        return self._docs()[aid]

    def verify(self,aid,*,force=False):
        stored=self.asset(aid)
        path=self.home/(aid+'.bin')
        valid=path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==stored['sha256']
        return {'asset_id':aid,'valid':bool(valid and not self.corrupt)}

    def import_from_path(self,path,name,lineage):
        docs=self._docs()
        aid=str(uuid4())
        data=Path(path).read_bytes()
        self.home.joinpath(aid+'.bin').write_bytes(data)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        record={'asset_id':aid,'name':name,'row_count':len(data.splitlines())-1,
                'sha256':hashlib.sha256(data).hexdigest(), 'lineage':lineage}
        docs[aid]=record
        temporary=self.home/(aid+'.tmp')
        temporary.write_text(json.dumps(docs))
        os.replace(temporary,self.home/'meta.json')
        with (self.home/'writes.log').open('a') as stream:
            stream.write(aid+'\n')
        return record


def create_case(home):
    runtime=build_worker_runtime('publication',home=home)
    catalog=runtime.services['projects']
    pid=catalog.create(ProjectCreate(name='并发发布测试'))['id']
    source=b'time,temp\n2026-01-01,21\n2026-01-02,22\n'
    doc=runtime.services['imports'].ingest(io.BytesIO(source),'sensor.csv')
    iid=doc['import_id']
    catalog.attach_import(pid,iid)
    cid=doc['candidates'][0]['candidate_id']
    return runtime,pid,iid,cid


def process_publish(home,pid,iid,cid,output):
    try:
        runtime=build_worker_runtime('publication',home=Path(home))
        service=runtime.services['publication_jobs'].service
        service.native=DiskNative(home,sleep_seconds=0.3)
        service.platform.evaluate_quality=lambda *args,**kwargs:{'status':'passed'}
        result=service.publish(pid,iid,cid,PublishGeneral(name='原始数据',confirm=True))
        output.put(('ok',result['native_asset_id'],result['reused']))
        runtime.close()
    except Exception as exc:
        output.put(('failed',type(exc).__name__,str(exc)))


def test_same_version_concurrent_submit_uses_one_durable_identity(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        jobs=runtime.services['publication_jobs']
        request=PublishGeneral(name='设备温度',confirm=True)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:jobs.submit(pid,iid,cid,request),range(12)))
        assert len({r['job_id'] for r in results})==1
        identity=jobs.service.generic.expected_version(iid,cid,request)
        key=publication_key(pid,iid,cid,identity['identity_sha256'])
        with jobs.catalog._db() as db:
            registered=db.execute('SELECT * FROM project_publication_identities WHERE publication_key=?',(key,)).fetchall()
            assert len(registered)==1 and registered[0]['job_id']==results[0]['job_id']
            assert db.execute('SELECT COUNT(*) FROM project_publication_jobs').fetchone()[0]==1
        with pytest.raises(SmartImportError,match='已有发布任务'):
            jobs.submit(pid,iid,cid,PublishGeneral(name='改名冲突',confirm=True))
    finally:
        runtime.close()


def test_two_os_processes_publish_one_physical_snapshot(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    runtime.close()
    ctx=multiprocessing.get_context('spawn')
    out=ctx.Queue()
    processes=[ctx.Process(target=process_publish,args=(str(tmp_path),pid,iid,cid,out)) for _ in range(2)]
    for p in processes:p.start()
    try:
        for p in processes:
            p.join(timeout=35)
            if p.is_alive():p.terminate();p.join(timeout=5)
            assert p.exitcode==0
        replies=[out.get(timeout=5) for _ in processes]
        assert all(r[0]=='ok' for r in replies),replies
        assert len({r[1] for r in replies})==1
        assert sorted(r[2] for r in replies)==[False,True]
        assert len((tmp_path/'fake_native'/'writes.log').read_text().splitlines())==1
        runtime=build_worker_runtime('publication',home=tmp_path)
        try:
            assert len(runtime.services['projects'].generic_for(pid))==1
        finally:runtime.close()
    finally:
        for p in processes:
            if p.is_alive():p.terminate();p.join()


def test_reused_corrupt_snapshot_fails_closed_without_new_import(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        svc=runtime.services['publication_jobs'].service
        fake=DiskNative(tmp_path)
        svc.native=fake
        svc.platform.evaluate_quality=lambda *args,**kwargs:{'status':'passed'}
        request=PublishGeneral(name='初版',confirm=True)
        first=svc.publish(pid,iid,cid,request)
        aid=first['native_asset_id']
        path=fake.home/(aid+'.bin')
        path.write_bytes(b'CORRUPTED')
        with pytest.raises(SmartImportError,match='SHA256'):
            svc.publish(pid,iid,cid,PublishGeneral(name='重新命名',confirm=True))
        assert len((fake.home/'writes.log').read_text().splitlines())==1
    finally:runtime.close()


def test_generic_manifest_cannot_hide_changed_csv(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        generic=runtime.services['publication_jobs'].service.generic
        request=PublishGeneral(name='原始数据',confirm=True)
        meta=generic.publish(iid,cid,request)
        path=generic._base(iid,cid,meta['version_id'])/'data.csv'
        path.write_bytes(b'altered,bytes\n1,2\n')
        with pytest.raises(SmartImportError,match='完整性'):
            generic.publish(iid,cid,request)
    finally:runtime.close()


def test_conflicting_project_and_unified_version_links_are_rejected(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        svc=runtime.services['publication_jobs'].service
        fake=DiskNative(tmp_path)
        svc.native=fake
        svc.platform.evaluate_quality=lambda *args,**kwargs:{'status':'passed'}
        first=svc.publish(pid,iid,cid,PublishGeneral(name='初版',confirm=True))
        meta=first['meta'];aid=first['native_asset_id']
        with pytest.raises(SmartImportError,match='另一原生资产'):
            runtime.services['projects'].attach_generic(pid,iid,cid,meta['version_id'],str(uuid4()))
        with svc.catalog._db() as db:
            db.execute('UPDATE unified_versions SET native_asset_id=? WHERE project_id=? AND version_id=?',
                       (str(uuid4()),pid,meta['version_id']))
        with pytest.raises(SmartImportError,match='物理身份冲突'):
            svc.platform.register(pid,{'import_id':iid,'candidate_id':cid,
                                       'version_id':meta['version_id'],'native_asset_id':aid},meta)
    finally:runtime.close()


def test_closed_review_prevents_sync_republish_even_inside_writer_lock(tmp_path):
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        jobs=runtime.services['publication_jobs']
        request=PublishGeneral(name='确认版本',confirm=True)
        queued=jobs.submit(pid,iid,cid,request)
        with jobs.catalog._db() as db:
            db.execute("UPDATE project_publication_jobs SET status='failed',stage='needs_reconciliation' WHERE job_id=?",
                       (queued['job_id'],))
        with pytest.raises(SmartImportError,match='需核对'):
            jobs.service.publish(pid,iid,cid,request)
    finally:runtime.close()


def process_hold_region_lock(home,pid,iid,cid,notice):
    from timepilot.publication_identity import region_publication_lock
    runtime=build_worker_runtime('publication',home=Path(home))
    with region_publication_lock(runtime.services['projects'],pid,iid,cid):
        notice.send('acquired')
        # Simulate a SIGKILL-like abrupt termination without context cleanup.
        os._exit(17)


def test_crashed_writer_releases_os_lock(tmp_path):
    from timepilot.publication_identity import region_publication_lock
    runtime,pid,iid,cid=create_case(tmp_path)
    ctx=multiprocessing.get_context('spawn');parent,child=ctx.Pipe(duplex=False)
    process=ctx.Process(target=process_hold_region_lock,args=(str(tmp_path),pid,iid,cid,child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv()=='acquired'
        process.join(timeout=20)
        assert process.exitcode==17
        # A crash releases the advisory lock; orphan detection still decides
        # whether a subsequent external write is safe.
        with region_publication_lock(runtime.services['projects'],pid,iid,cid,timeout_seconds=2):
            assert True
    finally:
        if process.is_alive():process.terminate();process.join()
        runtime.close()


def test_two_reviewers_cannot_finalize_same_incident_twice(tmp_path):
    from timepilot.publication_reconciliation import ReconciliationDecision
    runtime,pid,iid,cid=create_case(tmp_path)
    try:
        jobs=runtime.services['publication_jobs']
        request=PublishGeneral(name='并发审批',confirm=True)
        job=jobs.submit(pid,iid,cid,request)
        jobs.service.generic.publish(iid,cid,request)
        fake=DiskNative(tmp_path)
        jobs.service.native=fake
        with jobs.catalog._db() as db:
            db.execute("UPDATE project_publication_jobs SET status='failed',stage='needs_reconciliation' WHERE job_id=?",
                       (job['job_id'],))
        evidence=jobs.reconciliation.inspect(pid,job['job_id'])
        assert evidence['classification']=='verified_absence'
        def review(_):
            payload=ReconciliationDecision(decision_id=str(uuid4()),
                action='retry_verified_absence',
                reason='已查看原始数据版本及 Native BI 清单，确认没有外部写入',
                evidence_sha256=evidence['evidence_sha256'],confirm=True)
            try:return jobs.reconciliation.decide(pid,job['job_id'],payload)['job']['status']
            except SmartImportError:return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(review,range(2)))
        assert sorted(results)==['queued','rejected']
        assert len(jobs.reconciliation.history(pid,job['job_id'])['events'])==1
    finally:runtime.close()


def test_launcher_routes_release_gate_to_current_version(monkeypatch,tmp_path):
    import sys
    from scripts import launcher
    from timepilot import __version__
    called=[]
    monkeypatch.setattr(launcher,'source_env',lambda: {
        'TIMEPILOT_HOME':str(tmp_path),'TIMEPILOT_DATA_HOME_ORIGIN':'qa'})
    monkeypatch.setattr(launcher,'run',lambda *args,**kwargs:called.append(args))
    monkeypatch.setattr(sys,'argv',['launcher.py','release-gate','--mode','source'])
    assert launcher.main()==0
    assert len(called)==1
    assert called[0][1]==f'scripts/release_gate_v{__version__.replace(".", "")}.py'
    assert called[0][2:]==('--mode','source')
