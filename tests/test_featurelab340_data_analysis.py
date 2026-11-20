"""v0.34.0 automatic EDA execution (without relying on optional DuckDB)."""
from __future__ import annotations
from test_conversational_bi_v0231 import _env
from test_featurelab32_auto_workflow import simulate_profile
from timepilot.feature_data_analysis import FeatureDataAnalysis
from timepilot.feature_auto_analysis import FeatureAutoAnalysis


def test_profile_request_idempotent_and_auto_analysis_summary(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':True})
        eda=FeatureDataAnalysis(app.state.projects,app.state.feature_semantic,
              app.state.feature_jobs,app.state.native_bi)
        s=eda.status(pid,aid,'base')
        assert s['status']=='not_started'
        first=eda.start(pid,aid,'base')
        again=eda.start(pid,aid,'base')
        assert first['job_id']==again['job_id']
        assert eda.status(pid,aid,'base')['status']=='queued'
        con=app.state.feature_semantic.latest(pid,aid,'base').model_dump()
        simulate_profile(app,pid,aid,con,first['job_id'])
        done=eda.status(pid,aid,'base')
        assert done['status']=='ready'
        assert done['profile']['rows_scanned']==3
        assert done['source_sha256']==first['source_sha256']
        assert any(item['code']=='inspect_quality' for item in done['recommendations'])
        assert eda.start(pid,aid,'base')['status']=='ready'
        resp=client.get(f'/api/v1/projects/{pid}/feature-lab/data-analysis',params={'asset_id':aid})
        assert resp.status_code==200 and resp.json()['status']=='ready'
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':False})
        assert client.get(f'/api/v1/projects/{pid}/feature-lab/data-analysis',params={'asset_id':aid}).status_code in (409,422)
    finally:client.__exit__(None,None,None)
