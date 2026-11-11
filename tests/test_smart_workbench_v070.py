"""v0.7.0: BI multi-dimensional analysis, saved views, forecast draft and lineage."""
from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.smart_import import SmartImportService, TransformRecipe
from timepilot.smart_workbench import SmartWorkbench, ViewSelection, SaveView, DraftOptions


def data_file():
    wb=Workbook();ws=wb.active;ws.title='说明';ws.append(['导入说明'])
    ws=wb.create_sheet('车型月度明细')
    ws.append(['国家/地区','整车厂/品牌','车型','统计口径','来源','年份']+[f'{i}月' for i in range(1,13)])
    for year in [2022,2023,2024]:
        ws.append(['中国','比亚迪','汉','销量','source-A',year]+[year-2000+i*2 for i in range(1,13)])
    for year in [2023,2024]:
        ws.append(['美国','丰田','Camry','销量','source-B',year]+[year-2000+i for i in range(1,13)])
    ws.append(['中国','比亚迪','宋','产量','source-A',2024]+[10+i for i in range(12)])
    ws.append(['中国','比亚迪','海豚','销量','source-A',2024]+[None if i==7 else 4+i for i in range(12)])
    buf=io.BytesIO();wb.save(buf);return buf.getvalue()


def setup_service(tmp_path):
    svc=SmartImportService(tmp_path)
    source=svc.ingest(io.BytesIO(data_file()),'汽车销量.xlsx')
    version=svc.transform(source['import_id'],TransformRecipe(candidate_ids=source['suggested_candidate_ids']))
    wb=SmartWorkbench(svc)
    return wb,svc,source,version


def test_country_brand_vehicle_source_scope_cascading_and_coverage(tmp_path):
    w,svc,m,v=setup_service(tmp_path)
    id,vid=m['import_id'],v['version_id']
    all=w.analyze(id,vid,ViewSelection())
    assert all['summary']['rows']==84
    assert all['summary']['series']==4
    assert all['summary']['missing']==1
    assert all['summary']['calendar_coverage']==1.0
    assert all['summary']['coverage']==round(83/84,4)
    assert {x['value'] for x in all['facets']['country']['items']}=={'中国','美国'}
    assert len(all['trend'])==36
    sel=ViewSelection(filters={'country':['中国'],'brand':['比亚迪'],'vehicle':['汉'],
                               'source_name':['source-A'],'statistical_scope':['销量']})
    result=w.analyze(id,vid,sel)
    assert result['summary']['rows']==36
    assert result['summary']['series']==1
    assert result['summary']['missing']==0
    assert result['summary']['coverage']==1.0
    assert result['summary']['calendar_coverage']==1.0
    assert result['trend'][0]['month']=='2022-01'
    assert result['trend'][-1]['month']=='2024-12'
    assert result['samples'][0]['source_cell'].startswith('R')
    assert result['samples'][0]['source_file']=='汽车销量.xlsx'
    assert w.facet_search(id,vid,sel,'vehicle','汉')['items'][0]['value']=='汉'
    assert w.analyze(id,vid,ViewSelection(filters={'country':['中国']},date_from='2024-07',date_to='2024-09'))['summary']['rows']==9


def test_view_persistence_immutable_filter_and_source_provenance(tmp_path):
    w,svc,m,v=setup_service(tmp_path);iid,vid=m['import_id'],v['version_id']
    view=w.save_view(iid,vid,SaveView(name='汉 · 销量',selection=ViewSelection(filters={'vehicle':['汉']})))
    assert view['view_sha256']
    assert w.view(iid,vid,view['view_id'])['name']=='汉 · 销量'
    assert len(w.views(iid,vid))==1
    assert svc.version_file(iid,vid).is_file()
    path=svc._dir(iid)/'workbench'/vid/'views'/f"{view['view_id']}.json"
    damaged=json.loads(path.read_text(encoding='utf-8'));damaged['name']='篡改'
    path.write_text(json.dumps(damaged),encoding='utf-8')
    import pytest
    with pytest.raises(ValueError,match='修改'):
        w.view(iid,vid,view['view_id'])
    assert svc.version_file(iid,vid).is_file()


def test_draft_assumption_required_and_materialization_does_not_queue(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        created=client.post('/api/v1/smart-import/uploads',files={
            'file':('汽车销量.xlsx',data_file(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert created.status_code==201,created.text
        m=created.json();iid=m['import_id']
        changed=client.post(f'/api/v1/smart-import/imports/{iid}/transform',json={
            'candidate_ids':m['suggested_candidate_ids'],'name':'汽车月度'})
        assert changed.status_code==201,changed.text
        vid=changed.json()['version_id'];base=f'/api/v1/smart-import/imports/{iid}/versions/{vid}/workbench'
        query={'filters':{'country':['中国'],'brand':['比亚迪'],'vehicle':['汉'],
                          'source_name':['source-A'],'statistical_scope':['销量']}}
        analysis=client.post(base+'/analyze',json=query)
        assert analysis.status_code==200,analysis.text
        assert analysis.json()['summary']['rows']==36
        view=client.post(base+'/views',json={'name':'中国比亚迪汉月度销量','selection':query})
        assert view.status_code==201,view.text
        saved=view.json()
        draft1=client.post(base+f"/views/{saved['view_id']}/forecast-drafts",json={
            'name':'汉销量预测','forecast_horizon':3})
        assert draft1.status_code==201,draft1.text
        obj=draft1.json();assert obj['status']=='needs_review'
        assert obj['preflight']['reason_counts']['missing_available_at']==1
        assert client.post(base+f"/forecast-drafts/{obj['draft_id']}/materialize").status_code==422
        draft2=client.post(base+f"/views/{saved['view_id']}/forecast-drafts",json={
            'name':'汉销量预测','forecast_horizon':3,'publication_lag_days':35})
        assert draft2.status_code==201,draft2.text
        ready=draft2.json();assert ready['status']=='ready',ready
        assert ready['preflight']['eligible_series']==1
        assert ready['preflight']['matched_rows']==36
        produced=client.post(base+f"/forecast-drafts/{ready['draft_id']}/materialize")
        assert produced.status_code==201,produced.text
        obj=produced.json();assert obj['status']=='dataset_ready'
        assert obj['dataset_id']
        assert obj['task_spec']['approved'] is False
        assert client.get('/api/v1/runs').json()==[]
        assert client.post(base+f"/forecast-drafts/{ready['draft_id']}/materialize").json()['dataset_id']==obj['dataset_id']
        preview=client.get('/api/v1/datasets/'+obj['dataset_id']+'/preview')
        assert preview.status_code==200,preview.text
        assert preview.json()['manifest']['record_count']==36
        path=tmp_path/'datasets'/obj['dataset_id']/'smart_import_lineage.json'
        lineage=json.loads(path.read_text(encoding='utf-8'))
        assert lineage['view_sha256']==saved['view_sha256']
        assert lineage['publication_lag_days_assumption']==35
        assert lineage['model_training_started'] is False
        assert client.get(f'/imports/{iid}/versions/{vid}/workbench').status_code==200
        assert client.get('/assets/smart-workbench-ui.js').status_code==200


def test_explicit_exclude_invalid_series_and_mixed_sources_block(tmp_path):
    w,svc,m,v=setup_service(tmp_path);iid,vid=m['import_id'],v['version_id']
    sel=ViewSelection(filters={'country':['中国'],'source_name':['source-A'],'statistical_scope':['销量']})
    view=w.save_view(iid,vid,SaveView(name='中国销量',selection=sel))
    all_d=w.create_draft(iid,vid,view['view_id'],DraftOptions(publication_lag_days=35))
    assert all_d['status']=='needs_review'
    assert 'missing_target' in all_d['preflight']['reason_counts']
    eligible=w.create_draft(iid,vid,view['view_id'],DraftOptions(publication_lag_days=35,series_policy='eligible_only'))
    assert eligible['status']=='ready',eligible
    assert eligible['preflight']['eligible_series']==1
    assert eligible['preflight']['matched_series']==2
    broad=w.save_view(iid,vid,SaveView(name='所有销量',selection=ViewSelection(filters={'statistical_scope':['销量']})))
    ambiguous=w.create_draft(iid,vid,broad['view_id'],DraftOptions(publication_lag_days=35,series_policy='eligible_only'))
    assert any('来源' in x for x in ambiguous['preflight']['blockers'])


def test_user_filters_are_whitelisted_and_invalid_ids_rejected(tmp_path):
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError): ViewSelection(filters={'SQL; DROP':['x']})
    with pytest.raises(ValidationError): ViewSelection(date_from='2023-99')
    w,svc,m,v=setup_service(tmp_path)
    with pytest.raises(ValueError): w.facet_search(m['import_id'],v['version_id'],ViewSelection(),'unsafe;drop')
    with pytest.raises(ValueError): w.view(m['import_id'],v['version_id'],'../bad')


def test_async_index_progress_http(tmp_path):
    import time
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        up=client.post('/api/v1/smart-import/uploads',files={'file':('demo.xlsx',data_file(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()
        vid=client.post(f"/api/v1/smart-import/imports/{up['import_id']}/transform",json={'candidate_ids':up['suggested_candidate_ids']}).json()['version_id']
        base=f"/api/v1/smart-import/imports/{up['import_id']}/versions/{vid}/workbench"
        assert client.get(base+'/index-status').json()['status']=='unbuilt'
        status=client.post(base+'/build-index')
        assert status.status_code==202,status.text
        for _ in range(80):
            status=client.get(base+'/index-status').json()
            if status['status'] in ('ready','failed'):break
            time.sleep(.05)
        assert status['status']=='ready',status
        assert status['indexed_rows']==84
        assert client.post(base+'/build-index').json()['status']=='ready'
        assert client.post(base+'/facets/vehicle?q=汉',json={'filters':{}}).json()['items'][0]['value']=='汉'
