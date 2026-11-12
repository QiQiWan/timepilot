"""v0.15: resumable XLSX sheets, scoped BI views and forecast provenance."""
import io
import json
import sqlite3
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.data_assets import asset_id_for
from timepilot.import_checkpoints import WorksheetCheckpoints
from timepilot.smart_import import TransformRecipe


def sample_book():
    wb=Workbook()
    for index in range(4):
        sheet=wb.active if index==0 else wb.create_sheet()
        sheet.title=f'Sheet_{index}'
        sheet.append(['Time','A2','A14','available_at'])
        for month in range(1,15):
            y=2024+(month-1)//12;m=(month-1)%12+1
            sheet.append([f'{y}-{m:02d}-01','A',month, f'{y}-{m:02d}-03'])
    stream=io.BytesIO();wb.save(stream)
    return stream.getvalue()


def source_csv():
    lines=['Time,A2,A14,available_at']
    for m in range(1,14):
        year=2024+(m-1)//12;month=(m-1)%12+1
        lines.append(f'{year}-{month:02d}-01,car,12,{year}-{month:02d}-04')
    return ('\n'.join(lines)+'\n').encode()


def test_checkpoint_after_first_sheet_then_retry_reuses_only_valid_sheets(tmp_path,monkeypatch):
    app=create_app(home=tmp_path,embedded_worker=False)
    original=WorksheetCheckpoints.put
    state={'saves':0}

    def broken(self,**kwargs):
        state['saves']+=1
        if state['saves']==2:
            raise OSError('模拟第二张表扫描前掉电')
        return original(self,**kwargs)

    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'大工作簿'}).json()['id']
        root=f'/api/v1/projects/{pid}/imports/jobs'
        result=client.post(root,files=[('files',('many.xlsx',sample_book()))])
        jid=result.json()['job_id']
        monkeypatch.setattr(WorksheetCheckpoints,'put',broken)
        first=app.state.project_import_jobs.work_once()
        assert first['status']=='failed'
        checkpoint=client.get(root+'/'+jid+'/checkpoints')
        assert checkpoint.status_code==200
        assert len(checkpoint.json()['sheets'])==1
        assert checkpoint.json()['sheets'][0]['sheet']=='Sheet_0'
        monkeypatch.setattr(WorksheetCheckpoints,'put',original)
        assert client.post(root+'/'+jid+'/retry').status_code==200
        second=app.state.project_import_jobs.work_once()
        assert second['status']=='succeeded',second
        job=client.get(root+'/'+jid).json()
        assert job['completed_sheets']==4
        assert job['total_sheets']==4
        assert job['progress_percent']==100
        assert job['reused_sheets']==1
        manifest=app.state.smart_import.manifest(jid)
        assert len(manifest['candidates'])==4
        assert {x['sheet'] for x in manifest['candidates']}=={f'Sheet_{i}' for i in range(4)}
        assert not (tmp_path/'projects'/'import_uploads'/jid).exists()


def test_corrupt_sheet_checkpoint_is_not_trusted(tmp_path,monkeypatch):
    app=create_app(home=tmp_path,embedded_worker=False)
    original=WorksheetCheckpoints.put
    count={'num':0}

    def fail_after_one(self,**kwargs):
        count['num']+=1
        if count['num']==2:
            raise OSError('stop here')
        return original(self,**kwargs)

    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'校验缓存'}).json()['id']
        root=f'/api/v1/projects/{pid}/imports/jobs'
        jid=client.post(root,files=[('files',('many.xlsx',sample_book()))]).json()['job_id']
        monkeypatch.setattr(WorksheetCheckpoints,'put',fail_after_one)
        assert app.state.project_import_jobs.work_once()['status']=='failed'
        folder=tmp_path/'projects'/'import_uploads'/jid/'checkpoints'
        target=next(folder.glob('*.json'))
        payload=json.loads(target.read_text())
        payload['payload']['candidates'][0]['columns']=['malicious']
        target.write_text(json.dumps(payload))
        monkeypatch.setattr(WorksheetCheckpoints,'put',original)
        assert client.get(root+'/'+jid+'/checkpoints').json()['sheets']==[]
        client.post(root+'/'+jid+'/retry')
        assert app.state.project_import_jobs.work_once()['status']=='succeeded'
        state=client.get(root+'/'+jid).json()
        assert state['reused_sheets']==0
        assert 'malicious' not in str(app.state.smart_import.manifest(jid))


def test_lease_heartbeat_and_exhausted_crash_are_durable(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    jobs=app.state.project_import_jobs
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'租约核验'}).json()['id']
        jid=client.post(f'/api/v1/projects/{pid}/imports/jobs',files=[
            ('files',('work.xlsx',sample_book()))]).json()['job_id']
    claim=jobs._claim()
    assert claim['job_id']==jid
    owner=claim['lease_owner']
    with jobs.catalog._db() as db:
        before=db.execute('SELECT lease_until FROM project_import_jobs WHERE job_id=?',(jid,)).fetchone()['lease_until']
    time.sleep(.01)
    assert jobs._heartbeat(jid,owner)
    with jobs.catalog._db() as db:
        after=db.execute('SELECT lease_until FROM project_import_jobs WHERE job_id=?',(jid,)).fetchone()['lease_until']
    assert after>before
    assert not jobs._heartbeat(jid,'wrong-owner')
    with jobs.catalog._db() as db:
        db.execute("UPDATE project_import_jobs SET attempts=3,lease_until=? WHERE job_id=?",
                   (time.time()-1,jid))
    assert jobs._claim() is None
    assert jobs.get(pid,jid)['status']=='failed'
    assert jobs.get(pid,jid)['error']


def test_scoped_view_revision_draft_and_model_lineage(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    imports=app.state.smart_import
    source=imports.ingest(io.BytesIO(source_csv()),'data.csv')
    vid=imports.transform(source['import_id'],TransformRecipe(
        candidate_ids=source['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14'}))['version_id']
    aid=asset_id_for(source['import_id'])
    with TestClient(app) as client:
        first=client.post('/api/v1/projects',json={'name':'预测项目'}).json()['id']
        other=client.post('/api/v1/projects',json={'name':'无关项目'}).json()['id']
        root=f'/api/v1/projects/{first}'
        link=client.post(root+'/assets',json={'asset_id':aid,'version_id':vid})
        assert link.status_code==201,link.text
        workspace={'panels':{'1':{'table':'timepilot_active','plugin':'Datagrid'}},'derived_tables':{}}
        save=client.post(root+'/assets/'+aid+'/views',json={
            'name':'本月销量','selection':{'filters':{}},
            'perspective_workspace':workspace,'base_table_name':'timepilot_active'})
        assert save.status_code==201,save.text
        view=save.json();assert view['version_id']==vid
        assert client.get(root+'/assets/'+aid+'/views/'+view['view_id']).json()['view_sha256']==view['view_sha256']
        assert client.get(f'/api/v1/projects/{other}/assets/{aid}/views/{view["view_id"]}').status_code==404
        # Sharing the same source asset never shares project-specific views.
        assert client.post(f'/api/v1/projects/{other}/assets',json={'asset_id':aid,'version_id':vid}).status_code==201
        assert client.get(f'/api/v1/projects/{other}/assets/{aid}/views').json()==[]
        bad=client.post(root+'/assets/'+aid+'/views',json={'name':'超大工作区',
            'selection':{},'perspective_workspace':{'panels':{'x':'Y'*66000}}})
        assert bad.status_code==422
        draft=client.post(root+'/assets/'+aid+'/views/'+view['view_id']+'/forecast-drafts',json={
            'name':'月度销量预测','forecast_horizon':2,'validation_windows':2,
            'min_history':6,'model_candidates':['seasonal_naive']})
        assert draft.status_code==201,draft.text
        item=draft.json();assert item['view_id']==view['view_id']
        assert client.get(f'/api/v1/projects/{other}/forecast-drafts/{item["draft_id"]}').status_code==404
        assert len(client.get(root+'/forecast-drafts').json())==1
        assert client.get(root+'/models').status_code==200
        assert client.get(root+'/tasks').json()['forecast_drafts'][0]['draft_id']==item['draft_id']
        assert client.get(root).status_code==200
        approved=client.post(root+'/assets/'+aid+'/views/'+view['view_id']+'/forecast-drafts',json={
            'name':'短期业务预测','forecast_horizon':1,'validation_windows':1,
            'min_history':2,'model_candidates':['moving_average']})
        assert approved.status_code==201,approved.text
        assert approved.json()['status']=='ready',approved.json()
        did=approved.json()['draft_id']
        prepared=client.post(root+'/forecast-drafts/'+did+'/materialize')
        assert prepared.status_code==201,prepared.text
        dataset_id=prepared.json()['dataset_id']
        assert prepared.json()['task_spec']['approved'] is False
        task={**prepared.json()['task_spec'],'approved':True}
        invalid=dict(task,forecast_horizon=2)
        rejected=client.post(root+'/runs/submit',json={
            'dataset':dataset_id,'task':invalid,'draft_id':did})
        assert rejected.status_code==422,rejected.text
        assert client.get('/api/v1/runs').json()==[]
        mismatch=client.post(f'/api/v1/projects/{other}/runs/submit',json={
            'dataset':dataset_id,'task':task,'draft_id':did})
        assert mismatch.status_code==404
        submitted=client.post(root+'/runs/submit',json={
            'dataset':dataset_id,'task':task,'draft_id':did})
        assert submitted.status_code==201,submitted.text
        rid=submitted.json()['run_id']
        assert submitted.json()['view_id']==view['view_id']
        lineage=client.get(root+'/models').json()
        assert len(lineage)==1 and lineage[0]['run_id']==rid
        assert lineage[0]['draft_id']==did and lineage[0]['view_id']==view['view_id']
        assert lineage[0]['asset_id']==aid and lineage[0]['version_id']==vid
        assert client.get(root+'/tasks').json()['runs'][0]['dataset_id']==dataset_id
    # Restarts may never dissolve the project-view association or change digest.
    app2=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app2) as client:
        after=client.get(f'/api/v1/projects/{first}/assets/{aid}/views/{view["view_id"]}')
        assert after.status_code==200 and after.json()['view_sha256']==view['view_sha256']
        assert client.get(f'/api/v1/projects/{other}/assets/{aid}/views').json()==[]
        assert client.get(f'/api/v1/projects/{first}/models').json()[0]['view_id']==view['view_id']


def test_old_schema_migrates_without_losing_legacy_links(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    db=tmp_path/'projects'/'catalog.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('UPDATE project_schema SET version=1')
    migrated=create_app(home=tmp_path,embedded_worker=False)
    with sqlite3.connect(db) as con:
        from timepilot.project_migrations import TARGET_SCHEMA
        assert con.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA
    assert migrated.state.projects.list()==app.state.projects.list()
