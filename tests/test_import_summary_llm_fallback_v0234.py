"""LLM unavailable, invalid JSON or prompt injection cannot affect source summary."""
from __future__ import annotations
from fastapi.testclient import TestClient
from test_universal_import_v0221 import _app,_make_project,_upload
from timepilot.import_semantics import ImportSemanticAssistant
from timepilot.import_tool_registry import ImportToolRegistry
from timepilot.import_summary import ImportSummaryService

class BrokenGateway:
    def _message(self,*args,**kwargs):raise RuntimeError('Simulated 429 or timeout')

class InvalidGateway:
    def _message(self,*args,**kwargs):return {'content':'{invalid-json'}
    def _parse_json(self,*args,**kwargs):raise ValueError('Simulated model JSON parse error')


def test_model_unavailable_falls_back_to_exact_regions(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c);iid,cat=_upload(c,pid)
        reg=ImportToolRegistry(app.state.projects)
        for gateway,expected in [(None,'not_configured'),(BrokenGateway(),'fallback'),(InvalidGateway(),'fallback')]:
            obj=ImportSemanticAssistant(reg,gateway).explain(pid,iid,use_model=True)
            assert obj['model_status']==expected,obj
            assert obj['original_names_modified'] is False
            assert len(obj['tables'])==3
            assert [table['columns'] for table in obj['tables']]==[
                ['Batch','Qty'],['Device','Temp'],['Event','Status']]
            assert obj['privacy']=='schema_only'
        # Deterministic evidence remains available despite every LLM failure.
        rid=cat['files'][0]['worksheets'][0]['regions'][0]['region_id']
        url=f'/api/v1/projects/{pid}/imports/{iid}/regions/{rid}/summary'
        ImportSummaryService(app.state.projects).summarize_region(pid,iid,rid)
        evidence=c.get(url)
        assert evidence.status_code==200,evidence.text
        assert evidence.json()['shape']['row_count']==2
