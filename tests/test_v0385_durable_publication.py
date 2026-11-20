"""v0.38.5 durable sync/async publication and recovery regression.

Uses real FastAPI/SQLite/filesystem and a deliberately explicit on-disk BI
substitute. Does not claim DuckDB/Parquet or Perspective physical validation.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.generic_assets import PublishGeneral
from timepilot.project_publication import ProjectPublicationJobs
from timepilot.projects import ProjectCreate
from timepilot.smart_import import SmartImportError
from timepilot.worker_runtime import build_worker_runtime


class DiskBI:
    """Explicit physical bytes and metadata, without a fake Parquet claim."""
    def __init__(self, home):
        self.root=Path(home)/'test_bi'
        self.root.mkdir(exist_ok=True)
        self.assets={}
        self.calls=0
        self.error_after_write=False

    def import_from_path(self, path, name, lineage):
        self.calls+=1
        data=Path(path).read_bytes()
        aid=str(uuid4())
        target=self.root/(aid+'.csv')
        target.write_bytes(data)
        with io.StringIO(data.decode('utf-8')) as stream:
            count=sum(1 for _ in csv.DictReader(stream))
        record={'asset_id':aid,'name':name,'row_count':count,'lineage':lineage,
                'sha256':hashlib.sha256(data).hexdigest()}
        self.assets[aid]=record
        if self.error_after_write:
            raise OSError('response lost after external filesystem write')
        return record

    def asset(self, aid):
        return self.assets[aid]

    def verify(self, aid, *, force=False):
        stored=self.assets[aid]
        path=self.root/(aid+'.csv')
        good=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==stored['sha256']
        return {'asset_id':aid,'valid':good}

    def find_published_candidates(self,pid,iid,cid,vid):
        matching=[]
        for aid,record in self.assets.items():
            lineage=record['lineage']
            if all(lineage.get(k)==v for k,v in {'project_id':pid,'import_id':iid,
                            'candidate_id':cid,'version_id':vid}.items()):
                matching.append(aid)
        return {'asset_ids':matching,'complete':True,'unregistered_snapshots':0,
                'scanned':len(self.assets),'source':'on_disk_csv_double'}


def setup_case(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    bi=DiskBI(tmp_path)
    native=app.state.native_bi
    native.import_from_path=bi.import_from_path
    native.asset=bi.asset
    native.verify=bi.verify
    native.find_published_candidates=bi.find_published_candidates
    app.state.project_publication_jobs.service.platform.evaluate_quality=lambda *a,**kw:{'status':'passed'}
    client=TestClient(app)
    client.__enter__()
    pid=client.post('/api/v1/projects',json={'name':'持久发布回归'}).json()['id']
    source='timestamp,device,temp\n2026-11-18 08:00,A,25\n2026-11-18 08:01,A,26\n'
    uploaded=client.post(f'/api/v1/projects/{pid}/imports',
                         files=[('files',('sensor.csv',source.encode()))])
    assert uploaded.status_code==201,uploaded.text
    iid=uploaded.json()['import_id']
    cid=client.get(f'/api/v1/projects/{pid}/imports/{iid}').json()['candidates'][0]['candidate_id']
    endpoint=f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general'
    payload={'name':'设备温度','confirm':True}
    return app,client,bi,pid,iid,cid,endpoint,payload


def test_immediate_api_uses_one_durable_job_and_immutable_stage_receipts(tmp_path):
    app,client,bi,pid,iid,cid,url,payload=setup_case(tmp_path)
    try:
        first=client.post(url,json=payload)
        assert first.status_code==201,first.text
        doc=first.json()
        assert doc['status']=='ready' and doc['job_status']=='succeeded'
        assert doc['meta']['row_count']==2
        jid=doc['job_id']
        assert bi.calls==1
        report=client.get(f'/api/v1/projects/{pid}/publish-jobs/{jid}/journal')
        assert report.status_code==200,report.text
        timeline=report.json()
        phases=[r['phase'] for r in timeline['events']]
        assert timeline['chain_valid']
        for step in ('queued','verifying','publishing','csv_prepared','registering_bi',
                     'native_registered','project_linked','checking_quality','complete'):
            assert step in phases,phases
        assert phases.index('registering_bi') < phases.index('native_registered')
        assert phases.index('native_registered') < phases.index('project_linked')
        assert phases.index('ready_to_commit') < phases.index('complete')
        assert phases.count('complete')==1
        assert timeline['events'][-1]['status']=='succeeded'
        assert 'csv_sha256' in next(r for r in timeline['events'] if r['phase']=='csv_prepared')['receipt']
        second=client.post(url,json=payload)
        assert second.status_code==201 and second.json()['reused'] is True
        assert second.json()['job_id']==jid and bi.calls==1
        same=client.get(f'/api/v1/projects/{pid}/publish-jobs/{jid}/journal').json()
        assert same['event_count']==timeline['event_count']
        # Immutable audit entries are protected against local SQL UPDATE/DELETE.
        with app.state.projects._db() as db:
            with pytest.raises(sqlite3.DatabaseError,match='immutable'):
                db.execute('UPDATE project_publication_events SET event_hash=? WHERE job_id=?',('bad',jid))
            with pytest.raises(sqlite3.DatabaseError,match='immutable'):
                db.execute('DELETE FROM project_publication_events WHERE job_id=?',(jid,))
    finally:
        client.__exit__(None,None,None)


def test_failure_after_external_write_requires_evidence_based_reconciliation(tmp_path):
    app,client,bi,pid,iid,cid,url,payload=setup_case(tmp_path)
    try:
        bi.error_after_write=True
        resp=client.post(url,json=payload)
        assert resp.status_code==422,resp.text
        jobs=app.state.project_publication_jobs.list(pid)
        assert len(jobs)==1
        job=jobs[0]
        assert job['status']=='failed' and job['stage']=='needs_reconciliation'
        assert bi.calls==1
        assert client.post(url,json=payload).status_code==422
        assert bi.calls==1
        inspect=client.get(f'/api/v1/projects/{pid}/publish-jobs/{job["job_id"]}/reconciliation')
        assert inspect.status_code==200,inspect.text
        evidence=inspect.json()
        assert evidence['classification']=='verified_incomplete_link'
        review=client.post(f'/api/v1/projects/{pid}/publish-jobs/{job["job_id"]}/reconciliation/decide',
            json={'decision_id':str(uuid4()),'action':'adopt_verified_orphan',
                  'evidence_sha256':evidence['evidence_sha256'],
                  'reason':'已核实磁盘文件 SHA256 和来源关系，确认补齐未完成的项目关联',
                  'confirm':True})
        assert review.status_code==200,review.text
        assert review.json()['job']['status']=='succeeded'
        assert bi.calls==1
        journal=client.get(f'/api/v1/projects/{pid}/publish-jobs/{job["job_id"]}/journal').json()
        assert journal['chain_valid']
        assert journal['events'][-1]['receipt']['action']=='adopt_verified_orphan'
        assert journal['events'][-1]['status']=='succeeded'
    finally:
        client.__exit__(None,None,None)


def test_queued_source_fingerprint_mismatch_fails_before_io(tmp_path,monkeypatch):
    runtime=build_worker_runtime('publication',home=tmp_path)
    try:
        projects=runtime.services['projects']
        pid=projects.create(ProjectCreate(name='来源变化'))['id']
        uploaded=runtime.services['imports'].ingest(io.BytesIO(b'a,b\n1,2\n'),'sensor.csv')
        iid=uploaded['import_id'];cid=uploaded['candidates'][0]['candidate_id']
        projects.attach_import(pid,iid)
        jobs=runtime.services['publication_jobs']
        job=jobs.submit(pid,iid,cid,PublishGeneral(name='初版',confirm=True))
        original=jobs.service.generic.expected_version
        def altered(*args,**kwargs):
            result=original(*args,**kwargs).copy()
            result['version_id']='a'*24
            return result
        monkeypatch.setattr(jobs.service.generic,'expected_version',altered)
        monkeypatch.setattr(jobs.service,'publish',lambda *a,**kw:pytest.fail('source changed yet imported'))
        result=jobs.work_once()
        assert result['status']=='failed' and result['stage']=='failed'
        assert '来源数据' in result['error']
        journal=jobs.journal(pid,job['job_id'])
        assert journal['chain_valid']
        assert journal['events'][-1]['receipt']['error_type']=='SmartImportError'
        assert not list((tmp_path/'fake_native').glob('*')) if (tmp_path/'fake_native').is_dir() else True
    finally:
        runtime.close()


def test_expired_external_write_is_fenced_and_recorded(tmp_path):
    runtime=build_worker_runtime('publication',home=tmp_path)
    try:
        projects=runtime.services['projects']
        pid=projects.create(ProjectCreate(name='租约保护'))['id']
        uploaded=runtime.services['imports'].ingest(io.BytesIO(b'a,b\n1,2\n'),'sensor.csv')
        iid=uploaded['import_id'];cid=uploaded['candidates'][0]['candidate_id']
        projects.attach_import(pid,iid)
        jobs=runtime.services['publication_jobs']
        job=jobs.submit(pid,iid,cid,PublishGeneral(name='原始',confirm=True))
        with projects._db() as db:
            db.execute('''UPDATE project_publication_jobs SET status='running',
                stage='registering_bi',attempts=1,lease_owner=?,lease_until=? WHERE job_id=?''',
                ('dead-worker',time.time()-1,job['job_id']))
        assert jobs.work_once() is None
        failed=jobs.get(pid,job['job_id'])
        assert failed['status']=='failed' and failed['stage']=='needs_reconciliation'
        journal=jobs.journal(pid,job['job_id'])
        assert journal['chain_valid']
        assert journal['events'][-1]['receipt']['reason_code']=='expired_lease_after_write_boundary'
        with pytest.raises(SmartImportError,match='人工核对'):
            jobs.retry(pid,job['job_id'])
    finally:
        runtime.close()


def test_project_scope_of_journal(tmp_path):
    app,client,bi,pid,iid,cid,url,payload=setup_case(tmp_path)
    try:
        job=client.post(url,json=payload).json()
        other=client.post('/api/v1/projects',json={'name':'另一个项目'}).json()['id']
        forbidden=client.get(f'/api/v1/projects/{other}/publish-jobs/{job["job_id"]}/journal')
        assert forbidden.status_code==404
    finally:
        client.__exit__(None,None,None)
