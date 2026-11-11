"""Persistent, restart-safe project upload acceptance (zero new runtime env)."""
import io
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.project_import_jobs import ProjectImportJobs


def xlsx():
    book=Workbook();s=book.active;s.title='说明';s.append(['说明','报表']);
    s=book.create_sheet('数据');s.append(['Time','A2','A14'])
    s.append(['2024-01-01','m1',11]);s.append(['2024-02-01','m1',12]);
    out=io.BytesIO();book.save(out);return out.getvalue()


def test_job_is_queued_then_scanned_and_is_project_scoped(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        pa=c.post('/api/v1/projects',json={'name':'甲'}).json()['id']
        pb=c.post('/api/v1/projects',json={'name':'乙'}).json()['id']
        base=f'/api/v1/projects/{pa}/imports/jobs'
        r=c.post(base,files=[('files',('测试数据.xlsx',xlsx(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))]);assert r.status_code==202,r.text
        task=r.json();assert task['status']=='queued';assert not task['import_id']
        assert c.get(f'/api/v1/projects/{pb}/imports/jobs/{task["job_id"]}').status_code==404
        result=app.state.project_import_jobs.work_once()
        assert result['status']=='succeeded',result
        status=c.get(base+'/'+task['job_id']).json()
        assert status['import_id']==task['job_id']
        assert c.get(f'/api/v1/projects/{pa}/imports/{task["job_id"]}').status_code==200
        assert c.get(f'/api/v1/projects/{pb}/imports/{task["job_id"]}').status_code==404
        assert c.get(f'/api/v1/projects/{pa}/tasks').json()['upload_jobs'][0]['status']=='succeeded'
        assert not list((tmp_path/'projects').rglob('*.csv'))


def test_cancel_and_sha_guard(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        p=c.post('/api/v1/projects',json={'name':'测试'}).json()['id'];root=f'/api/v1/projects/{p}/imports/jobs'
        data=xlsx()
        cancel=c.post(root,files=[('files',('test.xlsx',data))]).json()
        assert c.post(root+'/'+cancel['job_id']+'/cancel').json()['status']=='cancelled'
        job=c.post(root,files=[('files',('test.xlsx',data))]).json()
        uploaded=tmp_path/'projects'/'import_uploads'/job['job_id']/'0000.xlsx'
        uploaded.write_bytes(data+b'altered')
        result=app.state.project_import_jobs.work_once()
        assert result['status']=='failed' and ('哈希' in result['error'] or '大小' in result['error'])
        assert c.post(root+'/'+job['job_id']+'/retry').json()['status']=='queued'
        assert app.state.project_import_jobs.work_once()['status']=='failed'


def test_restart_reconciles_immutable_import(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        p=c.post('/api/v1/projects',json={'name':'恢复'}).json()['id'];root=f'/api/v1/projects/{p}/imports/jobs'
        job=c.post(root,files=[('files',('test.xlsx',xlsx()))]).json()
    # Force simulated worker crash after manifest commit but before DB status.
    stage=tmp_path/'projects'/'import_uploads'/job['job_id']/'0000.xlsx'
    with stage.open('rb') as stream:
        app.state.smart_import.ingest(stream,'test.xlsx',import_id=job['job_id'])
    other=create_app(home=tmp_path,embedded_worker=False)
    assert other.state.project_import_jobs.work_once()['status']=='succeeded'
    assert len(other.state.smart_import.list_imports())==1
    assert len(other.state.projects.imports_for(p))==1


def test_archive_is_blocked_while_upload_scan_pending(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        p=c.post('/api/v1/projects',json={'name':'不可中断项目'}).json()['id']
        base=f'/api/v1/projects/{p}/imports/jobs'
        job=c.post(base,files=[('files',('data.xlsx',xlsx()))]).json()
        response=c.post(f'/api/v1/projects/{p}/archive')
        assert response.status_code==422
        c.post(base+'/'+job['job_id']+'/cancel')
        assert c.post(f'/api/v1/projects/{p}/archive').status_code==200


def test_project_plan_history_is_persistent_and_private(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        p=c.post('/api/v1/projects',json={'name':'有历史方案'}).json()['id']
        other=c.post('/api/v1/projects',json={'name':'没有权限'}).json()['id']
        path=f'/api/v1/projects/{p}/imports'
        iid=c.post(path,files=[('files',('a.xlsx',xlsx()))]).json()['import_id']
        info=c.get(path+'/'+iid).json()
        result=c.post(path+'/'+iid+'/plans',json={'candidate_ids':info['suggested_candidate_ids'],'name':'业务字段确认'})
        assert result.status_code==201,result.text
        history=c.get(path+'/'+iid+'/plans')
        assert history.status_code==200 and history.json()[0]['plan_id']==result.json()['plan_id']
        assert c.get(f'/api/v1/projects/{other}/imports/{iid}/plans').status_code==404
