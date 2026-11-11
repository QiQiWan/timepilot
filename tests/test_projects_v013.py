"""Project-first API: migration, scoped access, intelligent import and BI linkage."""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.smart_import import TransformRecipe
from timepilot.data_assets import asset_id_for


def book():
    wb=Workbook();ws=wb.active;ws.title='说明';ws.append(['说明','数据'])
    ws=wb.create_sheet('车型月度明细')
    ws.append(['车型','年份','统计口径','1月','2月','3月'])
    ws.append(['Sedan',2024,'销量',10,12,14]);ws.append(['SUV',2024,'产量',20,None,18])
    with io.BytesIO() as b:
        wb.save(b);return b.getvalue()


def test_project_crud_soft_delete_and_isolation(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        a=c.post('/api/v1/projects',json={'name':'汽车预测','domain':'forecast'}).json()
        b=c.post('/api/v1/projects',json={'name':'设备预测','domain':'manufacturing'}).json()
        assert a['name']=='汽车预测' and a['status']=='active'
        assert c.patch('/api/v1/projects/'+a['id'],json={'description':'月度市场序列'}).json()['description']=='月度市场序列'
        assert c.get('/api/v1/projects/'+b['id']+'/assets').json()==[]
        assert c.post('/api/v1/projects/'+a['id']+'/archive').json()['status']=='archived'
        assert c.post('/api/v1/projects/'+a['id']+'/imports',files=[('files',('a.xlsx',book(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))]).status_code==422
        assert c.post('/api/v1/projects/'+a['id']+'/restore').json()['status']=='active'
        assert c.delete('/api/v1/projects/'+a['id']).json()['status']=='trash'
        assert not any(p['id']==a['id'] for p in c.get('/api/v1/projects').json())
        assert c.post('/api/v1/projects/'+a['id']+'/restore').json()['status']=='active'
        assert c.get('/projects/'+a['id']+'/data').status_code==200
        assert c.get('/api/v1/projects/non-existent').status_code in (404,422)


def test_project_import_plan_version_and_no_copy(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        a=c.post('/api/v1/projects',json={'name':'车辆销量预测'}).json()['id']
        b=c.post('/api/v1/projects',json={'name':'其它项目'}).json()['id']
        uri='/api/v1/projects/'+a
        up=c.post(uri+'/imports',files=[('files',('model.xlsx',book(),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))])
        assert up.status_code==201,up.text
        ident=up.json()['import_id']
        # v0.22: a header-only instructions sheet is an unassigned note,
        # not a publishable data table. The monthly data sheet remains.
        assert up.json()['candidate_count']==1
        assert c.get(uri+'/imports/'+ident+'/catalog').json()['unassigned_regions']
        assert c.get(uri+'/imports/'+ident).status_code==200
        assert c.get('/api/v1/projects/'+b+'/imports/'+ident).status_code==404
        info=c.get(uri+'/imports/'+ident).json()
        assert 'sample_rows' not in json.dumps(info)
        p=c.post(uri+'/imports/'+ident+'/plans',json={'candidate_ids':info['suggested_candidate_ids'],
            'name':'年度车型分析'}); assert p.status_code==201,p.text
        plan=p.json()
        assert plan['execution_policy']['auto_train'] is False
        approval={'plan_sha256':plan['plan_sha256'],'confirm':True,
                  'acknowledged_issues':[x['code'] for x in plan['issues']],
                  'scope_selection_mode':'keep_separate'}
        res=c.post(uri+'/imports/'+ident+'/plans/'+plan['plan_id']+'/execute',json=approval)
        assert res.status_code==202,res.text
        for _ in range(120):
            status=c.get(uri+'/imports/'+ident+'/plans/'+plan['plan_id']+'/execution')
            assert status.status_code==200,status.text
            if status.json()['status'] not in ('queued','running'):break
            time.sleep(.025)
        assert status.json()['status']=='completed',status.text
        aid=asset_id_for(ident)
        versions=c.get(uri+'/assets').json()
        assert len(versions)==1 and versions[0]['asset_id']==aid
        assert versions[0]['row_count']==6
        assert c.get('/api/v1/projects/'+b+'/assets/'+aid).status_code==404
        assert c.get(uri+'/assets/'+aid).status_code==200
        assert c.get('/projects/'+a+'/bi/'+aid).status_code==200
        source_path=app.state.smart_import.version_file(ident,versions[0]['version_id'])
        assert source_path.exists()
        # Project references are in SQLite; no duplicate CSV/Parquet materialization.
        assert not list((tmp_path/'projects').rglob('*.csv'))
        assert c.get('/api/v1/ready').status_code in (200,503)


def test_legacy_backfill_is_once_and_stable(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    service=app.state.smart_import
    result=service.ingest(io.BytesIO(book()),'old.xlsx')
    version=service.transform(result['import_id'],TransformRecipe(candidate_ids=result['suggested_candidate_ids']))
    # Clear marker to simulate a previous installation's initial migration.
    import sqlite3
    with sqlite3.connect(tmp_path/'projects'/'catalog.sqlite3') as db:
        db.execute("DELETE FROM project_settings WHERE key='legacy_backfill_v1'")
    restored=app.state.projects.backfill_legacy_once()
    assert restored['performed']
    again=app.state.projects.backfill_legacy_once()
    assert not again['performed']
    defaults=[p for p in app.state.projects.list() if p['domain']=='legacy']
    assert len(defaults)==1
    assert app.state.projects.assets_for(defaults[0]['id'])[0]['version_id']==version['version_id']
    assert service.version_file(result['import_id'],version['version_id']).is_file()


def test_project_frontend_is_single_entry_without_duplicate_import_grid():
    root=Path(__file__).resolve().parents[1]
    html=(root/'src/timepilot/static/index.html').read_text()
    ui=(root/'src/timepilot/static/project-ui.js').read_text()
    assert 'project-ui.js' in html and '项目管理' in html
    # The old forecast-specific mapping form is no longer in the default import UX.
    assert 'project-upload-form' in ui and 'publish-import-batch' in ui
    assert 'project-understand-form' not in ui
    assert 'mountPerspectiveRemote' not in ui  # use one native BI component
    assert 'src/timepilot' not in ui


def test_readiness_is_fail_closed_on_missing_offline_release(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as c:
        r=c.get('/api/v1/ready')
        if r.status_code==503:
            assert r.json()['detail']['component']=='perspective_bundle'
            assert 'repair' in r.json()['detail']
        else:
            assert r.json()['ready'] is True
