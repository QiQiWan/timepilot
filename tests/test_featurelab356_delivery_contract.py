"""v0.35.6: the unified workspace reuses authorization and immutable report APIs."""
from __future__ import annotations
import hashlib

from test_featurelab335_immutable_reports import prepared


def test_existing_http_delivery_endpoints_form_same_session_report_chain(tmp_path,monkeypatch):
    app,client,pid,aid,analysis,state=prepared(tmp_path,monkeypatch)
    try:
        root=f'/api/v1/projects/{pid}/feature-lab'
        latest=client.get(root+'/analysis/latest',params={'asset_id':aid,'version_id':'base'})
        assert latest.status_code==200,latest.text
        assert latest.json()['session']['session_id']==state['session_id']
        report=client.get(root+f'/analysis/sessions/{state["session_id"]}/report')
        assert report.status_code==200,report.text
        live=report.json()
        assert hashlib.sha256(live['markdown'].encode('utf8')).hexdigest()==live['report_sha256']
        assert '模型价值' in live['markdown']
        assert '尚未完成' in live['markdown'] or '尚无' in live['markdown']
        listed=client.get(root+f'/analysis/sessions/{state["session_id"]}/reports')
        assert listed.status_code==200 and listed.json()['reports']==[]
        archived=client.post(root+f'/analysis/sessions/{state["session_id"]}/reports',json={'expected_revision':state['revision']})
        assert archived.status_code==201,archived.text
        stored=archived.json()
        assert stored['report_sha256']==live['report_sha256']
        assert stored['verified'] and stored['immutable']
        loaded=client.get(root+'/reports/'+stored['report_id'])
        assert loaded.status_code==200,loaded.text
        assert loaded.json()['markdown']==live['markdown']
        assert loaded.json()['manifest']['source_sha256']==state['source_sha256']
        second=client.post(root+f'/analysis/sessions/{state["session_id"]}/reports',json={'expected_revision':state['revision']})
        assert second.status_code==201
        assert second.json()['report_id']==stored['report_id']
        assert len(client.get(root+f'/analysis/sessions/{state["session_id"]}/reports').json()['reports'])==1
    finally:
        client.__exit__(None,None,None)


def test_unified_report_archive_rejects_outdated_revision_and_tampered_bytes(tmp_path,monkeypatch):
    app,client,pid,aid,analysis,state=prepared(tmp_path,monkeypatch)
    try:
        root=f'/api/v1/projects/{pid}/feature-lab'
        sid=state['session_id']
        invalid=client.post(root+f'/analysis/sessions/{sid}/reports',json={'expected_revision':state['revision']+1})
        assert invalid.status_code in (400,409,422)
        created=client.post(root+f'/analysis/sessions/{sid}/reports',json={'expected_revision':state['revision']})
        assert created.status_code==201,created.text
        rec=created.json()
        from timepilot.feature_report_artifacts import FeatureReportArtifacts
        archive=FeatureReportArtifacts(analysis)
        archive._path(archive._row(pid,rec['report_id'])).write_bytes(b'tampered')
        result=client.get(root+'/reports/'+rec['report_id'])
        assert result.status_code in (400,409,422)
    finally:
        client.__exit__(None,None,None)
