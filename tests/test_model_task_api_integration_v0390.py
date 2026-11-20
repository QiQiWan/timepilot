"""Real app, project/catalog/CSV source, real SQLite revisioned model task API.

Only optional Parquet publishing is substituted, as in the existing v0.17
integration fixture; no database or modeling rules are mocked.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from timepilot.api import create_app
from test_modeling_workflow_v017 import prepare_bi


def test_v0390_real_project_publisher_to_task_review_and_source_pin(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app) as client:
        pid, other, aid, _, _ = prepare_bi(app, client)
        base = f'/api/v1/projects/{pid}/model-tasks'
        request = {
            'name':'Device temperature prediction', 'asset_id':aid, 'version_id':'base',
            'task_kind':'continuous_forecast',
            'target':{'column':'温度(℃)','horizon_steps':3},
            'features':[{'column':'气压','role':'observed_past'}],
            'time':{'event_time_column':'采集时间','entity_columns':['设备编号'],
                    'publication_lag_days':0, 'timezone':'Asia/Shanghai', 'frequency':'h'},
            'validation':{'kind':'rolling_origin','folds':3},
        }
        created=client.post(base,json=request)
        assert created.status_code == 201, created.text
        tid=created.json()['task_id']
        assert created.json()['spec']['asset']['source_sha256']
        preflight=client.post(base+'/'+tid+'/preflight')
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()['ready'],preflight.json()
        wrong_project=client.get(f'/api/v1/projects/{other}/model-tasks/{tid}')
        assert wrong_project.status_code == 404
        approved=client.post(base+'/'+tid+'/approve',json={
            'expected_revision':1,'confirm_target':True,
            'confirm_feature_availability':True,'confirm_split':True})
        assert approved.status_code == 200, approved.text
        assert approved.json()['status']=='approved'
        assert client.get(base+'/'+tid+'/events').status_code==200
        updated_spec={**request,'name':'Device temperature prediction revised'}
        changed=client.patch(base+'/'+tid,json={'expected_revision':1,'spec':updated_spec})
        assert changed.status_code == 200, changed.text
        assert changed.json()['approval_sha256'] is None
        assert client.patch(base+'/'+tid,json={'expected_revision':1,'spec':updated_spec}).status_code==409
