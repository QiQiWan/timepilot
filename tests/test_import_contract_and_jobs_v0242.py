"""Real SQLite lease recovery and source-evidenced Region review contracts."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_universal_import_v0221 import _app, _make_project, _upload
from timepilot.import_summary import ImportSummaryService


def test_summary_reclaims_stale_lease_resumes_cursor_and_cancel(tmp_path):
    app,_ = _app(tmp_path)
    with TestClient(app) as client:
        pid=_make_project(client)
        iid,tree=_upload(client,pid)
        regions=tree['files'][0]['worksheets'][0]['regions']
        ids=[x['region_id'] for x in regions]
        service=ImportSummaryService(app.state.projects)
        # Test scheduler state without running a background thread.
        submitted=[]
        service.pool.submit=lambda *args: submitted.append(args)
        job=service.submit_job(pid,iid,region_ids=ids)
        jid=job['job_id']
        assert job['status']=='running' and job['attempts']==1
        # Crash after exactly one Region has completed.
        service.summarize_region(pid,iid,ids[0])
        with app.state.projects._db() as db:
            db.execute("UPDATE import_summary_jobs SET completed=1,lease_owner='dead-process',lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=?",(jid,))
        with patch.object(ImportSummaryService,'recover_jobs',lambda self:0):
            restarted=ImportSummaryService(app.state.projects)
        restarted.pool.submit=lambda *args: submitted.append(args)
        assert restarted.recover_jobs()>=1
        assert restarted.job(pid,iid,jid)['attempts']==2
        restarted._run_job(pid,iid,jid)
        done=restarted.job(pid,iid,jid)
        assert done['status']=='succeeded' and done['completed']==len(ids)
        assert done['lease_owner'] is None
        second=restarted.submit_job(pid,iid,region_ids=ids)
        assert second['status']=='running'
        cancelled=restarted.cancel_job(pid,iid,second['job_id'])
        assert cancelled['status']=='cancelled'
        restarted._run_job(pid,iid,second['job_id'])
        assert restarted.job(pid,iid,second['job_id'])['status']=='cancelled'
        service.pool.shutdown(wait=False)
        restarted.pool.shutdown(wait=False)


def test_contract_preserves_source_and_enforces_region_review(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as client:
        pid=_make_project(client)
        iid,tree=_upload(client,pid)
        r=tree['files'][0]['worksheets'][0]['regions'][0]
        rid=r['region_id']
        endpoint=f'/api/v1/projects/{pid}/imports/{iid}/regions/{rid}'
        contract=client.get(endpoint+'/contract')
        assert contract.status_code==200,contract.text
        data=contract.json()
        assert data['source_sha256']
        assert data['fields'][0]['source_name']=='Batch'
        assert data['fields'][0]['source_cell']=='B2'
        assert data['fields'][0]['physical_type_scope']=='sample_inferred'
        assert data['fields'][0]['semantic_status']=='unconfirmed'
        assert client.post(endpoint+'/review',json={'decision':'accepted','confirmed':False}).status_code==422
        yes=client.post(endpoint+'/review',json={'decision':'accepted','confirmed':True})
        assert yes.status_code==200,yes.text
        assert client.get(endpoint+'/contract').json()['review']['decision']=='accepted'
        other=_make_project(client,'另一个项目')
        assert client.get(f'/api/v1/projects/{other}/imports/{iid}/regions/{rid}/contract').status_code==404
