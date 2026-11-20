"""Synthetic API workflow/state audit using real project and SQLite services.

NativeBI file registration is a test double because the execution environment
may not have DuckDB/PyArrow. This test covers HTTP/SQLite state and permissions,
NOT physical Parquet or actual browser pixels.
"""
from __future__ import annotations
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_universal_import_v0221 import _app, _make_project, _upload
from timepilot.feature_analysis_journey import FeatureAnalysisJourney
from timepilot.feature_data_analysis import FeatureDataAnalysis
from timepilot.feature_auto_analysis import FeatureAutoAnalysis
from timepilot.featurelab361_fixtures import generate_suite


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    folder=tmp_path_factory.mktemp('featurelab362-api-data')
    generate_suite(folder)
    return folder


def _published(app,client,pid,contents,name):
    iid,catalog=_upload(client,pid,contents,name)
    regions=[r for s in catalog['files'][0]['worksheets'] for r in s['regions']]
    assert regions, {'name':name,'catalog':catalog}
    candidates=sorted(regions,key=lambda r:-int(r.get('row_count_estimate',0)))
    selection=candidates[0]
    endpoint=f'/api/v1/projects/{pid}/imports/{iid}/publish-batch'
    cmd={'region_ids':[selection['region_id']],'confirm':True}
    created=client.post(endpoint,json=cmd)
    assert created.status_code==202,created.text
    again=client.post(endpoint,json=cmd)
    assert again.status_code==202
    assert created.json()['batch_id']==again.json()['batch_id']
    worked=app.state.project_publication_jobs.work_once()
    assert worked['status']=='succeeded',worked
    result=client.get(f'/api/v1/projects/{pid}/publish-batches/{created.json()["batch_id"]}')
    assert result.status_code==200
    assert result.json()['status']=='succeeded'
    return result.json()['items'][0]['native_asset_id']


def test_synthetic_upload_publish_read_only_workspace_and_no_model_api(tmp_path,corpus,monkeypatch):
    app,saved=_app(tmp_path/'app-home')
    with TestClient(app) as client:
        pid=_make_project(client,'QA synthetic - exploratory')
        bytes_in=(corpus/'sensor_unlabeled.csv').read_bytes()
        aid=_published(app,client,pid,bytes_in,'sensor_unlabeled.csv')
        assert len(saved[aid][1])==1200
        # Native verify is mocked only for offline API testing; actual file
        # bytes / Parquet parity belongs to the separate physical gate.
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _: {'valid':True})
        auto=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs,models=None)
        journey=FeatureAnalysisJourney(FeatureDataAnalysis(app.state.projects,
            app.state.feature_semantic,app.state.feature_jobs,app.state.native_bi),auto)
        first=journey.start(pid,aid)
        second=journey.start(pid,aid)
        assert first['feature_preview']['session_id']==second['feature_preview']['session_id']
        assert second['auto_start']=='reused'
        assert second['requires_model_api'] is False
        assert 'materialization' in first['requires_human_confirmation_for']
        sid=first['feature_preview']['session_id']
        assert sid
        url=f'/api/v1/projects/{pid}/feature-lab/workspace'
        for _ in range(3):
            response=client.get(url,params={'asset_id':aid,'version_id':'base'})
            assert response.status_code==200,response.text
            body=response.json()
            assert body['asset_id']==aid and body['version_id']=='base'
            assert body['feature_preview']['session_id']==sid
            assert body['feature_preview']['status']=='profiling'
        with app.state.projects._db() as db:
            count=db.execute('SELECT COUNT(*) FROM feature_analysis_sessions WHERE project_id=?',(pid,)).fetchone()[0]
            runs=db.execute('SELECT COUNT(*) FROM feature_runs WHERE project_id=?',(pid,)).fetchone()[0]
            evaluations=db.execute('SELECT COUNT(*) FROM feature_evaluations WHERE project_id=?',(pid,)).fetchone()[0]
        assert (count,runs,evaluations)==(1,0,0)
        other=_make_project(client,'QA isolated tenant')
        assert client.get(f'/api/v1/projects/{other}/feature-lab/workspace',
            params={'asset_id':aid}).status_code in (403,404,422)
        assert client.get(f'/api/v1/projects/{other}/feature-lab/analysis/sessions/{sid}').status_code in (403,404)
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _: {'valid':False})
        with pytest.raises(ValueError,match='SHA256'):
            journey.start(pid,aid)


def test_two_independent_synthetic_assets_never_share_feature_state(tmp_path,corpus,monkeypatch):
    app,saved=_app(tmp_path/'qa-multi')
    with TestClient(app) as client:
        pid=_make_project(client,'QA synthetic - multi asset')
        aid=_published(app,client,pid,(corpus/'sensor_unlabeled.csv').read_bytes(),'unlabeled.csv')
        other=_published(app,client,pid,(corpus/'static_regression.csv').read_bytes(),'static_regression.csv')
        assert aid!=other and len(saved[aid][1])==1200 and len(saved[other][1])==1800
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _: {'valid':True})
        auto=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs,models=None)
        journey=FeatureAnalysisJourney(FeatureDataAnalysis(app.state.projects,
            app.state.feature_semantic,app.state.feature_jobs,app.state.native_bi),auto)
        a=journey.start(pid,aid);b=journey.start(pid,other)
        assert a['source_sha256']!=b['source_sha256']
        assert a['feature_preview']['session_id']!=b['feature_preview']['session_id']
        assert journey.status(pid,aid)['feature_preview']['session_id']==a['feature_preview']['session_id']
        assert journey.status(pid,other)['feature_preview']['session_id']==b['feature_preview']['session_id']
        with app.state.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM feature_analysis_sessions WHERE project_id=?',(pid,)).fetchone()[0]==2


def test_dirty_synthetic_csv_is_never_silently_corrected_on_import(tmp_path,corpus):
    app,saved=_app(tmp_path/'dirty')
    with TestClient(app) as client:
        pid=_make_project(client,'QA synthetic - dirty data')
        aid=_published(app,client,pid,(corpus/'dirty_import.csv').read_bytes(),'dirty_import.csv')
        rows=saved[aid][1]
        assert len(rows)==500
        # Import must preserve questionable data for downstream quality gates;
        # rejecting invalid time semantics happens after contract validation.
        assert sum(x['temperature_c']=='' for x in rows)==24
        assert sum(x['event_timestamp']=='NOT_A_TIMESTAMP' for x in rows)==10
        assert sum(x['temperature_c']=='99999.0' for x in rows)==8
        assert len(rows)-len({x['sample_id'] for x in rows})==12
