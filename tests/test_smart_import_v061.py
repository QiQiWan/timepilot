"""v0.6.0/v0.6.1: real spreadsheet and API contract, reproducibility and safety."""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.smart_import import SmartImportError, SmartImportService, TransformRecipe


def xlsx_bytes(rows, sheet='车型月度明细', leading_notes=False):
    wb=Workbook()
    ws=wb.active
    ws.title='说明' if leading_notes else sheet
    if leading_notes:
        ws.append(['事项','说明']);ws.append(['版本','样例'])
        ws=wb.create_sheet(sheet)
    for row in rows: ws.append(row)
    with io.BytesIO() as buf:
        wb.save(buf)
        return buf.getvalue()


def sample_files():
    head=['国家/地区','整车厂/品牌','车型','统计口径','年份','1月','2月','3月','来源网址']
    a=xlsx_bytes([head,['中国','丰田','Corolla','销量',2024,10,None,-1,'https://a.example/m'],
                  ['中国','丰田','Camry','产量',2024,9,3,2,'https://a.example/m']],leading_notes=True)
    b=xlsx_bytes([head,['中国','丰田','Corolla','销量',2025,17,22,8,'https://a.example/m'],
                  ['中国','丰田','Corolla','销量',2025,17,22,8,'https://b.example/m']])
    with io.BytesIO() as buf:
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('folder/2024.xlsx',a)
            z.writestr('folder/2025.xlsx',b)
        return buf.getvalue()


def test_zip_discovery_across_years_and_immutable_transform(tmp_path):
    svc=SmartImportService(tmp_path)
    manifest=svc.ingest(io.BytesIO(sample_files()),'sample.zip')
    assert len(manifest['files'])==2
    assert len(manifest['candidates'])==3
    assert len(manifest['suggested_candidate_ids'])==2
    notes=next(c for c in manifest['candidates'] if c['sheet']=='说明')
    assert notes['suggested'] is False
    cand=next(c for c in manifest['candidates'] if c['sheet']=='车型月度明细')
    assert cand['kind']=='wide_monthly'
    assert len(cand['month_columns'])==3
    sample=svc.preview_region(manifest['import_id'],cand['candidate_id'])
    assert sample['rows'][0]['values']['1月']==10
    recipe=TransformRecipe(candidate_ids=manifest['suggested_candidate_ids'],name='销量 / 产量')
    result=svc.transform(manifest['import_id'],recipe)
    assert result['counts']['cells_scanned']==12
    assert result['counts']['negative_values']==1
    assert result['counts']['missing_values']>=1
    assert result['counts']['identical_overlap']>=3
    assert result['period_min']=='2024-01' and result['period_max']=='2025-03'
    assert set(result['statistical_scopes'])=={'销量','产量'}
    path=svc.version_file(manifest['import_id'],result['version_id'])
    with path.open(encoding='utf-8') as f:
        rows=list(csv.DictReader(f))
    corolla=[r for r in rows if r['vehicle']=='Corolla']
    # Same source and dimensions match across 2024 and 2025.
    assert len({r['series_id'] for r in corolla if r['source_name']=='a.example'})==1
    assert len({r['source_file'] for r in corolla})==2
    assert {'F2','G2','H2'}.issubset({r['source_cell'] for r in rows})
    assert result==svc.transform(manifest['import_id'],recipe)
    allv=svc.preview_version(manifest['import_id'],result['version_id'],limit=50)
    assert len(allv['rows'])==result['counts']['output_rows']
    pivot=svc.basic_pivot(manifest['import_id'],result['version_id'],scopes=['销量'])
    assert '多个数据来源' in pivot['warning']
    scoped=svc.basic_pivot(manifest['import_id'],result['version_id'],scopes=['销量'],sources=['a.example'])
    assert scoped['warning'] is None
    assert pivot['rows'][0]['name']=='2024-01'


def test_exact_duplicate_opt_in_and_version_isolation(tmp_path):
    svc=SmartImportService(tmp_path)
    m=svc.ingest(io.BytesIO(sample_files()),'book.zip')
    one=svc.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids']))
    two=svc.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids'],duplicate_policy='drop_identical'))
    assert two['counts']['dropped_identical']==3
    assert two['counts']['output_rows']==one['counts']['output_rows']-3
    assert two['version_id']!=one['version_id']
    assert svc.version_file(m['import_id'],one['version_id']).is_file()
    assert len(svc.manifest(m['import_id'])['versions'])==2


def test_nonstandard_time_and_target_automatically_discovered(tmp_path):
    wb=xlsx_bytes([['A2','Time','A14'],['item-A','2023-01-01',12],['item-B','2023-02-01',25]],sheet='sheetA')
    svc=SmartImportService(tmp_path)
    m=svc.ingest(io.BytesIO(wb),'price_data.xlsx')
    c=m['candidates'][0]
    assert c['kind']=='ordinary_timeseries'
    assert c['time_column']=='Time'
    assert 'A14' in c['quantity_columns']
    with pytest.raises(SmartImportError,match='series_id'):
        svc.transform(m['import_id'],TransformRecipe(candidate_ids=[c['candidate_id']]))
    v=svc.transform(m['import_id'],TransformRecipe(candidate_ids=[c['candidate_id']],
            field_mapping={'series_id':'A2','target':'A14'}))
    rows=svc.preview_version(m['import_id'],v['version_id'])['rows']
    assert [x['target'] for x in rows]==['12','25']
    assert [x['timestamp'] for x in rows]==['2023-01-01','2023-02-01']


def test_missing_publication_not_invented_and_explicit_assumption(tmp_path):
    svc=SmartImportService(tmp_path)
    m=svc.ingest(io.BytesIO(xlsx_bytes([['年份','1月','统计口径'],[2025,12,'销量']])),'sales.xlsx')
    # Two-column time source would not be considered a wide table. Three months required.
    assert m['candidates'][0]['kind']=='ordinary_table'
    m=svc.ingest(io.BytesIO(sample_files()),'sales.zip')
    rec=TransformRecipe(candidate_ids=m['suggested_candidate_ids'])
    v=svc.transform(m['import_id'],rec)
    assert svc.preview_version(m['import_id'],v['version_id'])['rows'][0]['available_at']==''
    assumed=svc.transform(m['import_id'],rec.model_copy(update={'publication_lag_days':38}))
    assert 'assumed_publication' in svc.preview_version(m['import_id'],assumed['version_id'])['rows'][0]['quality_flags']


def test_unsafe_zip_does_not_escape_private_storage(tmp_path):
    svc=SmartImportService(tmp_path)
    with io.BytesIO() as buf:
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('../../outside.xlsx',xlsx_bytes([['年份','1月','2月','3月'],[2022,1,2,3]]))
        data=buf.getvalue()
    manifest=svc.ingest(io.BytesIO(data),'files.zip')
    assert not (tmp_path/'outside.xlsx').exists()
    assert (svc._dir(manifest['import_id'])/'workbooks/0000.xlsx').exists()


def test_reject_oversize_and_wrong_type(tmp_path):
    svc=SmartImportService(tmp_path)
    with pytest.raises(SmartImportError): svc.ingest(io.BytesIO(b'12345'),'unsafe.exe')
    with pytest.raises(SmartImportError): svc.ingest(io.BytesIO(b'12345'),'sample.csv',max_bytes=2)
    assert not svc.list_imports()


def test_real_http_upload_scan_transform_and_basic_chart(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as cli:
        resp=cli.post('/api/v1/smart-import/uploads',files={'file':('sheets.zip',sample_files(),'application/zip')})
        assert resp.status_code==201,resp.text
        m=resp.json()
        scan=cli.get('/api/v1/smart-import/imports/'+m['import_id']).json()
        assert len(scan['groups'])>=1
        new=cli.post(f"/api/v1/smart-import/imports/{m['import_id']}/transform",
                     json={'candidate_ids':m['suggested_candidate_ids'],'name':'sample'})
        assert new.status_code==201,new.text
        version=new.json()['version_id']
        url=f"/api/v1/smart-import/imports/{m['import_id']}/versions/{version}"
        assert cli.get(url+'/preview').json()['rows']
        assert cli.get(url+'/pivot?group=timestamp&scopes=销量').json()['rows']
        exported=cli.get(url+'/export.csv')
        assert exported.status_code==200
        assert 'source_cell' in exported.text
        assert cli.get('/imports').status_code==200
        assert cli.get('/assets/smart-import-ui.js').status_code==200


def test_individual_excel_batch_and_async_progress(tmp_path):
    import time
    first=xlsx_bytes([['车型','年份','1月','2月','3月','统计口径'],
                      ['Sedan',2024,12,15,19,'销量']],sheet='车型月度明细')
    second=xlsx_bytes([['车型','年份','1月','2月','3月','统计口径'],
                       ['Sedan',2025,21,25,29,'销量']],sheet='车型月度明细')
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as cli:
        resp=cli.post('/api/v1/smart-import/uploads/batch',files=[
            ('files',('MarkLines_2024.xlsx',first,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
            ('files',('MarkLines_2025.xlsx',second,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))])
        assert resp.status_code==201,resp.text
        m=resp.json()
        assert [f['filename'] for f in m['files']]==['MarkLines_2024.xlsx','MarkLines_2025.xlsx']
        assert len(m['suggested_candidate_ids'])==2
        route=f"/api/v1/smart-import/imports/{m['import_id']}"
        job=cli.post(route+'/transform-jobs',json={'candidate_ids':m['suggested_candidate_ids']})
        assert job.status_code==202,job.text
        job_id=job.json()['job_id']
        for _ in range(100):
            state=cli.get(route+'/transform-jobs/'+job_id).json()
            if state['status'] not in ('running','queued'): break
            time.sleep(.02)
        assert state['status']=='completed',state
        assert state['completed_candidates']==2
        assert state['output_rows']==6
        v=cli.get(route+'/versions/'+state['version_id']).json()
        assert v['counts']['output_rows']==6
        records=cli.get(route+'/versions/'+state['version_id']+'/preview?limit=10').json()['rows']
        assert set(r['source_file'] for r in records)=={'MarkLines_2024.xlsx','MarkLines_2025.xlsx'}
        assert set(r['timestamp'] for r in records)=={
            '2024-01-01','2024-02-01','2024-03-01','2025-01-01','2025-02-01','2025-03-01'}
        assert cli.get(route+'/transform-jobs/not-valid').status_code==422
        assert 'multiple required' in cli.get('/assets/smart-import-ui.js').text


def test_quality_detects_large_monthly_jump_without_changing_original(tmp_path):
    svc=SmartImportService(tmp_path)
    book=xlsx_bytes([['车型','年份','统计口径','1月','2月','3月'],
                     ['Sedan',2025,'销量',100,1100,-25]])
    m=svc.ingest(io.BytesIO(book),'trend.xlsx')
    v=svc.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids']))
    assert v['counts']['abrupt_change_review']==1
    assert v['counts']['negative_values']==1
    assert v['quality_rates']['negative_fraction']>0
    rows=svc.preview_version(m['import_id'],v['version_id'])['rows']
    assert rows[1]['target']=='1100'
    assert 'abrupt_increase_review' in rows[1]['quality_flags']
    assert rows[2]['target']=='-25'
    assert 'negative' in rows[2]['quality_flags']
    with (svc._dir(m['import_id'])/'source.xlsx').open('rb') as f:
        assert f.read()==book
