"""v0.35.4 real SQLite state and source-bound automatic low-cost preview."""
from test_conversational_bi_v0231 import _env
from timepilot.feature_analysis_journey import FeatureAnalysisJourney
from timepilot.feature_data_analysis import FeatureDataAnalysis
from timepilot.feature_auto_analysis import FeatureAutoAnalysis


def test_publish_journey_is_idempotent_without_model_api(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _: {'valid':True})
        auto=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs,models=None)
        journey=FeatureAnalysisJourney(FeatureDataAnalysis(app.state.projects,
            app.state.feature_semantic,app.state.feature_jobs,app.state.native_bi),auto)
        first=journey.start(pid,aid)
        second=journey.start(pid,aid)
        assert first['feature_preview']['session_id']==second['feature_preview']['session_id']
        assert second['auto_start']=='reused'
        assert journey.status(pid,aid)['feature_preview']['status']=='profiling'
        assert journey.status(pid,aid)['requires_model_api'] is False
        assert 'materialization' in second['requires_human_confirmation_for']
        # The HTTP read contract must never trigger new work.
        for _ in range(2):
            resp=client.get(f'/api/v1/projects/{pid}/feature-lab/workspace',params={'asset_id':aid})
            assert resp.status_code==200
            assert resp.json()['feature_preview']['session_id']==first['feature_preview']['session_id']
        with app.state.projects._db() as db:
            n=db.execute('SELECT COUNT(*) FROM feature_analysis_sessions WHERE project_id=?',(pid,)).fetchone()[0]
            assert n==1
    finally:client.__exit__(None,None,None)


def test_fingerprint_validation_prevents_reuse_after_source_mutation(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _: {'valid':False})
        auto=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs,models=None)
        service=FeatureAnalysisJourney(FeatureDataAnalysis(app.state.projects,
            app.state.feature_semantic,app.state.feature_jobs,app.state.native_bi),auto)
        import pytest
        with pytest.raises(ValueError,match='SHA256'):
            service.start(pid,aid)
    finally:client.__exit__(None,None,None)
