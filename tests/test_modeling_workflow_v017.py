"""v0.17 generic immutable source -> BI selection -> reviewed prediction regression.

When optional DuckDB/Perspective are not installed, only the BI adapter is
replaced. The project catalog, import, generic publish, modeling REST API,
normalizer, dataset store, task validation and runtime engine remain real.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.modeling_workflow import FieldMapping, ForecastPlan


def fixture_csv(*, cadence='h', points=105, gap=False):
    raw=io.StringIO(newline='')
    out=csv.writer(raw)
    out.writerow(['设备编号', '采集时间', '温度(℃)', '气压', '维护事件'])
    for dev in ('设备-A', '设备-B'):
        for i in range(points):
            step=i+(1 if gap and i>=30 and dev=='设备-A' else 0)
            dt=datetime(2026,3,1)+timedelta(hours=step if cadence=='h' else 0,
                                              minutes=step if cadence=='min' else 0)
            out.writerow([dev,dt.strftime('%Y-%m-%d %H:%M:%S'),round(23+math.sin(i/10)+.1*i,3),
                          101.5+i/100,'检修' if i==5 else ''])
    return raw.getvalue().encode('utf-8')


def prepare_bi(app, http, csv_content=None):
    # A deterministic adapter replaces only the optional Parquet import/view
    # access; business logic sees the same immutable publisher linkage.
    csv_content=csv_content or fixture_csv()
    native=app.state.native_bi
    data={};views={}
    def imported(path, name, lineage):
        with Path(path).open('r',encoding='utf-8',newline='') as input_file:
            reader=csv.DictReader(input_file)
            rows=list(reader)
            names=reader.fieldnames
        aid=str(uuid4())
        data[aid]={'asset_id':aid,'name':name,'row_count':len(rows),
                   'columns':[{'name':c,'physical_type':'VARCHAR'} for c in names],
                   'lineage':lineage,'sha256':str(lineage['data_sha256']), 'head_version':'base'}
        return data[aid]
    native.import_from_path=imported
    native.asset=lambda aid:data[aid]
    native.verify=lambda aid:{'asset_id':aid,'valid':True}
    native._version=lambda aid,vid:{'version_id':vid}
    native.get_view=lambda vid:views[vid]
    pid=http.post('/api/v1/projects',json={'name':'设备温度研究'}).json()['id']
    pid2=http.post('/api/v1/projects',json={'name':'隔离的项目'}).json()['id']
    upload=http.post(f'/api/v1/projects/{pid}/imports',files=[('files',('device.csv',csv_content))])
    assert upload.status_code==201,upload.text
    iid=upload.json()['import_id']
    cid=app.state.smart_import.manifest(iid)['candidates'][0]['candidate_id']
    published=http.post(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general',
                        json={'name':'设备运行表','confirm':True})
    assert published.status_code==201,published.text
    aid=published.json()['native_asset_id']
    return pid,pid2,aid,data,views


def create_plan(*,aid,view_id=None,filters=None,frequency='h',availability='declared_lag',lag=0):
    return {'asset_id':aid,'version_id':'base','view_id':view_id,'filters':filters,
            'name':'设备温度短期预测','confirm_scope':True,
            'mapping':{'time':'采集时间','entity':'设备编号','target':'温度(℃)',
                       'timezone':'Asia/Shanghai','available_at':None,'target_unit':'℃',
                       'availability_policy':availability,'publication_lag_days':lag},
            'plan':{'task_kind':'continuous_forecast','task_family':'equipment_state','frequency':frequency,
                    'horizon_steps':3,'validation_windows':3,'min_history':24,
                    'primary_metric':'MAE','model_candidates':['last_value','moving_average']}}


def _post(http,url,payload=None):
    result=http.post(url,json=payload) if payload is not None else http.post(url)
    assert result.status_code in (200,201),result.text
    return result.json()


def test_from_saved_bi_view_to_forecast_runtime_replay(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,views=prepare_bi(app,http)
        vid=str(uuid4())
        filters=[{'column':'设备编号','op':'eq','value':'设备-A'}]
        views[vid]={'view_id':vid,'asset_id':aid,'config':{'format':'timepilot-perspective-v2',
            'source':{'kind':'query','version_id':'base','query':{'asset_id':aid,'version_id':'base',
                'filters':filters,'limit':5,'offset':50}},'workspace':{'theme':'light'}}}
        base=f'/api/v1/projects/{pid}/modeling'
        proposed=http.get(base+f'/assets/{aid}/suggestions')
        assert proposed.status_code==200,proposed.text
        fields=proposed.json()['profile']['fields']
        assert [x['name'] for x in fields]==['设备编号','采集时间','温度(℃)','气压','维护事件']
        assert proposed.json()['profile']['recommendation']['time']=='采集时间'
        assert proposed.json()['profile']['recommendation']['target']=='温度(℃)'
        assert all('sample_rows' not in f for f in fields)
        executor=app.state.agent_executor
        profile=executor.registry.execute('modeling.profile.inspect',
            {'project_id':pid,'asset_id':aid},executor.services)
        assert profile['source_rows']==210
        proposal=executor.registry.execute('modeling.tasks.suggest',
            {'project_id':pid,'asset_id':aid},executor.services)
        assert proposal['proposals'][0]['task_kind']=='continuous_forecast'
        draft=_post(http,base+'/drafts',create_plan(aid=aid,view_id=vid))
        did=draft['draft_id']; endpoint=base+'/drafts/'+did
        blocked=http.get(f'/api/v1/projects/{other}/modeling/drafts/{did}')
        assert blocked.status_code==404
        assert http.get(f'/api/v1/projects/{other}/modeling/assets/{aid}/profile').status_code==404
        pf=_post(http,endpoint+'/preflight')
        assert pf['ready'],pf
        assert pf['source_rows']==210 and pf['selected_rows']==105
        assert pf['eligible_rows']==105 and pf['series_count']==1
        assert pf['evaluation']['no_random_shuffle'] is True
        assert pf['evaluation']['primary_metric']=='MAE'
        assert pf['evaluation']['target_unit']=='℃'
        assert pf['evaluation']['task_family']=='equipment_state'
        assert pf['time_coverage']['per_entity_min_rows']==105
        denied=http.post(endpoint+'/approve',json={'expected_revision':draft['revision'],'confirm':True})
        assert denied.status_code==422
        reviewed=_post(http,endpoint+'/approve',{'expected_revision':draft['revision'],
            'confirm':True,'acknowledge_single_target':True,'acknowledge_availability':True})
        assert reviewed['status']=='approved'
        assert app.state.modeling.inspect_preflight(pid,did)['ready']
        assert http.get(endpoint).json()['approval']==reviewed['approval']
        assert _post(http,endpoint+'/preflight')['ready']
        assert http.get(endpoint).json()['approval']==reviewed['approval']
        materialized=_post(http,endpoint+'/materialize')
        assert materialized['status']=='dataset_ready'
        assert materialized['dataset_id']
        from timepilot.data import get_dataset
        snapshot,_=get_dataset(app.state.projects.engine.store,app.state.projects.engine.settings,materialized['dataset_id'])
        assert snapshot.unit=='℃'
        assert _post(http,endpoint+'/materialize')['dataset_id']==materialized['dataset_id']
        assert http.patch(endpoint,json={'expected_revision':1,'name':'改名'}).status_code==422
        queued=_post(http,endpoint+'/submit')
        assert queued['status']=='queued' and queued['dataset_id']==materialized['dataset_id']
        assert _post(http,endpoint+'/submit')['run_id']==queued['run_id']
        catalog=http.get(f'/api/v1/projects/{pid}/models').json()
        run=next(x for x in catalog if x['run_id']==queued['run_id'])
        assert run['asset_id']==aid and run['view_id']==vid and run['draft_id']==did
        lineage=http.get(endpoint+'/lineage').json()
        assert lineage['filters']==filters
        assert lineage['run_id']==queued['run_id']
        assert lineage['materialization']['selected_input_sha256']
        rerun=_post(http,endpoint+'/reproduce')
        assert rerun['reproduction_of']==did and rerun['draft_id']!=did
        assert rerun['selection']['selection_hash']==draft['selection']['selection_hash']
        assert rerun['approval'] is None and rerun['dataset_id'] is None
        assert rerun['run_id'] is None
        # Run the real forecasting engine with baseline models.
        outcome=app.state.client.engine.work_once() if hasattr(app.state,'client') else app.state.projects.engine.work_once()
        run_live=next(x for x in app.state.projects.engine.store.list_runs() if x.id==queued['run_id'])
        assert run_live.status == 'succeeded', (run_live.status,outcome)
        cloned=base+'/drafts/'+rerun['draft_id']
        assert _post(http,cloned+'/preflight')['ready']
        _post(http,cloned+'/approve',{'expected_revision':1,
            'confirm':True,'acknowledge_single_target':True,'acknowledge_availability':True})
        _post(http,cloned+'/materialize')
        repeat=_post(http,cloned+'/submit')
        app.state.projects.engine.work_once()
        assert app.state.projects.engine.store.run(repeat['run_id']).status=='succeeded'
        import json
        a=json.loads((app.state.projects.engine.run_dir(queued['run_id'])/'stages/forecast_result.json').read_text())
        b=json.loads((app.state.projects.engine.run_dir(repeat['run_id'])/'stages/forecast_result.json').read_text())
        def values(result):
            return sorted((r['series_id'],r['model_id'],r['horizon_step'],r['mean'])
                          for r in result['test']+result['future'])
        assert values(a)==values(b)


def test_same_import_multiple_independent_slices_and_guarded_mutations(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,views=prepare_bi(app,http)
        base=f'/api/v1/projects/{pid}/modeling'
        d1=_post(http,base+'/drafts',create_plan(aid=aid,filters=[{'column':'设备编号','op':'eq','value':'设备-A'}]))
        d2=_post(http,base+'/drafts',create_plan(aid=aid,filters=[{'column':'设备编号','op':'eq','value':'设备-B'}]))
        assert d1['selection']['selection_hash']!=d2['selection']['selection_hash']
        assert len(http.get(base+'/drafts').json())==2
        assert _post(http,base+'/drafts/'+d1['draft_id']+'/preflight')['selected_rows']==105
        assert _post(http,base+'/drafts/'+d2['draft_id']+'/preflight')['selected_rows']==105
        assert http.post(base+'/drafts',json=create_plan(aid=aid,filters=[{'column':'not-there','op':'eq','value':'x'}])).status_code==422
        # A scoped view is hashed. Mutating layout or filters cannot silently repoint training.
        vid=str(uuid4());views[vid]={'view_id':vid,'asset_id':aid,'config':{'format':'timepilot-perspective-v2',
               'source':{'kind':'sample','version_id':'base','limit':8},'workspace':{'version':1}}}
        dv=_post(http,base+'/drafts',create_plan(aid=aid,view_id=vid))
        views[vid]['config']['workspace']['version']=2
        assert http.post(base+'/drafts/'+dv['draft_id']+'/preflight').status_code==422


def test_minute_data_sequence_index_and_hourly_gap_fail_closed(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,fixture_csv(cadence='min',points=105))
        base=f'/api/v1/projects/{pid}/modeling'
        d=_post(http,base+'/drafts',create_plan(aid=aid,frequency='min'))
        pre=_post(http,base+'/drafts/'+d['draft_id']+'/preflight')
        assert not pre['ready'] and any('未被现有预测引擎支持' in x for x in pre['blockers'])
        wrong=_post(http,base+'/drafts',create_plan(aid=aid,frequency='h'))
        pre=_post(http,base+'/drafts/'+wrong['draft_id']+'/preflight')
        assert not pre['ready'] and any('缺口或频率不匹配' in x for x in pre['blockers'])
    app2=create_app(home=tmp_path/'gap',embedded_worker=False)
    with TestClient(app2) as http:
        pid,_,aid,_,_=prepare_bi(app2,http,fixture_csv(points=105,gap=True))
        base=f'/api/v1/projects/{pid}/modeling'
        d=_post(http,base+'/drafts',create_plan(aid=aid))
        pre=_post(http,base+'/drafts/'+d['draft_id']+'/preflight')
        assert not pre['ready'] and any('缺口' in x for x in pre['blockers'])


def test_late_available_data_guard_future_leakage(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http)
        base=f'/api/v1/projects/{pid}/modeling'
        d=_post(http,base+'/drafts',create_plan(aid=aid,lag=1))
        pre=_post(http,base+'/drafts/'+d['draft_id']+'/preflight')
        assert not pre['ready'] and any('预测时刻' in x or '可用时间' in x for x in pre['blockers'])


def test_numeric_time_index_never_promoted_to_calendar_or_hour(tmp_path):
    content=('Time,A1,A2\n'+'\n'.join(f'{i},{15+i/10:.1f},{20+i/10:.1f}' for i in range(1,121))+'\n').encode('utf-8')
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,content)
        profile=http.get(f'/api/v1/projects/{pid}/modeling/assets/{aid}/profile').json()
        time=next(x for x in profile['fields'] if x['name']=='Time')
        assert any(r['role']=='sequence_step' for r in time['suggested_roles'])
        assert profile['recommendation']['time'] is None
        assert profile['recommendation']['target'] is None
        suggestions=http.get(f'/api/v1/projects/{pid}/modeling/assets/{aid}/suggestions').json()['proposals']
        assert suggestions and suggestions[0]['status']=='adapter_required'


def test_immutable_asset_checksum_mismatch_blocks_model(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http)
        base=f'/api/v1/projects/{pid}/modeling'
        draft=_post(http,base+'/drafts',create_plan(aid=aid))
        assert _post(http,base+'/drafts/'+draft['draft_id']+'/preflight')['ready']
        link=app.state.projects.require_generic(pid,aid)
        path=app.state.modeling.generic.version_file(link['import_id'],link['candidate_id'],link['version_id'])
        path.write_bytes(path.read_bytes()+b'\n')
        failure=http.post(base+'/drafts/'+draft['draft_id']+'/preflight')
        assert failure.status_code==422
        assert '完整性' in failure.text or 'checksum' in failure.text


def test_agent_modeling_contract_and_page_integration():
    from timepilot.agent_tools import default_registry
    from pathlib import Path
    registry=default_registry()
    profile=registry.get('modeling.profile.inspect')
    plan=registry.get('modeling.tasks.suggest')
    prepare=registry.get('modeling.draft.prepare')
    assert profile.permission=='read'
    assert plan.permission=='read'
    assert prepare.permission=='write_confirm'
    root=Path(__file__).resolve().parents[1]
    html=(root/'src/timepilot/static/index.html').read_text(encoding='utf-8')
    app=(root/'src/timepilot/static/app.js').read_text(encoding='utf-8')
    native=(root/'src/timepilot/static/native-bi.js').read_text(encoding='utf-8')
    projects=(root/'src/timepilot/static/project-ui.js').read_text(encoding='utf-8')
    assert '/assets/modeling-ui.js?v=0.22.1' in html
    assert '/assets/modeling-ui.css?v=0.22.1' in html
    assert 'TimePilotModeling?.click' in app and 'TimePilotModeling?.submit' in app
    assert 'data-native="open-modeling"' in native
    assert '/projects/'+"'" in native or 'ctx.projectId' in native
    assert 'reproduce-modeling-draft' in projects
