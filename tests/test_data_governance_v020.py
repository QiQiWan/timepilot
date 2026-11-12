"""Canonical identity, project-scoped governance, immutable selection and publish recovery."""
from __future__ import annotations
import csv
import io
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.generic_assets import GenericAssetStore, PublishGeneral
from timepilot.smart_import import SmartImportService
from timepilot.data_platform import DataPlatform


def test_data_version_is_rename_invariant_and_keeps_all_columns(tmp_path):
    service=SmartImportService(tmp_path/'home')
    incoming=service.ingest(io.BytesIO(('Time,A1,A2\n1,23,101\n2,24,102\n').encode()),'housing.csv')
    cid=incoming['candidates'][0]['candidate_id']
    store=GenericAssetStore(service)
    one=store.publish(incoming['import_id'],cid,PublishGeneral(name='old label',confirm=True))
    two=store.publish(incoming['import_id'],cid,PublishGeneral(name='new label',confirm=True))
    assert one['version_id']==two['version_id']
    assert one['data_sha256']==two['data_sha256']
    assert len(store.list_versions(incoming['import_id'],cid))==1
    assert list(csv.DictReader(store.version_file(incoming['import_id'],cid,one['version_id']).open())) == [
        {'Time':'1','A1':'23','A2':'101'},{'Time':'2','A1':'24','A2':'102'}]


def test_publish_resumes_sha_verified_chunks_after_interruption(tmp_path,monkeypatch):
    service=SmartImportService(tmp_path/'home')
    incoming=service.ingest(io.BytesIO(('time,temp\n'+''.join(f'{i},23\n' for i in range(20030))).encode()),'minutes.csv')
    cid=incoming['candidates'][0]['candidate_id']
    store=GenericAssetStore(service)
    source_rows=service._rows
    def interrupted(*a,**kw):
        for i,row in enumerate(source_rows(*a,**kw)):
            if i==20008:raise RuntimeError('simulated power loss')
            yield row
    monkeypatch.setattr(service,'_rows',interrupted)
    request=PublishGeneral(name='industrial sensor',confirm=True)
    with pytest.raises(RuntimeError,match='power loss'):
        store.publish(incoming['import_id'],cid,request)
    status=store.publish_checkpoint(incoming['import_id'],cid,request)
    assert status['completed_chunks']==1 and status['completed_rows']==20000
    assert len(store.list_versions(incoming['import_id'],cid))==0
    monkeypatch.setattr(service,'_rows',source_rows)
    meta=store.publish(incoming['import_id'],cid,request)
    assert meta['row_count']==20030 and meta['verified_publish_chunks']==2
    assert store.publish_checkpoint(incoming['import_id'],cid,request)['status']=='none'
    with store.version_file(incoming['import_id'],cid,meta['version_id']).open() as f:
        assert sum(1 for _ in csv.reader(f))==20031


def build_app_with_native_double(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    files={}
    def fake_import(path,name,lineage):
        aid=str(uuid4())
        with Path(path).open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f); data=list(reader)
        meta={'asset_id':aid,'name':name,'row_count':len(data),
              'sha256':lineage['data_sha256'],
              'columns':[{'name':x} for x in reader.fieldnames], 'lineage':lineage}
        files[aid]=(meta,data)
        return meta
    native.import_from_path=fake_import
    native.asset=lambda aid:files[aid][0]
    native._version=lambda aid,version: {'version_id':version}
    native.verify=lambda aid: {'valid':aid in files,'reason':None if aid in files else 'not found'}
    native.query=lambda spec:{'total':sum(all(
        str(row.get(f.column,''))==str(f.value) if f.op=='eq' else True for f in spec.filters
        ) for row in files[spec.asset_id][1])}
    return app,files


def publish_in_project(client,pid):
    text=('设备编号,采集时间,温度,工况\n'
          'M1,2026-11-10 08:00:00,24.1,R1\n'
          'M1,2026-11-10 08:00:00,,R1\n'
          'M2,2026-11-10 08:01:00,999,R2\n')
    up=client.post(f'/api/v1/projects/{pid}/imports',files=[('files',('sensor.csv',text.encode()))])
    assert up.status_code==201,up.text
    iid=up.json()['import_id']
    info=client.get(f'/api/v1/projects/{pid}/imports/{iid}').json()
    cid=info['candidates'][0]['candidate_id']
    pub=client.post(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general',json={
        'name':'温度传感器','confirm':True,'roles':{'time':'采集时间','entity':'设备编号'}})
    assert pub.status_code==201,pub.text
    return pub.json()['native_asset_id']


def test_quality_policy_versioning_issues_and_project_isolation(tmp_path):
    app,files=build_app_with_native_double(tmp_path)
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'温度质量'}).json()['id']
        other=c.post('/api/v1/projects',json={'name':'无权限项目'}).json()['id']
        native_id=publish_in_project(c,pid)
        listing=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets']
        assert len(listing)==1
        aid=listing[0]['asset_id']
        assert aid!=native_id
        base=f'/api/v1/projects/{pid}/catalog/assets/{aid}'
        policy={'required_fields':['温度'], 'unique_key':['设备编号','采集时间'],
                'ranges':{'温度':{'min':-10,'max':150}},'max_missing_fraction':0}
        r=c.post(base+'/quality/policy',json=policy);assert r.status_code==201,r.text
        r=c.post(base+'/quality/evaluate');assert r.status_code==201,r.text
        report=r.json()
        assert report['status']=='blocked' and report['total_rows']==3
        assert {x['rule'] for x in report['issues']}=={
            'required:温度','key.duplicate','range.bounds:温度'}
        assert c.post(base+'/quality/evaluate').json()['reused'] is True
        assert c.get(f'/api/v1/projects/{other}/catalog/assets/{aid}/quality').status_code==404
        assert c.get(f'/api/v1/projects/{other}/catalog/assets/{aid}/versions').status_code==404
        assert c.get(f'/api/v1/projects/{pid}/quality/issues').json()['issues']
        issues=report['issues']
        for issue in issues:
            if issue['severity']=='blocking':
                r=c.post(f'/api/v1/projects/{pid}/quality/issues/{issue["issue_id"]}/decision',json={
                    'status':'acknowledged','reason':'工程师已核实该批次原始记录，保留问题证据'})
                assert r.status_code==200,r.text
        assert c.get(base+'/quality').json()['status']=='warning'
        renamed=c.patch(base,json={'name':'设备温度原始观测'}); assert renamed.status_code==200
        after=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets'][0]
        assert after['name']=='设备温度原始观测' and after['version_id']==listing[0]['version_id']
        assert c.get(base+'/integrity').json()['all_valid']
        assert c.get(f'/api/v1/projects/{pid}/governance/audit').json()['events']


def test_frozen_data_scope_and_origin_reproducibility(tmp_path):
    app,files=build_app_with_native_double(tmp_path)
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'冻结范围'}).json()['id']
        other=c.post('/api/v1/projects',json={'name':'不同项目'}).json()['id']
        native_id=publish_in_project(c,pid)
        url=f'/api/v1/projects/{pid}/selections'
        req={'native_asset_id':native_id,'version_id':'base',
             'filters':[{'column':'设备编号','op':'eq','value':'M1'}]}
        preview=c.post(url+'/preview',json=req)
        assert preview.status_code==200,preview.text
        assert preview.json()['row_count']==2
        assert c.post(url,json=req).status_code==422
        frozen=c.post(url,json={**req,'confirm':True})
        assert frozen.status_code==201,frozen.text
        sid=frozen.json()['selection_id']
        assert c.get(url+'/'+sid).json()['row_count']==2
        assert c.post(url,json={**req,'confirm':True}).json()['selection_id']==sid
        assert c.get(f'/api/v1/projects/{other}/selections/{sid}').status_code==404
        assert c.post(f'/api/v1/projects/{other}/selections/preview',json=req).status_code==404


def test_startup_gate_never_bootstraps_without_reviewed_build():
    root=Path(__file__).resolve().parents[1]
    source=(root/'scripts/launcher.py').read_text()
    ensure=(root/'scripts/ensure_native_bi.py').read_text()
    build=(root/'scripts/BUILD_OFFLINE_RELEASE.sh').read_text()
    assert "'--auto-build','--auto-bootstrap'" not in source
    assert "'--status-only'" in source
    assert 'npm ci' in build
    assert 'package-lock.json' in build
    assert '--with-native-bundle' in build
    assert 'pip install' not in ensure


def test_time_quality_detects_entity_gaps_and_misordered_rows(tmp_path):
    app,_=build_app_with_native_double(tmp_path)
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'秒级传感器质量'}).json()['id']
        text=('设备,时间,温度\n'
              'A,2026-11-10 09:00:00,20\n'
              'A,2026-11-10 09:01:00,21\n'
              'A,2026-11-10 09:05:00,23\n'
              'B,2026-11-10 09:00:00,19\n'
              'B,2026-11-10 09:00:00,20\n'
              'B,2026-11-10 08:59:00,18\n')
        up=c.post(f'/api/v1/projects/{pid}/imports',files=[('files',('sensor.csv',text.encode()))])
        assert up.status_code==201,up.text
        iid=up.json()['import_id']
        cid=c.get(f'/api/v1/projects/{pid}/imports/{iid}').json()['candidates'][0]['candidate_id']
        pub=c.post(f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general',
                   json={'name':'时序检查','confirm':True})
        assert pub.status_code==201,pub.text
        aid=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets'][0]['asset_id']
        base=f'/api/v1/projects/{pid}/catalog/assets/{aid}'
        request={'time_field':'时间','entity_field':'设备','max_gap_seconds':120,
                 'require_monotonic':True}
        resp=c.post(base+'/quality/policy',json=request)
        assert resp.status_code==201,resp.text
        result=c.post(base+'/quality/evaluate').json()
        counts={x['rule']:x['affected_count'] for x in result['issues']}
        assert counts['time.gap']==1 and counts['time.duplicate']==1 and counts['time.order']==1
        assert result['status']=='blocked'


def test_backup_runtime_roundtrip_including_sqlite(tmp_path):
    from timepilot.runtime_backup import create_backup,verify_backup,RuntimeBackupError
    import sqlite3
    home=tmp_path/'runtime';home.mkdir()
    with sqlite3.connect(home/'catalog.sqlite3') as db:
        db.execute('CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT)')
        db.execute('INSERT INTO items(name) VALUES(?)',('temperature',))
    raw=home/'assets'/'v1.csv';raw.parent.mkdir()
    raw.write_text('ts,temp\n2026-11-10,25\n',encoding='utf-8')
    output=tmp_path/'backup.zip'
    report=create_backup(home,output)
    assert report['verified'] and report['sqlite_snapshots']==1
    assert verify_backup(output)['files']==2
    with pytest.raises(RuntimeBackupError):
        create_backup(home,home/'bad.zip')


def test_version_comparison_detects_role_changes_without_confusing_column_names(tmp_path):
    app,_=build_app_with_native_double(tmp_path)
    with TestClient(app) as c:
        pid=c.post('/api/v1/projects',json={'name':'版本差异'}).json()['id']
        iid,cid=None,None
        text='time,temp,device\n2026-11-10 09:00,21,A\n2026-11-10 10:00,22,A\n'
        upload=c.post(f'/api/v1/projects/{pid}/imports',files=[('files',('sensor.csv',text.encode()))])
        assert upload.status_code==201,upload.text
        iid=upload.json()['import_id']
        cid=c.get(f'/api/v1/projects/{pid}/imports/{iid}').json()['candidates'][0]['candidate_id']
        url=f'/api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-general'
        versions=[]
        for roles in ({'time':'time'}, {'time':'time','target':'temp'}):
            resp=c.post(url,json={'name':'温度表','confirm':True,'roles':roles})
            assert resp.status_code==201,resp.text
            versions.append(resp.json()['meta']['version_id'])
        assert versions[0]!=versions[1]
        aid=c.get(f'/api/v1/projects/{pid}/data-catalog').json()['assets'][0]['asset_id']
        response=c.get(f'/api/v1/projects/{pid}/catalog/assets/{aid}/compare',params={
            'left':versions[0],'right':versions[1]})
        assert response.status_code==200,response.text
        diff=response.json()
        assert diff['added_fields']==[] and diff['removed_fields']==[]
        assert diff['role_changes']==[{'role':'target','field_before':None,'field_after':'temp'}]
        assert not diff['content_changed']
        assert len(c.get(f'/api/v1/projects/{pid}/catalog/assets/{aid}/versions').json()['versions'])==2


def test_sqlite_schema_preflight_backup_idempotent_and_integrity(tmp_path):
    import sqlite3
    import sys
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'scripts'))
    from preflight_schema import prepare
    home=tmp_path/'runtime';home.mkdir()
    source=home/'metadata.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE samples (x INTEGER)')
        db.execute('INSERT INTO samples VALUES(42)')
    first=prepare(home,'0.20.1')
    assert first['prepared'] and not first['reused'] and first['databases']==1
    second=prepare(home,'0.20.1')
    assert second['reused'] and second['path']==first['path']
    with sqlite3.connect(Path(first['path'])/'metadata.sqlite3') as db:
        assert db.execute('SELECT x FROM samples').fetchone()[0]==42
    assert source.is_file()


def test_normal_start_bootstraps_frontend_when_bundle_is_missing(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'scripts'))
    import ensure_native_bi as gate
    state={'probe':0};calls=[]
    def readiness():
        state['probe']+=1
        if state['probe']<3:
            return ({'ready':False,'problems':['perspective-build.json missing'],'version':'5.6.1'},False,[],True)
        return ({'ready':True,'problems':[],'version':'5.6.1'},True,[],True)
    monkeypatch.setattr(gate,'readiness',readiness)
    monkeypatch.setattr(gate,'FRONTEND',tmp_path/'frontend')
    monkeypatch.setattr(gate.shutil,'which',lambda _: '/usr/bin/npm')
    monkeypatch.setattr(gate.subprocess,'run',lambda argv,**kwargs: (calls.append((argv,kwargs)),SimpleNamespace(returncode=0))[1])
    monkeypatch.setattr(sys,'argv',['ensure_native_bi.py','--auto-build','--auto-bootstrap'])
    monkeypatch.delenv('TIMEPILOT_OFFLINE',raising=False)
    assert gate.main()==0
    assert len(calls)==1 and calls[0][0][-1]=='scripts/build_native_bi.py'
    assert calls[0][1]['env'].get('TIMEPILOT_SKIP_NPM_INSTALL') is None


def test_strict_offline_mode_refuses_missing_bundle_without_install(tmp_path,monkeypatch):
    import sys
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'scripts'))
    import ensure_native_bi as gate
    monkeypatch.setattr(gate,'readiness',lambda: (
        {'ready':False,'problems':['missing'],'version':'5.6.1'},False,[],True))
    monkeypatch.setattr(gate,'FRONTEND',tmp_path/'frontend')
    monkeypatch.setattr(gate.shutil,'which',lambda _: '/usr/bin/npm')
    monkeypatch.setenv('TIMEPILOT_OFFLINE','1')
    monkeypatch.setattr(sys,'argv',['ensure_native_bi.py','--auto-build','--auto-bootstrap'])
    assert gate.main()==2
