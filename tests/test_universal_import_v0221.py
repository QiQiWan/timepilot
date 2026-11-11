"""Source-faithful workbook regions, batch publication, and project isolation."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.table_region_scanner import discover_regions


def _app(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    native = app.state.native_bi
    saved = {}

    def fake_import(path, name, lineage):
        asset_id = str(uuid4())
        with Path(path).open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fields = list(reader.fieldnames or [])
        meta = {'asset_id': asset_id, 'name': name, 'row_count': len(rows),
                'sha256': lineage['data_sha256'],
                'columns': [{'name': x} for x in fields], 'lineage': lineage}
        saved[asset_id] = (meta, rows)
        return meta

    native.import_from_path = fake_import
    native.asset = lambda aid: saved[aid][0]
    native._version = lambda aid, version: {'version_id': version}
    native.query = lambda spec: {'total': len(saved[spec.asset_id][1])}
    return app, saved


def _workbook():
    book = Workbook()
    s = book.active
    s.title = '车间数据'
    for cell, val in {'B2':'Batch', 'C2':'Qty', 'F2':'Device', 'G2':'Temp',
                      'B3':'00123', 'C3':6, 'F3':'device_1', 'G3':18.3,
                      'B4':'00124', 'C4':7, 'F4':'device_2', 'G4':20.2,
                      'B8':'Event', 'C8':'Status', 'B9':'repair', 'C9':'done'}.items():
        s[cell]=val
    blank = book.create_sheet('空工作表')
    assert blank.max_row == 1
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _upload(client, pid, data=None, name='multi.xlsx'):
    if data is None: data = _workbook()
    uploaded = client.post(f'/api/v1/projects/{pid}/imports', files=[('files',(name,data))])
    assert uploaded.status_code == 201, uploaded.text
    iid = uploaded.json()['import_id']
    resp = client.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog')
    assert resp.status_code == 200, resp.text
    return iid,resp.json()


def _make_project(client, name='厂内'):
    r=client.post('/api/v1/projects',json={'name':name})
    assert r.status_code==201,r.text
    return r.json()['id']


def test_one_worksheet_multiple_rectangles_and_no_roles(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        pid = _make_project(c)
        iid, catalog = _upload(c, pid)
        assert catalog['regions_total'] == 3, catalog
        assert [x['sheet'] for x in catalog['files'][0]['worksheets']] == ['车间数据','空工作表']
        regions=catalog['files'][0]['worksheets'][0]['regions']
        assert [(r['bbox']['row_start'],r['bbox']['col_start']) for r in regions] == [(2,2),(2,6),(8,2)]
        assert [[f['source_name'] for f in r['fields']] for r in regions] == [
            ['Batch','Qty'], ['Device','Temp'], ['Event','Status']]
        assert regions[0]['fields'][0]['source_cell'] == 'B2'
        assert '字段业务角色' in catalog['notices'][0]
        rid=regions[0]['region_id']
        preview=c.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{rid}/preview')
        assert preview.status_code==200,preview.text
        assert preview.json()['rows'][0]['values']['Batch']=='00123'
        tools=c.get(f'/api/v1/projects/{pid}/imports/{iid}/tools')
        assert tools.status_code==200
        assert all(x['read_only'] and x['name'].startswith('import.') for x in tools.json()['tools'])
        explain=c.post(f'/api/v1/projects/{pid}/imports/{iid}/semantic-explain',json={'use_model':False})
        assert explain.status_code==200
        assert explain.json()['original_names_modified'] is False
        assert explain.json()['status']=='local'
        other = _make_project(c,'另一项目')
        assert c.get(f'/api/v1/projects/{other}/imports/{iid}/catalog').status_code==404


def test_one_click_batch_idempotent_and_reusable_version(tmp_path):
    app,saved=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid, catalog=_upload(c,pid)
        regions=catalog['files'][0]['worksheets'][0]['regions']
        ids=[r['region_id'] for r in regions]
        url=f'/api/v1/projects/{pid}/imports/{iid}/publish-batch'
        result=c.post(url,json={'region_ids':ids,'confirm':True})
        assert result.status_code==202,result.text
        batch=result.json()
        assert batch['count']==3 and batch['status']=='queued'
        assert c.post(url,json={'region_ids':list(reversed(ids)),'confirm':True}).json()['batch_id']==batch['batch_id']
        assert c.post(url,json={'region_ids':ids,'confirm':False}).status_code==422
        for _ in ids:
            finish=app.state.project_publication_jobs.work_once()
            assert finish['status']=='succeeded',finish
        response=c.get(f'/api/v1/projects/{pid}/publish-batches/{batch["batch_id"]}')
        assert response.status_code==200,response.text
        assert response.json()['status']=='succeeded'
        aids=[r['native_asset_id'] for r in response.json()['items']]
        assert len(set(aids))==3
        assert [len(saved[x][1]) for x in aids]==[2,2,1]
        assert saved[aids[0]][1][0]['Batch']=='00123'
        assert c.get(f'/api/v1/projects/{pid}/data-catalog').json()['count']==3
        assert c.post(url,json={'region_ids':ids,'confirm':True}).json()['batch_id']==batch['batch_id']
        assert len(saved)==3
        other=_make_project(c,'另一个项目')
        assert c.get(f'/api/v1/projects/{other}/publish-batches/{batch["batch_id"]}').status_code==404
        assert c.post(f'/api/v1/projects/{other}/imports/{iid}/publish-batch',json={'region_ids':ids,'confirm':True}).status_code==404


def test_partial_failure_then_retry_only_failed_region(tmp_path):
    app,saved=_app(tmp_path)
    native=app.state.native_bi
    original=native.import_from_path
    attempts={'n':0}
    def fail_middle(*args,**kwargs):
        attempts['n']+=1
        if attempts['n']==2:raise OSError('interrupted during second table BI registration')
        return original(*args,**kwargs)
    native.import_from_path=fail_middle
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,catalog=_upload(c,pid)
        ids=[r['region_id'] for r in catalog['files'][0]['worksheets'][0]['regions']]
        batch=c.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                     json={'region_ids':ids,'confirm':True}).json()
        for _ in ids:
            app.state.project_publication_jobs.work_once()
        pre=c.get(f'/api/v1/projects/{pid}/publish-batches/{batch["batch_id"]}').json()
        assert pre['status']=='partial_failed' and pre['succeeded']==2 and pre['failed']==1,pre
        existing_ids={x['native_asset_id'] for x in pre['items'] if x['status']=='succeeded'}
        assert len(saved)==2
        retry=c.post(f'/api/v1/projects/{pid}/publish-batches/{batch["batch_id"]}/retry')
        assert retry.status_code==200,retry.text
        app.state.project_publication_jobs.work_once()
        post=c.get(f'/api/v1/projects/{pid}/publish-batches/{batch["batch_id"]}').json()
        assert post['status']=='succeeded' and post['count']==3
        assert existing_ids.issubset({x['native_asset_id'] for x in post['items']})
        assert len(saved)==3
        assert attempts['n']==4


def test_duplicate_blank_csv_header_preserves_raw_names_values(tmp_path):
    app,saved=_app(tmp_path)
    content=b'Num,Num,,Flag\n00123,10,0,ok\n00124,11,1,bad\n'
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid,content,'mixed.csv')
        reg=tree['files'][0]['worksheets'][0]['regions'][0]
        assert [f['source_name'] for f in reg['fields']]==['Num','Num','','Flag']
        assert len({f['internal_alias'] for f in reg['fields']})==4
        assert len(reg['fields'])==4
        assert reg['fields'][2]['source_cell']=='C1'
        assert reg['row_count_estimate']==2
        batch=c.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                     json={'region_ids':[reg['region_id']],'confirm':True})
        assert batch.status_code==202,batch.text
        outcome=app.state.project_publication_jobs.work_once()
        assert outcome['status']=='succeeded',outcome
        meta,rows=next(iter(saved.values()))
        assert rows[0]['Num']=='00123'
        assert len(meta['columns'])==4
        assert '0' in rows[0].values()


def test_revision_is_immutable_and_excludes_superseded_region(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid)
        region=tree['files'][0]['worksheets'][0]['regions'][0]
        rid=region['region_id']
        result=c.patch(f'/api/v1/projects/{pid}/imports/{iid}/regions/{rid}/layout',
                       json={'header_row':2,'confirm':True})
        assert result.status_code==200,result.text
        revised_id=result.json()['region_id']
        assert revised_id!=rid
        new=c.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog').json()
        ids=[r['region_id'] for sh in new['files'][0]['worksheets'] for r in sh['regions']]
        assert rid not in ids and revised_id in ids
        failed=c.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                      json={'region_ids':[rid],'confirm':True})
        assert failed.status_code==422


def test_streaming_scanner_repeated_headers_split_into_regions():
    rows=[(1,['id','amount']),(2,['001',1]),(3,['002',2]),
          (4,['id','amount']),(5,['003',4]),(6,['004',5])]
    result=discover_regions(lambda:iter(rows),file_id='f0',logical_file='split.csv',sheet='CSV')
    assert len(result['regions'])==2
    assert [r['data_start_row'] for r in result['regions']]==[2,5]


def test_index_migration_persists_and_can_rebuild_from_old_manifest(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid)
        assert tree['directory']['directory_schema_version']==1
        first=tree['files'][0]['worksheets'][0]['regions'][0]
        rid=first['region_id']
        from timepilot.project_region_catalog import ProjectRegionDirectory
        before=ProjectRegionDirectory(app.state.projects).indexed_fields(pid,iid,rid)
        assert before[0]['source_cell']=='B2'
    # A fresh app instance indexes its existing project/source without moving
    # old binary files or invalidating existing asset identifiers.
    second=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(second) as c:
        tree=c.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog').json()
        assert tree['regions_total']==3
        after=__import__('timepilot.project_region_catalog',fromlist=['ProjectRegionDirectory']).ProjectRegionDirectory(second.state.projects).indexed_fields(pid,iid,rid)
        assert before==after
        assert c.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog').json()['directory']['indexed_region_count']==3


def test_import_llm_calls_scoped_read_only_tools_and_cannot_mutate(tmp_path):
    from timepilot.import_tool_registry import ImportToolRegistry
    from timepilot.import_semantics import ImportSemanticAssistant
    app,_=_app(tmp_path)
    class Gateway:
        calls=[]
        def _message(self,messages,*,max_tokens):
            self.calls.append(messages)
            if len(self.calls)==1:
                # Attack string must be rejected by the strict tool allowlist.
                return {'content':'{"tool_calls":[{"tool":"import.schema.profile","table_index":0},'
                        '{"tool":"import.publish.batch","table_index":0},'
                        '{"tool":"import.header.compare","table_index":999}]}', 'model':'fake'}
            return {'content':'{"summary":"发现三个独立表格","table_notes":'
                    '[{"table_index":0,"note":"字段结构已确认，业务用途未知"}]}','model':'fake'}
        @staticmethod
        def _parse_json(text):
            import json
            return json.loads(text)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid)
        gateway=Gateway()
        service=ImportSemanticAssistant(ImportToolRegistry(app.state.projects),gateway)
        output=service.explain(pid,iid,use_model=True)
        assert output['model_status']=='completed'
        assert output['tool_evidence_count']==1
        assert len(gateway.calls)==2
        text=str(gateway.calls[1]);assert '00123' not in text and 'device_1' not in text
        assert output['original_names_modified'] is False
        assert not c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets']


def test_annotations_and_formula_are_reported_not_executed(tmp_path):
    book=Workbook();s=book.active;s.title='记录'
    s['B1']='以下是说明';s['B3']='Serial';s['C3']='Result'
    s['B4']='0002';s['C4']='=2+3';s['B5']='0003';s['C5']=7
    data=io.BytesIO();book.save(data)
    app,saved=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid,data.getvalue(),'formula.xlsx')
        reg=tree['files'][0]['worksheets'][0]['regions'][0]
        assert reg['header_row']==3
        # The isolated note is visible as an unassigned source region.
        assert tree['unassigned_regions'],tree
        rid=reg['region_id']
        batch=c.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                     json={'region_ids':[rid],'confirm':True})
        assert batch.status_code==202,batch.text
        done=app.state.project_publication_jobs.work_once()
        assert done['status']=='succeeded',done
        rows=next(iter(saved.values()))[1]
        assert rows[0]['Serial']=='0002'
        assert rows[0]['Result']=='=2+3'


def test_100k_csv_scans_streaming_and_rows_are_complete(tmp_path):
    # Benchmark-shaped regression: no fixed time limit because CI hardware
    # varies. Verifies full-file extent and no early-sample truncation.
    source=('Time,temperature,pressure\n'+''.join(
        f'{i},{20+(i%10)},{2+(i%7)}\n' for i in range(100000))).encode('utf-8')
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid,source,'hundred_k.csv')
        assert tree['regions_total']==1,tree
        reg=tree['files'][0]['worksheets'][0]['regions'][0]
        assert reg['row_count_estimate']==100000
        assert reg['bbox']['row_end']==100001
        assert [x['source_name'] for x in reg['fields']]==['Time','temperature','pressure']


def test_batch_registration_recovers_missing_links_after_restart(tmp_path):
    """A crash between batch header and child registration is repairable."""
    app, saved = _app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid)
        ids=[r['region_id'] for r in tree['files'][0]['worksheets'][0]['regions']]
        url=f'/api/v1/projects/{pid}/imports/{iid}/publish-batch'
        batch=c.post(url,json={'region_ids':ids,'confirm':True}).json()
        with app.state.projects._db() as db:
            # Mimic a kill immediately after the durable header/first link.
            db.execute('DELETE FROM import_publication_batch_items WHERE batch_id=? AND ordinal>1',
                       (batch['batch_id'],))
        recovered=c.post(url,json={'region_ids':list(reversed(ids)),'confirm':True})
        assert recovered.status_code==202,recovered.text
        result=recovered.json()
        assert result['batch_id']==batch['batch_id'] and result['count']==3
        with app.state.projects._db() as db:
            # Every source table still has just one active writer.
            linked=db.execute('''SELECT COUNT(*) AS n FROM import_publication_batch_items
                WHERE batch_id=?''',(batch['batch_id'],)).fetchone()['n']
            total=db.execute('SELECT COUNT(*) AS n FROM project_publication_jobs').fetchone()['n']
        assert linked==3 and total==3
        for _ in ids:
            outcome=app.state.project_publication_jobs.work_once()
            assert outcome['status']=='succeeded',outcome
        assert len(saved)==3
