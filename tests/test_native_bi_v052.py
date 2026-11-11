"""v0.5.1 native viewer contract + v0.5.2 versioning, security and integrity.

Unit tests run without optional DuckDB; full I/O integration is required in
Python 3.12.12 CI/workstation with the unified requirements.txt installed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.db import NativeBIAssetRow, NativeBIEditVersionRow
from timepilot.native_bi import NativeBIError, QuerySpec, ViewSpec, compile_query
from timepilot.native_bi_edit import (BASE_VERSION, CellEdit, EditBatch,
                                      RollbackRequest, UndoRequest)


def example(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    service = app.state.native_bi
    asset_id = str(uuid4())
    parquet = service._path(asset_id)
    parquet.parent.mkdir(parents=True)
    parquet.write_bytes(b'unchanged-base-bytes-for-metadata-tests')
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIAssetRow(
            id=asset_id, name='v052-fixture', row_count=1000000,
            columns_json=json.dumps([{'name':'区域','physical_type':'VARCHAR'},
                                     {'name':'销售额','physical_type':'DOUBLE'}]),
            lineage_json='{}', sha256=hashlib.sha256(parquet.read_bytes()).hexdigest(),
            file_bytes=parquet.stat().st_size))
    return app, service, asset_id, parquet


def test_edit_schema_rejects_duplicate_cell_and_illegal_edits():
    with pytest.raises(ValueError):
        EditBatch(asset_id='x', edits=[CellEdit(row_id=3,column='销售额',value='1'),
                                      CellEdit(row_id=3,column='销售额',value='2')])
    with pytest.raises(ValueError):
        EditBatch(asset_id='x', edits=[CellEdit(row_id=-1,column='销售额',value='1')])
    with pytest.raises(ValueError):
        EditBatch(asset_id='x', edits=[CellEdit(row_id=i,column='值',value='1') for i in range(5001)])
    with pytest.raises(ValueError):
        EditBatch(asset_id='x', edits=[CellEdit(row_id=1,column='值',value='z'*2100000)])
    with pytest.raises(ValueError):
        CellEdit(row_id=1,column='值',value=float('nan'))


def test_query_contract_can_include_internal_row_key_without_exposing_sql():
    spec=QuerySpec(asset_id='asset',include_row_ids=True,filters=[{'column':'区域','op':'eq','value':'east'}])
    sql,params,count=compile_query(spec,['区域','销售额'],source_sql='__tp_effective')
    assert '"__tp_rowid"' in sql
    assert 'FROM __tp_effective' in sql
    assert 'read_parquet(?)' not in sql
    assert params==['east']
    assert 'FROM __tp_effective' in count
    grouping=QuerySpec(asset_id='asset',group_by=['区域'],metrics=[{'operation':'sum','column':'销售额','alias':'sales'}])
    sql,_,_=compile_query(grouping,['区域','销售额'],source_sql='__tp_effective')
    assert 'SUM(TRY_CAST("销售额" AS DOUBLE))' in sql


def test_rollback_is_a_new_version_and_does_not_overwrite_parquet(tmp_path):
    app, service, asset, base = example(tmp_path)
    before = base.read_bytes()
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIEditVersionRow(id=str(uuid4()),asset_id=asset,
                                     kind='edit',depth=1,comment='unapplied-fixture'))
    record=service.history(asset)
    assert record['current_version']==BASE_VERSION
    with pytest.raises(NativeBIError,match='确认'):
        service.rollback(asset,RollbackRequest(expected_version='base',target_version=record['versions'][0]['version_id']))
    assert base.read_bytes()==before


def test_rollback_history_undo_and_redo_via_history(tmp_path):
    app, service, asset, base = example(tmp_path)
    version=str(uuid4())
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIEditVersionRow(id=version,asset_id=asset,kind='rollback',depth=1,
                                     fields_json='[]',comment='已保存的历史状态'))
    moved=service.rollback(asset,RollbackRequest(expected_version='base',target_version=version,confirmed=True))
    assert moved['restored_version']==version
    assert service._head(asset)==moved['version_id']
    reverted=service.undo(asset,UndoRequest(expected_version=moved['version_id'],confirmed=True))
    assert reverted['restored_version']=='base'
    assert service.history(asset)['current_version']==reverted['version_id']
    restored=service.rollback(asset,RollbackRequest(expected_version=reverted['version_id'],
                                                    target_version=version,confirmed=True))
    assert service._head(asset)==restored['version_id']
    assert service._versions_chain(asset,restored['version_id'])[-2].id==version
    assert base.read_bytes()==b'unchanged-base-bytes-for-metadata-tests'
    assert service.verify_history(asset)['valid']


def test_wrong_asset_and_stale_heads_are_refused(tmp_path):
    app, service, asset, _ = example(tmp_path)
    with pytest.raises(NativeBIError):
        service._version(asset,str(uuid4()))
    with pytest.raises(NativeBIError,match='版本已经更新'):
        service.rollback(asset,RollbackRequest(expected_version=str(uuid4()),target_version='base',confirmed=True))
    with pytest.raises(NativeBIError,match='原始数据'):
        service.undo(asset,UndoRequest(expected_version='base',confirmed=True))


def test_edit_validation_rejects_bad_columns_and_rows(tmp_path):
    _,service,asset,_=example(tmp_path)
    with pytest.raises(NativeBIError,match='字段'):
        service._validate_batch(EditBatch(asset_id=asset,edits=[CellEdit(row_id=0,column='不存在',value='1')]))
    with pytest.raises(NativeBIError,match='行编号'):
        service._validate_batch(EditBatch(asset_id=asset,edits=[CellEdit(row_id=1000001,column='销售额',value='1')]))


def test_version_file_corruption_detected_without_touching_base(tmp_path):
    app,service,asset,base=example(tmp_path)
    version=str(uuid4())
    patch=service._delta_path(asset,version);patch.parent.mkdir(parents=True)
    patch.write_text('row_id,column_name,value,is_null,depth\n1,销售额,999,0,1\n')
    digest=hashlib.sha256(patch.read_bytes()).hexdigest()
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIEditVersionRow(id=version,asset_id=asset,kind='edit',depth=1,
            delta_sha256=digest,fields_json='["销售额"]',edit_count=1))
    assert service.verify_history(asset)['valid']
    sql=service._effective_sql(asset,version,json.loads(service._asset(asset).columns_json))
    assert 'WITH __tp_ranked' in sql and 'LEFT JOIN' in sql
    assert 'read_parquet' in sql and 'read_csv' in sql
    patch.write_text('tampered')
    with pytest.raises(NativeBIError,match='校验失败'):
        service._effective_sql(asset,version,json.loads(service._asset(asset).columns_json))
    assert not service.verify_history(asset)['valid']
    assert service.verify_history(asset)['invalid_versions']==[version]
    assert service.verify(asset)['valid']


def test_api_versioning_routes_return_explicit_states(tmp_path):
    app,service,asset,_=example(tmp_path)
    with TestClient(app) as client:
        assert client.get(f'/api/v1/native-bi/assets/{asset}/versions').json()['current_version']=='base'
        assert client.get(f'/api/v1/native-bi/assets/{asset}/versions/verify').json()['valid']
        assert client.post(f'/api/v1/native-bi/assets/{asset}/undo',json={'expected_version':'base','confirmed':False}).status_code==422
        res=client.post('/api/v1/native-bi/edits/preview',json={'asset_id':asset,'edits':[{'row_id':0,'column':'BAD','value':'2'}]})
        assert res.status_code==422
        bad=client.post(f'/api/v1/native-bi/assets/{asset}/rollback',json={'expected_version':'base','target_version':str(uuid4()),'confirmed':True})
        assert bad.status_code==422
        missing=client.get(f'/api/v1/native-bi/assets/{str(uuid4())}/versions')
        assert missing.status_code==404
        assert client.get('/api/v1/native-bi/assets/'+asset).json()['head_version']=='base'


def test_saved_perspective_view_rejects_bad_revision_and_cross_asset(tmp_path):
    app, svc, asset, _ = example(tmp_path)
    workspace={'panels':{'grid':{'table':'timepilot_active','plugin':'Datagrid'}},
               'layout':{'type':'tab-layout','tabs':['grid']}}
    def view(source):
        return ViewSpec(asset_id=asset,name='业务视图',config={
            'format':'timepilot-perspective-v2','workspace':workspace,'source':source})
    saved=svc.save_view(view({'kind':'sample','version_id':'base','limit':1000}))
    assert svc.get_view(saved['view_id'])['config']['source']['version_id']=='base'
    with pytest.raises(NativeBIError,match='版本'):
        svc.save_view(view({'kind':'sample','version_id':str(uuid4())}))
    with pytest.raises(NativeBIError,match='资产或版本'):
        svc.save_view(view({'kind':'query','version_id':'base','query':{
            'asset_id':str(uuid4()),'version_id':'base','group_by':['区域'],
            'metrics':[{'operation':'sum','column':'销售额','alias':'sales'}]}}))
    correct={'kind':'query','version_id':'base','query':{'asset_id':asset,'version_id':'base',
             'group_by':['区域'],'metrics':[{'operation':'sum','column':'销售额','alias':'sales'}]}}
    saved_query=svc.save_view(view(correct))
    assert svc.get_view(saved_query['view_id'])['config']['source']['kind']=='query'
    with TestClient(app) as client:
        assert client.get(f'/bi/native/{asset}/view/{saved_query["view_id"]}').status_code==200


def test_native_frontend_uses_real_perspective_plugins_and_history():
    root=Path(__file__).resolve().parents[1]
    js=(root/'frontend/native-bi/src/main.js').read_text()
    ui=(root/'src/timepilot/static/native-bi.js').read_text()
    assert '@perspective-dev/viewer-datagrid' in js
    assert '@perspective-dev/viewer-charts' in js
    assert 'saveWorkspace()' in js and 'restoreWorkspace(' in js
    assert "name: ROOT_TABLE" in js
    assert 'rebindWorkspaceBase' in js
    helper=(root/'frontend/native-bi/src/viewer-contract.mjs').read_text()
    assert 'if (!metric) throw new Error' in helper
    assert 'init_server(' in js and 'init_client(' in js
    assert "'Y Bar'" in helper and "plugin: 'Datagrid'" in helper
    assert '/native-bi/edits/preview' in ui
    assert '/native-bi/edits/commit' in ui
    assert "data-native=\\\"undo\\\"" not in ui or 'data-native="undo"' in ui
    assert 'query.arrow' in ui


def test_real_duckdb_sparse_edit_lifecycle(tmp_path):
    pytest.importorskip('duckdb')
    pytest.importorskip('pyarrow')
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        src='编号,区域,销售额\n001,华南,10\n002,华北,20\n003,华南,30\n'.encode()
        create=client.post('/api/v1/native-bi/assets/upload',data={'name':'销售测试'},
            files={'file':('sales.csv',src,'text/csv')})
        assert create.status_code==201,create.text
        asset=create.json()['asset_id']
        base_sha=create.json()['sha256']
        preview=client.post('/api/v1/native-bi/edits/preview',json={'asset_id':asset,'edits':[
            {'row_id':0,'column':'销售额','value':'100'},
            {'row_id':1,'column':'区域','value':'华南'},
            {'row_id':2,'column':'销售额','value':None}], 'comment':'批量修改'})
        assert preview.status_code==200,preview.text
        token=preview.json()['preview_token']
        body={'batch':{'asset_id':asset,'expected_version':'base','edits':[
            {'row_id':0,'column':'销售额','value':'100'},
            {'row_id':1,'column':'区域','value':'华南'},
            {'row_id':2,'column':'销售额','value':None}], 'comment':'批量修改'},'preview_token':token}
        committed=client.post('/api/v1/native-bi/edits/commit',json=body)
        assert committed.status_code==201,committed.text
        head=committed.json()['version_id']
        assert client.post('/api/v1/native-bi/edits/commit',json=body).status_code==422
        q=client.post('/api/v1/native-bi/query',json={'asset_id':asset,'limit':20,'include_row_ids':True,'include_total':True}).json()
        assert q['version_id']==head and q['total']==3
        assert str(q['rows'][0]['销售额'])=='100' and q['rows'][1]['区域']=='华南'
        assert q['rows'][2]['销售额'] is None
        agg=client.post('/api/v1/native-bi/query',json={'asset_id':asset,'group_by':['区域'],
            'metrics':[{'column':'销售额','operation':'sum','alias':'sum_sales'}]}).json()
        assert agg['rows'][0]['sum_sales']==120.0
        old=client.post('/api/v1/native-bi/query',json={'asset_id':asset,'version_id':'base','limit':10}).json()
        assert str(old['rows'][0]['销售额'])=='10'
        assert client.get('/api/v1/native-bi/assets/'+asset).json()['sha256']==base_sha
        assert client.get('/api/v1/native-bi/assets/'+asset+'/versions/verify').json()['valid']
        arrow=client.post('/api/v1/native-bi/query.arrow',json={'asset_id':asset,'group_by':['区域'],
            'metrics':[{'column':'销售额','operation':'sum','alias':'sum_sales'}]})
        assert arrow.status_code==200 and len(arrow.content)>20
        undone=client.post(f'/api/v1/native-bi/assets/{asset}/undo',json={'expected_version':head,'confirmed':True})
        assert undone.status_code==201,undone.text
        revert=undone.json()['version_id']
        restored=client.post(f'/api/v1/native-bi/assets/{asset}/rollback',json={
            'expected_version':revert,'target_version':head,'confirmed':True})
        assert restored.status_code==201,restored.text
        restored_rows=client.post('/api/v1/native-bi/query',json={'asset_id':asset,'limit':10}).json()
        assert str(restored_rows['rows'][0]['销售额'])=='100'
