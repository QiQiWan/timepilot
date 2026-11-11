"""Industrial, opaque-header and multi-sheet regression cases for generic publishing."""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.generic_assets import GenericAssetStore, PublishGeneral
from timepilot.smart_import import SmartImportService, SmartImportError, TransformRecipe


def upload(client, pid, data, filename):
    result=client.post(f'/api/v1/projects/{pid}/imports',files=[('files',(filename,data))])
    assert result.status_code==201,result.text
    return result.json()['import_id']


def test_opaque_fields_are_from_source_and_never_pretend_business_labels(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    source='Time,'+','.join(f'A{i}' for i in range(1,15))+'\n'
    for n in range(506):
        source += '2026-11-10 08:00:%02d,'% (n%60)+','.join(str(n+i) for i in range(1,15))+'\n'
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'通用指标'}).json()['id']
        iid=upload(client,pid,source.encode(),'housing.csv')
        info=client.get(f'/api/v1/projects/{pid}/imports/{iid}').json()
        region=info['candidates'][0]['candidate_id']
        schema=client.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{region}/schema').json()
        assert [c['name'] for c in schema['columns']]==['Time',*[f'A{i}' for i in range(1,15)]]
        assert schema['source_file']=='housing.csv'
        assert schema['columns'][1]['suggested_role']=='measure'
        assert schema['columns'][1]['metric_candidate'] is True
        assert schema['columns'][1]['business_meaning_confirmed'] is False
        assert schema['source_fields'][1]['source_name']=='A1'
        assert not any('A1/A2' in x for x in schema['warnings'])
        assert schema['columns'][0]['suggested_role']=='time'
        assert schema['sampled_rows']==120
        assert client.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{region}/schema?header_row=999').status_code==422
        assert client.post(f'/api/v1/projects/{pid}/imports/{iid}/regions/{region}/publish-general',json={
            'name':'house','confirm':False}).status_code==422
        store=GenericAssetStore(app.state.smart_import)
        item=store.publish(iid,region,PublishGeneral(name='housing',confirm=True))
        assert item['column_count']==15 and item['row_count']==506
        assert item['columns'][1]['nonempty_count']==506
        assert item['columns'][1]['missing_fraction']==0
        with store.version_file(iid,region,item['version_id']).open(encoding='utf-8') as inp:
            reader=csv.DictReader(inp)
            assert reader.fieldnames==['Time',*[f'A{i}' for i in range(1,15)]]
            assert len(list(reader))==506


def test_industrial_signals_events_seconds_are_preserved_in_generic_asset(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    csv_text=('设备编号,采集时间,温度(°C),压力(kPa),维护事件,批次号\n'
              'M-01,2026-11-10 08:00:01,26.5,100.1,清洗,B001\n'
              'M-01,2026-11-10 08:00:02,27.5,100.2,,B001\n')
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'设备过程'}).json()['id']
        iid=upload(client,pid,csv_text.encode(),'sensor.csv')
        region=app.state.smart_import.manifest(iid)['candidates'][0]['candidate_id']
        schema=client.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{region}/schema').json()
        assert schema['columns'][1]['suggested_role']=='time'
        assert schema['columns'][2]['unit_hint']=='°C'
        assert schema['columns'][4]['suggested_role']=='event'
        published=GenericAssetStore(app.state.smart_import).publish(iid,region,PublishGeneral(
            name='设备过程全量',confirm=True,roles={'device':'设备编号','time':'采集时间',
                                                  'event':'维护事件','batch':'批次号'}))
        output=GenericAssetStore(app.state.smart_import).version_file(iid,region,published['version_id'])
        with output.open(encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        assert len(rows)==2
        assert rows[0]['采集时间']=='2026-11-10 08:00:01'
        assert rows[1]['采集时间']=='2026-11-10 08:00:02'
        assert rows[0]['维护事件']=='清洗'
        assert '压力(kPa)' in rows[0]
        assert published['confirmed_roles']['event']=='维护事件'
        # Legacy specialized time-series still keeps seconds, rather than merging into one day.
        specialized=app.state.smart_import.transform(iid,TransformRecipe(
            candidate_ids=[region],metric_column='温度(°C)',field_mapping={'series_id':'设备编号'}))
        with app.state.smart_import.version_file(iid,specialized['version_id']).open(encoding='utf-8') as f:
            observations=list(csv.DictReader(f))
        assert [r['timestamp'] for r in observations]==['2026-11-10T08:00:01','2026-11-10T08:00:02']
        assert all('conflict_review' not in x['quality_flags'] for x in observations)


def test_multisheet_header_correction_and_project_scope(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    wb=Workbook();ws=wb.active;ws.title='工艺记录';ws.append(['重要说明：本表原始数据']);ws.append(['设备','压力','维护']);ws.append(['A',10,'正常']);ws.append(['B',11,'保养'])
    second=wb.create_sheet('另一种格式');second.append(['A1','A2']);second.append([1,2])
    buff=io.BytesIO();wb.save(buff)
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'工业项目'}).json()['id']
        other=client.post('/api/v1/projects',json={'name':'隔离项目'}).json()['id']
        iid=upload(client,pid,buff.getvalue(),'process.xlsx')
        manifest=app.state.smart_import.manifest(iid)
        cid=next(c['candidate_id'] for c in manifest['candidates'] if c['sheet']=='工艺记录')
        schema=client.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/schema?header_row=2')
        assert schema.status_code==200,schema.text
        assert [x['name'] for x in schema.json()['columns'][:3]]==['设备','压力','维护']
        assert client.get(f'/api/v1/projects/{other}/imports/{iid}/regions/{cid}/schema').status_code==404
        store=GenericAssetStore(app.state.smart_import)
        item=store.publish(iid,cid,PublishGeneral(name='工艺',header_row=2,confirm=True))
        assert item['row_count']==2
        assert item['source_header_row']==2
        assert store.publish(iid,cid,PublishGeneral(name='工艺',header_row=2,confirm=True))['version_id']==item['version_id']
        assert client.get(f'/api/v1/projects/{other}/imports/{iid}/regions/{cid}/versions').status_code==404


def test_gb18030_csv_source_header_is_preserved(tmp_path):
    imports=SmartImportService(tmp_path)
    raw='设备编号,温度,维护记录\n机器1,20,清洗\n'.encode('gb18030')
    item=imports.ingest(io.BytesIO(raw),'工厂.csv')
    candidate=item['candidates'][0]
    assert candidate['columns'][:3]==['设备编号','温度','维护记录']
    meta=GenericAssetStore(imports).publish(item['import_id'],candidate['candidate_id'],
                                            PublishGeneral(name='机器',confirm=True))
    assert meta['row_count']==1


def test_reject_unverified_roles_and_bad_source_hash(tmp_path):
    imports=SmartImportService(tmp_path)
    item=imports.ingest(io.BytesIO(b'Time,Value\n2026-01-01,5\n'),'data.csv')
    cid=item['candidates'][0]['candidate_id']
    store=GenericAssetStore(imports)
    with pytest.raises(SmartImportError,match='不在源表'):
        store.publish(item['import_id'],cid,PublishGeneral(name='fail',confirm=True,roles={'time':'fabricated'}))
    original=imports._dir(item['import_id'])/'source.csv'
    original.write_bytes(b'Time,Value\ncorrupt,20\n')
    with pytest.raises(SmartImportError,match='哈希'):
        store.inspect(item['import_id'],cid)


def test_native_bi_publish_api_idempotent_and_project_isolated(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    text='采集时间,温度,气压,事件\n2026-11-10 01:00:00,22.5,100,维修\n2026-11-10 01:00:01,23.4,101,\n'
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'生产测试'}).json()['id']
        other=client.post('/api/v1/projects',json={'name':'另一个项目'}).json()['id']
        iid=upload(client,pid,text.encode(),'plant.csv')
        cid=app.state.smart_import.manifest(iid)['candidates'][0]['candidate_id']
        endpoint=f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general'
        request={'name':'生产过程表','confirm':True,'roles':{'time':'采集时间','event':'事件'}}
        forbidden=client.post(f'/api/v1/projects/{other}/imports/{iid}/regions/{cid}/publish-general',json=request)
        assert forbidden.status_code==404
        # Register with a deterministic local BI test double when optional
        # DuckDB/Perspective runtime is not present in the test container.
        native=app.state.native_bi
        if __import__('importlib').util.find_spec('duckdb') is None:
            generated={}
            def fake_import(path,name,lineage):
                with Path(path).open(encoding='utf-8') as f:
                    reader=csv.DictReader(f); rows=list(reader)
                    created={'asset_id':'local-bi-test-id','name':name,'row_count':len(rows),
                             'columns':[{'name':x} for x in reader.fieldnames]}
                generated[created['asset_id']]=created
                return created
            def fake_asset(aid):
                return generated[aid]
            native.import_from_path=fake_import
            native.asset=fake_asset
        first=client.post(endpoint,json=request)
        assert first.status_code==201,first.text
        result=first.json();assert result['status']=='ready'
        aid=result['native_asset_id']
        info=client.get(f'/api/v1/native-bi/assets/{aid}')
        assert info.status_code==200,info.text
        meta=info.json()
        assert meta['row_count']==2
        assert [x['name'] for x in meta['columns']]==['采集时间','温度','气压','事件']
        assert client.get(f'/api/v1/projects/{pid}/generic-assets/{aid}').status_code==200
        assert client.get(f'/api/v1/projects/{pid}/native-assets/{aid}').status_code==200
        assert client.get(f'/api/v1/projects/{other}/native-assets/{aid}').status_code==404
        assert client.get(f'/api/v1/projects/{other}/generic-assets/{aid}').status_code==404
        assert client.get(f'/api/v1/projects/{pid}/generic-assets').json()[0]['native_asset_id']==aid
        unified=client.get(f'/api/v1/projects/{pid}/data-catalog')
        assert unified.status_code==200 and unified.json()['count']==1
        assert unified.json()['assets'][0]['metric_candidates']==2
        assert client.get(f'/api/v1/projects/{other}/data-catalog').json()['count']==0
        again=client.post(endpoint,json=request)
        assert again.status_code==201 and again.json()['reused'] is True
        assert again.json()['native_asset_id']==aid
    # Restart retains project lineage; the native test double is intentionally
    # non-persistent, unlike the production DuckDB asset registry.
    restarted=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(restarted) as client:
        assert client.get(f'/api/v1/projects/{pid}/generic-assets/{aid}').status_code==200
        assert client.get(f'/api/v1/projects/{other}/generic-assets/{aid}').status_code==404


def test_agent_generic_inspect_does_not_return_source_rows(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        pid=client.post('/api/v1/projects',json={'name':'Agent 项目'}).json()['id']
        iid=upload(client,pid,'批次号,温度\nSENSITIVE_001,38.5\n'.encode(),'batch.csv')
        cid=app.state.smart_import.manifest(iid)['candidates'][0]['candidate_id']
        executor=app.state.agent_executor
        fields=executor.registry.execute('generic.schema.inspect',{
            'project_id':pid,'import_id':iid,'candidate_id':cid},executor.services)
        assert fields['columns'][0]['name']=='批次号'
        assert 'sample_rows' not in fields
        assert 'SENSITIVE_001' not in str(fields)
        catalog=executor.registry.execute('project.assets.list',{'project_id':pid},executor.services)
        assert catalog['project_id']==pid
        other=client.post('/api/v1/projects',json={'name':'无关'}).json()['id']
        with pytest.raises(KeyError):
            executor.registry.execute('generic.schema.inspect',{'project_id':other,
                'import_id':iid,'candidate_id':cid},executor.services)
        job=client.post('/api/v1/agent/jobs',json={'goal':'generic_understand',
            'project_id':pid,'import_id':iid,'candidate_id':cid})
        assert job.status_code==201,job.text
        status=app.state.agent_executor.run_once()
        assert status is not None
        saved=client.get('/api/v1/agent/jobs/'+job.json()['job_id'])
        assert saved.status_code==200
        assert 'SENSITIVE_001' not in saved.text


def test_semicolon_delimited_csv_and_tsv_are_auto_identified(tmp_path):
    service=SmartImportService(tmp_path/'imports')
    csv_bytes='采集时间;温度;压力\n2026-11-10 01:01:02;23.4;100.1\n'.encode('utf-8')
    payload=service.ingest(io.BytesIO(csv_bytes),'machine.csv')
    cid=payload['candidates'][0]['candidate_id']
    region=GenericAssetStore(service).inspect(payload['import_id'],cid)
    assert [x['name'] for x in region['columns']]==['采集时间','温度','压力']
    output=GenericAssetStore(service).publish(payload['import_id'],cid,PublishGeneral(name='机器',confirm=True))
    assert output['column_count']==3 and output['row_count']==1
    tsv=b'timestamp\ttemperature\tpressure\n2026-11-10 02:00:00\t22\t100\n'
    payload2=service.ingest(io.BytesIO(tsv),'machine2.csv')
    assert payload2['candidates'][0]['columns']==['timestamp','temperature','pressure']


def test_unlabeled_trailing_values_are_preserved_without_column_loss(tmp_path):
    service=SmartImportService(tmp_path/'imports')
    source='timestamp,温度\n2026-11-10 01:01:02,23,101\n'
    item=service.ingest(io.BytesIO(source.encode()),'signals.csv')
    cid=item['candidates'][0]['candidate_id']
    version=GenericAssetStore(service).publish(item['import_id'],cid,
                                                PublishGeneral(name='signals',confirm=True))
    assert version['row_count']==1 and version['column_count']==3
    assert version['source_columns']==['timestamp','温度','']
    assert version['source_fields'][2]['source_name']==''
    with GenericAssetStore(service).version_file(item['import_id'],cid,version['version_id']).open(encoding='utf-8') as f:
        row=next(csv.DictReader(f))
    assert row['_field_3']=='101'


def test_numeric_time_axis_is_proposed_without_guessing_units():
    from timepilot.data_schema import infer_field
    time=infer_field('Time',[0,1,2,3],source_header_row=1)
    assert time['suggested_role']=='time' and time['confidence']=='review'
    assert '单位' in ' '.join(time['evidence'])
    metric=infer_field('A6',[0.1,0.2,0.3],source_header_row=1)
    assert metric['metric_candidate'] is True
    assert metric['business_meaning_confirmed'] is False


def test_opaque_numeric_signal_not_misidentified_as_entity(tmp_path):
    svc=SmartImportService(tmp_path/'data')
    sample='Time,A1,A2\n2026-11-10 08:00:01,1.1,101.1\n2026-11-10 08:00:02,1.2,101.2\n'
    info=svc.ingest(io.BytesIO(sample.encode()),'industrial.csv')
    cid=info['candidates'][0]['candidate_id']
    result=svc.transform(info['import_id'],TransformRecipe(candidate_ids=[cid],metric_column='A1'))
    assert result['counts']['output_rows']==2


def test_opaque_numeric_column_at_duplicate_time_requires_identity_review(tmp_path):
    svc=SmartImportService(tmp_path/'data')
    sample='Time,A2,A14\n2026-11-10 08:00:01,101,1.1\n2026-11-10 08:00:01,102,1.2\n'
    info=svc.ingest(io.BytesIO(sample.encode()),'codes.csv')
    cid=info['candidates'][0]['candidate_id']
    with pytest.raises(SmartImportError,match='series_id'):
        svc.transform(info['import_id'],TransformRecipe(candidate_ids=[cid],metric_column='A14'))
