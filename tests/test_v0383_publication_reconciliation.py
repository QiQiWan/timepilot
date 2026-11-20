"""v0.38.3: uncertain native side effects, human review and audit trace."""
from __future__ import annotations

import io
import json
import time
from uuid import uuid4

import pytest
from timepilot.generic_assets import PublishGeneral
from timepilot.projects import ProjectCreate
from timepilot.smart_import import SmartImportError
from timepilot.worker_runtime import build_worker_runtime
from timepilot.publication_reconciliation import ReconciliationDecision


class FakeNative:
    def __init__(self):
        self.docs = {}
        self.reliable = True
        self.unregistered = 0
        self.writes = 0
        self.fail_after_native_write = False

    def find_published_candidates(self,pid,iid,cid,vid):
        matches=[aid for aid,doc in self.docs.items() if all(doc['lineage'].get(k)==v for k,v in
                 {'project_id':pid,'import_id':iid,'candidate_id':cid,'version_id':vid}.items())]
        return {'asset_ids':matches,'scanned':len(self.docs),'complete':self.reliable,
                'unregistered_snapshots':self.unregistered,'source':'fake_authoritative_metadata'}

    def asset(self,aid):
        return self.docs[aid]

    def verify(self,aid,*,force=False):
        return {'valid':self.docs[aid].get('valid',True),'asset_id':aid}

    def import_from_path(self,path,name,lineage):
        self.writes += 1
        aid=str(uuid4())
        rows=path.read_text().count('\n')-1
        self.docs[aid]={'asset_id':aid,'name':name,'lineage':lineage,
                        'row_count':rows,'sha256':'a'*64, 'valid':True}
        if self.fail_after_native_write:
            raise OSError('process died after BI metadata committed')
        return self.docs[aid]


def create_case(tmp_path):
    runtime=build_worker_runtime('publication',home=tmp_path)
    projects=runtime.services['projects']
    pid=projects.create(ProjectCreate(name='对账回归'))['id']
    imports=runtime.services['imports']
    source=b'time,temperature\n2026-01-01,20\n2026-01-02,21\n'
    document=imports.ingest(io.BytesIO(source),'measurements.csv')
    iid=document['import_id'];projects.attach_import(pid,iid)
    cid=document['candidates'][0]['candidate_id']
    jobs=runtime.services['publication_jobs']
    native=FakeNative()
    jobs.service.native=native
    request=PublishGeneral(name='温度',confirm=True)
    job=jobs.submit(pid,iid,cid,request)
    jid=job['job_id']
    return runtime, pid, iid, cid, jid, jobs, native, request


def mark_uncertain(jobs,jid):
    with jobs.catalog._db() as db:
        db.execute("UPDATE project_publication_jobs SET status='failed',stage='needs_reconciliation',updated_at=? WHERE job_id=?",
                   (time.time(),jid))


def choice(evidence,action,*,key=None,reason='人工复核数据版本和原生快照完整性，确认下一步操作'):
    return ReconciliationDecision(decision_id=key or str(uuid4()),action=action,
                                  evidence_sha256=evidence['evidence_sha256'],reason=reason,confirm=True)


def test_failure_after_native_write_is_blocked_and_orphan_adopted(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        native.fail_after_native_write=True
        failed=jobs.work_once()
        assert failed['status']=='failed' and failed['reconciliation_required']
        assert len(native.docs)==1 and native.writes==1
        assert jobs.work_once() is None
        assert jobs.submit(pid,iid,cid,request)['job_id']==jid
        with pytest.raises(SmartImportError):
            jobs.submit(pid,iid,cid,PublishGeneral(name='改名后重试',confirm=True))
        with pytest.raises(SmartImportError):jobs.retry(pid,jid)
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='verified_incomplete_link',evidence
        assert evidence['allowed_actions']==['adopt_verified_orphan','close_without_retry']
        # Quality checking must not require DuckDB in this isolated fault test.
        jobs.service.platform.evaluate_quality=lambda *a,**kw: {'status':'passed'}
        decision=choice(evidence,'adopt_verified_orphan')
        done=jobs.reconciliation.decide(pid,jid,decision)
        assert done['job']['status']=='succeeded'
        assert native.writes==1 and len(jobs.catalog.generic_for(pid))==1
        history=jobs.reconciliation.history(pid,jid)
        assert history['chain_valid'] and len(history['events'])==1
        again=jobs.reconciliation.decide(pid,jid,decision)
        assert again['already_applied'] and native.writes==1
    finally:runtime.close()


def test_unknown_native_inventory_blocks_retry_until_explicit_closure(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        jobs.service.generic.publish(iid,cid,request)
        mark_uncertain(jobs,jid)
        native.reliable=False
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='blocked_inventory_incomplete'
        with pytest.raises(SmartImportError):jobs.reconciliation.decide(pid,jid,choice(evidence,'retry_verified_absence'))
        native.reliable=True
        native.unregistered=1
        changed=jobs.reconciliation.inspect(pid,jid)
        assert changed['classification']=='blocked_inventory_incomplete'
        with pytest.raises(SmartImportError):jobs.reconciliation.decide(pid,jid,choice(evidence,'close_without_retry'))
        resolved=jobs.reconciliation.decide(pid,jid,choice(changed,'close_without_retry'))
        assert resolved['job']['status']=='cancelled'
        assert jobs.reconciliation.history(pid,jid)['chain_valid']
    finally:runtime.close()


def test_absence_review_requeues_once_and_rejects_old_evidence(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        jobs.service.generic.publish(iid,cid,request)
        mark_uncertain(jobs,jid)
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='verified_absence'
        native.unregistered=1
        with pytest.raises(SmartImportError):jobs.reconciliation.decide(pid,jid,choice(evidence,'retry_verified_absence'))
        native.unregistered=0
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert jobs.reconciliation.decide(pid,jid,choice(evidence,'retry_verified_absence'))['job']['status']=='queued'
        assert jobs.work_once()['status']=='succeeded'
        assert native.writes==1
    finally:runtime.close()


def test_registered_verified_snapshot_can_complete_without_external_write(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        meta=jobs.service.generic.publish(iid,cid,request)
        lineage={'project_id':pid,'import_id':iid,'candidate_id':cid,
                 'version_id':meta['version_id'], 'source_sha256':meta['source_sha256'],
                 'data_sha256':meta['data_sha256']}
        aid=str(uuid4())
        native.docs[aid]={'asset_id':aid,'name':request.name,'lineage':lineage,
                          'row_count':meta['row_count'],'sha256':'b'*64,'valid':True}
        jobs.catalog.attach_generic(pid,iid,cid,meta['version_id'],aid)
        jobs.service.platform.register(pid,{'import_id':iid,'candidate_id':cid,
            'version_id':meta['version_id'],'native_asset_id':aid},meta)
        mark_uncertain(jobs,jid)
        e=jobs.reconciliation.inspect(pid,jid)
        assert e['classification']=='verified_complete',e
        jobs.reconciliation.decide(pid,jid,choice(e,'confirm_complete'))
        assert jobs.get(pid,jid)['status']=='succeeded' and native.writes==0
    finally:runtime.close()


def test_bad_snapshot_hash_prevents_adoption_and_audit_is_append_only(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        meta=jobs.service.generic.publish(iid,cid,request)
        aid=str(uuid4())
        native.docs[aid]={'lineage':{'project_id':pid,'import_id':iid,'candidate_id':cid,
          'version_id':meta['version_id'],'source_sha256':meta['source_sha256'],
          'data_sha256':meta['data_sha256']},'row_count':meta['row_count'],'sha256':'bad','valid':False}
        mark_uncertain(jobs,jid)
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='blocked_ambiguous_or_invalid'
        with pytest.raises(SmartImportError):jobs.reconciliation.decide(pid,jid,choice(evidence,'adopt_verified_orphan'))
        jobs.reconciliation.decide(pid,jid,choice(evidence,'close_without_retry'))
        with jobs.catalog._db() as db,pytest.raises(Exception):
            db.execute('DELETE FROM project_publication_reviews WHERE job_id=?',(jid,))
    finally:runtime.close()


def test_enterprise_review_is_project_admin_only_and_csrf_scoped(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from timepilot.api import create_app
    from timepilot.enterprise_security import EnterpriseSecurity
    security=EnterpriseSecurity(tmp_path)
    security.bootstrap_tenant('factory-a','Factory A','root','Root-Long-Secret-2026')
    security.bootstrap_tenant('factory-b','Factory B','other','Other-Long-Secret-2026')
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE','0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE','1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL','https://testserver')
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app,base_url='https://testserver') as admin:
        assert admin.post('/api/v1/auth/login',json={
            'tenant':'factory-a','username':'root','password':'Root-Long-Secret-2026'}).status_code==200
        csrf=admin.cookies.get('tp_csrf')
        pid=admin.post('/api/v1/projects',json={'name':'复核测试'},headers={'X-TimePilot-CSRF':csrf}).json()['id']
        source=b'time,value\n2026-01-01,1\n2026-01-02,2\n'
        imported=admin.post(f'/api/v1/projects/{pid}/imports',
             files=[('files',('factory.csv',source))],headers={'X-TimePilot-CSRF':csrf})
        assert imported.status_code==201, imported.text
        iid=imported.json()['import_id']
        cid=admin.get(f'/api/v1/projects/{pid}/imports/{iid}').json()['candidates'][0]['candidate_id']
        queued=admin.post(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-jobs',
                   json={'name':'回归','confirm':True},headers={'X-TimePilot-CSRF':csrf})
        assert queued.status_code==202,queued.text
        jid=queued.json()['job_id']
        mark_uncertain(app.state.project_publication_jobs,jid)
        endpoint=f'/api/v1/projects/{pid}/publish-jobs/{jid}/reconciliation'
        assert admin.get(endpoint).status_code==200
        payload=choice(admin.get(endpoint).json(),'close_without_retry').model_dump()
        created=admin.post('/api/v1/identity/users',json={
            'username':'viewer','password':'Viewer-Long-Secret-2026'},headers={'X-TimePilot-CSRF':csrf})
        uid=created.json()['user_id']
        assert admin.post(f'/api/v1/identity/projects/{pid}/members',
             json={'user_id':uid,'role':'viewer'},headers={'X-TimePilot-CSRF':csrf}).status_code==201
        with TestClient(app,base_url='https://testserver') as viewer:
            viewer.post('/api/v1/auth/login',json={
                'tenant':'factory-a','username':'viewer','password':'Viewer-Long-Secret-2026'})
            assert viewer.get(endpoint).status_code==200
            assert viewer.post(endpoint+'/decide',json=payload,
                headers={'X-TimePilot-CSRF':viewer.cookies.get('tp_csrf')}).status_code==403
        with TestClient(app,base_url='https://testserver') as other:
            other.post('/api/v1/auth/login',json={
                'tenant':'factory-b','username':'other','password':'Other-Long-Secret-2026'})
            assert other.get(endpoint).status_code==404
        assert admin.post(endpoint+'/decide',json=payload).status_code==403
        assert admin.post(endpoint+'/decide',json=payload,
                          headers={'X-TimePilot-CSRF':csrf}).status_code==200
        assert admin.get(endpoint+'/history').json()['chain_valid']


def test_partial_project_link_is_repaired_without_duplicate_native_import(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        meta=jobs.service.generic.publish(iid,cid,request)
        aid=str(uuid4())
        native.docs[aid]={'lineage':{'project_id':pid,'import_id':iid,'candidate_id':cid,
          'version_id':meta['version_id'],'source_sha256':meta['source_sha256'],
          'data_sha256':meta['data_sha256']},'row_count':meta['row_count'],
          'sha256':'c'*64,'valid':True}
        jobs.catalog.attach_generic(pid,iid,cid,meta['version_id'],aid)
        mark_uncertain(jobs,jid)
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='verified_incomplete_link'
        jobs.service.platform.evaluate_quality=lambda *a,**kw: {'status':'passed'}
        completed=jobs.reconciliation.decide(pid,jid,choice(evidence,'adopt_verified_orphan'))
        assert completed['job']['status']=='succeeded'
        assert native.writes==0
        assert jobs.reconciliation.inspect(pid,jid)['classification']=='verified_complete'
    finally:runtime.close()


def test_missing_or_modified_raw_source_still_allows_audited_closure(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        manifest=jobs.catalog.imports.manifest(iid)
        source=jobs.catalog.imports._dir(iid)/manifest['files'][0]['stored']
        source.write_text('time,temperature\nMODIFIED,999\n',encoding='utf-8')
        mark_uncertain(jobs,jid)
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='blocked_local_artifact'
        assert evidence['local']['status']=='invalid'
        assert jobs.reconciliation.decide(pid,jid,choice(evidence,'close_without_retry'))['job']['status']=='cancelled'
        # A closed incident cannot be bypassed by re-enqueuing the same region.
        with pytest.raises(SmartImportError):jobs.submit(pid,iid,cid,request)
    finally:runtime.close()


def test_native_metadata_discovery_includes_unregistered_parquet_artifacts(tmp_path):
    """The absence decision must check metadata AND crash-leftover files."""
    from timepilot.api import create_app
    from timepilot.db import NativeBIAssetRow
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    pid=str(uuid4());iid=str(uuid4());cid='a'*20;vid='b'*24
    assert native.find_published_candidates(pid,iid,cid,vid)['asset_ids']==[]
    aid=str(uuid4())
    lineage={'project_id':pid,'import_id':iid,'candidate_id':cid,'version_id':vid}
    native._persist_asset_metadata(NativeBIAssetRow(
        id=aid,name='created',row_count=2,columns_json='[]',
        lineage_json=json.dumps(lineage),sha256='d'*64,file_bytes=10))
    report=native.find_published_candidates(pid,iid,cid,vid)
    assert report['complete'] and report['asset_ids']==[aid] and report['unregistered_snapshots']==0
    orphan=tmp_path/'native_bi'/str(uuid4())
    # Use the actual configured storage root, never a guessed /tmp path.
    orphan=native.home/str(uuid4())
    orphan.mkdir(parents=True)
    (orphan/'data.parquet').write_bytes(b'partial native write')
    report=native.find_published_candidates(pid,iid,cid,vid)
    assert report['unregistered_snapshots']==1


def test_unknown_unregistered_snapshot_blocks_new_native_write(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        native.unregistered=1
        with pytest.raises(SmartImportError, match='孤立快照'):
            jobs.service.publish(pid,iid,cid,request)
        assert native.writes==0
        assert jobs.catalog.generic_for(pid)==[]
    finally:runtime.close()


def test_incomplete_inventory_cannot_confirm_even_valid_linked_snapshot(tmp_path):
    runtime,pid,iid,cid,jid,jobs,native,request=create_case(tmp_path)
    try:
        meta=jobs.service.generic.publish(iid,cid,request)
        aid=str(uuid4())
        native.docs[aid]={'lineage':{'project_id':pid,'import_id':iid,'candidate_id':cid,
            'version_id':meta['version_id'],'source_sha256':meta['source_sha256'],
            'data_sha256':meta['data_sha256']},'row_count':meta['row_count'],
            'sha256':'d'*64,'valid':True}
        jobs.catalog.attach_generic(pid,iid,cid,meta['version_id'],aid)
        jobs.service.platform.register(pid,{'import_id':iid,'candidate_id':cid,
            'version_id':meta['version_id'],'native_asset_id':aid},meta)
        mark_uncertain(jobs,jid)
        native.unregistered=1
        evidence=jobs.reconciliation.inspect(pid,jid)
        assert evidence['classification']=='blocked_inventory_incomplete'
        assert 'confirm_complete' not in evidence['allowed_actions']
        with pytest.raises(SmartImportError):
            jobs.reconciliation.decide(pid,jid,choice(evidence,'confirm_complete'))
        assert jobs.get(pid,jid)['reconciliation_required']
    finally:runtime.close()


def test_human_decision_rejects_whitespace_only_reason():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ReconciliationDecision(decision_id=str(uuid4()), action='close_without_retry',
            evidence_sha256='f'*64, reason=' '*24,confirm=True)
