"""HTTP integration: project/owner/source checks and read-only judgement endpoint."""
from test_conversational_bi_v0231 import _env
from test_featurelab335_immutable_reports import prepared


def test_workspace_insight_api_is_readonly_and_no_label_score(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':True})
        path=f'/api/v1/projects/{pid}/feature-lab/workspace/insights'
        for _ in range(2):
            response=client.get(path,params={'asset_id':aid,'version_id':'base'})
            assert response.status_code==200,response.text
            data=response.json()
            assert data['requires_model_api'] is False
            assert data['source_sha256']
            assert data['model']['comparisons']==[]
            assert 'mean_improvement' not in data['model']
        with app.state.projects._db() as db:
            assert db.execute('SELECT COUNT(*) FROM feature_analysis_sessions WHERE project_id=?',
                              (pid,)).fetchone()[0]==0
    finally:client.__exit__(None,None,None)


def test_workspace_insights_refuses_modified_source(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    try:
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':False})
        response=client.get(f'/api/v1/projects/{pid}/feature-lab/workspace/insights',
                            params={'asset_id':aid})
        assert response.status_code in (409,422)
        assert not response.json().get('model',{}).get('comparisons')
    finally:client.__exit__(None,None,None)


def test_completed_preview_report_includes_same_evidence_policy(tmp_path,monkeypatch):
    app,client,pid,aid,analysis,state=prepared(tmp_path,monkeypatch)
    try:
        route=f'/api/v1/projects/{pid}/feature-lab/workspace/insights'
        r=client.get(route,params={'asset_id':aid})
        assert r.status_code==200,r.text
        doc=r.json()
        assert doc['layers']['feature_quality']['status']=='measured'
        assert doc['model']['comparisons']==[]
        report=analysis.report_markdown(pid,state['session_id'])
        assert '自动研判（可核验的观察）' in report['markdown']
        assert '尚无' in report['markdown'] or '质量' in report['markdown']
    finally:client.__exit__(None,None,None)
