"""v0.33.5: archived report bytes, immutable revisions, provenance and source guards."""
from __future__ import annotations
import hashlib
import pytest

from test_conversational_bi_v0231 import _env
from test_featurelab32_auto_workflow import simulate_profile,set_job
from timepilot.feature_auto_analysis import FeatureAutoAnalysis
from timepilot.feature_auto_coordinator import FeatureAutoCoordinator
from timepilot.feature_report_artifacts import FeatureReportArtifacts


def prepared(tmp_path,monkeypatch):
    app,client,pid,aid,_=_env(tmp_path)
    monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':True})
    analysis=FeatureAutoAnalysis(app.state.projects,app.state.native_bi,
        app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
    state=analysis.create(pid,aid,'base',goal='诊断传感器',task_hint='quality',
        request_id='report-335',budget=5)
    simulate_profile(app,pid,aid,
        app.state.feature_semantic.latest(pid,aid,'base').model_dump(),state['profile_job_id'])
    assert FeatureAutoCoordinator(analysis).work_once()
    pending=analysis.get(pid,state['session_id'])
    doc=analysis.candidates.get(pid,pending['candidate_session_id'])
    values=[{'feature_name':x['output_name'],'rows_scanned':3,'finite_count':3,
             'null_rate':0,'constant_on_full_data':False} for x in doc['candidates']]
    for jid in pending['preview_job_ids']:
        set_job(app,jid,{'quality':values,'source_sha256':state['source_sha256']})
    assert FeatureAutoCoordinator(analysis).work_once()
    done=analysis.get(pid,state['session_id'])
    assert done['status']=='ready'
    return app,client,pid,aid,analysis,done


def test_archived_bytes_immutable_and_tamper_detected(tmp_path,monkeypatch):
    app,client,pid,aid,analysis,state=prepared(tmp_path,monkeypatch)
    try:
        reports=FeatureReportArtifacts(analysis)
        a=reports.archive(pid,state['session_id'],state['revision'])
        assert a['verified'] and a['immutable']
        assert reports.archive(pid,state['session_id'],state['revision'])['report_id']==a['report_id']
        content=reports.get(pid,a['report_id'])['markdown'].encode()
        assert hashlib.sha256(content).hexdigest()==a['report_sha256']
        row=reports._row(pid,a['report_id'])
        file=reports._path(row)
        file.write_bytes(content+b'\nMODIFIED')
        with pytest.raises(ValueError,match='校验失败'):
            reports.get(pid,a['report_id'])
        with pytest.raises(ValueError,match='校验失败|不同字节'):
            reports.archive(pid,state['session_id'],state['revision'])
    finally:client.__exit__(None,None,None)


def test_archived_report_prevents_source_change_and_unauthorized_revision(tmp_path,monkeypatch):
    app,client,pid,aid,analysis,state=prepared(tmp_path,monkeypatch)
    try:
        reports=FeatureReportArtifacts(analysis)
        with pytest.raises(ValueError,match='修订'):
            reports.archive(pid,state['session_id'],state['revision']-1)
        monkeypatch.setattr(app.state.native_bi,'verify',lambda _id:{'valid':False})
        with pytest.raises(ValueError,match='来源'):
            reports.archive(pid,state['session_id'],state['revision'])
    finally:client.__exit__(None,None,None)
