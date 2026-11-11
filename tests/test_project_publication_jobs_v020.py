"""Worker guarantees: queue progress, no partial publish, retries and project isolation."""
from __future__ import annotations
import csv
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from timepilot.api import create_app


def _app(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    samples={}
    def imported(file,name,lineage):
        aid=str(uuid4())
        with Path(file).open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f)
            rows=list(reader);fields=reader.fieldnames
        meta={'asset_id':aid,'name':name,'row_count':len(rows),
              'sha256':lineage['data_sha256'],'columns':[{'name':x} for x in fields],
              'lineage':lineage}
        samples[aid]=(meta,rows)
        return meta
    native.import_from_path=imported
    native.asset=lambda aid:samples[aid][0]
    native._version=lambda aid,version: {'version_id':version}
    native.query=lambda spec:{'total':len(samples[spec.asset_id][1])}
    return app,samples


def _upload(c,pid):
    rows='time,device,temp\n'
    rows+='\n'.join(f'2026-11-10 08:{i:02d},A,{20+i}' for i in range(10))
    response=c.post(f'/api/v1/projects/{pid}/imports',files=[('files',('sensor.csv',rows.encode()))])
    assert response.status_code==201,response.text
    iid=response.json()['import_id']
    cid=c.get(f'/api/v1/projects/{pid}/imports/{iid}').json()['candidates'][0]['candidate_id']
    return iid,cid


def test_async_publishing_checkpoint_and_status(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'生产线数据'}).json()['id']
        iid,cid=_upload(c,pid)
        uri=f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-jobs'
        payload={'name':'温度记录','roles':{'time':'time','entity':'device'},'confirm':True}
        initial=c.post(uri,json=payload)
        assert initial.status_code==202,initial.text
        job=initial.json()
        assert job['status']=='queued'
        assert c.post(uri,json=payload).json()['job_id']==job['job_id']
        checkpoint=c.get(f'/api/v1/projects/{pid}/publish-jobs/{job["job_id"]}/checkpoint')
        assert checkpoint.status_code==200
        done=app.state.project_publication_jobs.work_once()
        assert done['status']=='succeeded',done
        assert done['stage']=='complete'
        assert done['result']['native_asset_id']
        catalog=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets']
        assert len(catalog)==1
        assert catalog[0]['quality_status']=='passed'
        other=c.post('/api/v1/projects',json={'name':'隔离项目'}).json()['id']
        assert c.get(f'/api/v1/projects/{other}/publish-jobs/{job["job_id"]}').status_code==404
        assert c.get(f'/api/v1/projects/{other}/catalog/assets/{catalog[0]["asset_id"]}/versions').status_code==404


def test_publish_failure_durable_retry_and_rename(tmp_path):
    app,samples=_app(tmp_path)
    native=app.state.native_bi
    original=native.import_from_path
    counter={'attempt':0}
    def sometimes_fail(*args,**kwargs):
        counter['attempt']+=1
        if counter['attempt']==1:
            raise OSError('simulated Parquet file system failure')
        return original(*args,**kwargs)
    native.import_from_path=sometimes_fail
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'断点续发'}).json()['id']
        iid,cid=_upload(c,pid)
        uri=f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-jobs'
        payload={'name':'原始记录','roles':{},'confirm':True}
        job=c.post(uri,json=payload).json()
        error=app.state.project_publication_jobs.work_once()
        assert error['status']=='failed' and 'simulated' in error['error']
        assert c.get(f'/api/v1/projects/{pid}/data-catalog').json()['count']==0
        versions=c.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/versions').json()
        assert len(versions)==1
        retry=c.post(f'/api/v1/projects/{pid}/publish-jobs/{job["job_id"]}/retry')
        assert retry.status_code==200 and retry.json()['status']=='queued'
        done=app.state.project_publication_jobs.work_once()
        assert done['status']=='succeeded'
        assert counter['attempt']==2
        catalog=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets']
        version_id=catalog[0]['version_id'];aid=catalog[0]['asset_id']
        # A renamed publish of identical source and roles reuses content version.
        renamed=c.post(uri,json={**payload,'name':'温度数据'}).json()
        assert renamed['status']=='queued'
        final=app.state.project_publication_jobs.work_once()
        assert final['status']=='succeeded'
        listings=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets']
        assert len(listings)==1 and listings[0]['version_id']==version_id
        assert listings[0]['asset_id']==aid and listings[0]['name']=='温度数据'
        assert len(samples)==1
