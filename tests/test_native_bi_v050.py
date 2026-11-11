"""Native BI contract, SQL allowlist, migration and backward-compatibility tests.

DuckDB-dependent end-to-end tests intentionally skip if the runtime cannot
install the optional binary. See scripts/benchmark_native_bi.py for data scale.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.db import NativeBIAssetRow
from timepilot.native_bi import (
    FilterSpec, MetricSpec, NativeBIError, NativeBIService, OrderSpec,
    QuerySpec, ViewSpec, compile_query, quote_identifier
)


def contract(columns=None):
    return columns or ["产品编号", "区域", "销售额", "时间"]


def test_safe_identifier_and_bound_value():
    malicious = "a' OR 1=1--"
    spec = QuerySpec(asset_id=str(uuid4()), filters=[FilterSpec(column="区域", op="eq", value=malicious)])
    sql, params, count = compile_query(spec, contract())
    assert malicious not in sql and malicious not in count
    assert params == [malicious]
    assert '"区域" = ?' in sql
    assert '"__tp_rowid"' in sql
    assert quote_identifier('a"b') == '"a""b"'


def test_no_arbitrary_sql_accepted():
    for invalid in ["销售额; DROP TABLE t; --", "不存在", "__tp_rowid"]:
        with pytest.raises(NativeBIError):
            compile_query(QuerySpec(asset_id="a", columns=[invalid]), contract())
    with pytest.raises(NativeBIError):
        compile_query(QuerySpec(asset_id="a", filters=[FilterSpec(column="区域", op="contains", value="z" * 513)]), contract())
    with pytest.raises(NativeBIError):
        compile_query(QuerySpec(asset_id="a", filters=[FilterSpec(column="销售额", op="gt", value="15")]), contract())


def test_group_metrics_and_filter_are_allowlisted():
    spec=QuerySpec(asset_id="a", group_by=["区域"], metrics=[MetricSpec(operation="sum",column="销售额",alias="revenue")],
                   filters=[FilterSpec(column="区域",op="in",value=["华南","华东"])],
                   order_by=[OrderSpec(field="revenue",descending=True)],limit=25,include_total=True)
    sql,params,count=compile_query(spec,contract())
    assert 'SUM(TRY_CAST("销售额" AS DOUBLE)) AS "revenue"' in sql
    assert 'GROUP BY "区域"' in sql
    assert 'ORDER BY "revenue" DESC' in sql
    assert params==["华南","华东"]
    assert count.startswith('SELECT COUNT(*) FROM (SELECT')
    with pytest.raises(NativeBIError):
        compile_query(QuerySpec(asset_id="a", group_by=["区域"], metrics=[MetricSpec(operation="sum",column="销售额",alias="sales")],
                                order_by=[OrderSpec(field="时间")]),contract())


def test_query_limits_and_duplicate_fields():
    with pytest.raises(Exception):
        QuerySpec(asset_id="a",limit=1001)
    with pytest.raises(Exception):
        QuerySpec(asset_id="a",offset=2_000_001)
    with pytest.raises(NativeBIError):
        compile_query(QuerySpec(asset_id="a", columns=["区域","区域"]),contract())
    with pytest.raises(Exception):
        MetricSpec(operation="count",alias="区域")  # aliases are stable ASCII identifiers
    with pytest.raises(NativeBIError):
        compile_query(QuerySpec(asset_id="a", filters=[FilterSpec(column="产品编号", op="in",value=[])]),contract())


def test_native_api_routes_and_local_mode(tmp_path):
    with TestClient(create_app(home=tmp_path, embedded_worker=False)) as client:
        assert client.get('/bi/native').status_code==200
        assert client.get('/bi/native/'+str(uuid4())).status_code==200
        assert client.get('/bi/native/foo/extra').status_code==404
        res=client.get('/api/v1/native-bi/capabilities')
        assert res.status_code==200 and 'duckdb' in res.json()
        assert client.get('/api/v1/native-bi/assets').json()==[]
        assert client.get('/api/v1/native-bi/assets/missing').status_code==404
        bad=client.post('/api/v1/native-bi/query',json={'asset_id':'nonexistent','limit':1001})
        assert bad.status_code==422
        html=client.get('/').text
        assert '/assets/native-bi.js?v=0.22.1' in html


def test_view_save_is_json_and_versioned(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    ident=str(uuid4())
    file=native._path(ident)
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(b'fake-parquet-for-metadata-only')
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIAssetRow(id=ident,name='demo',row_count=1,
          columns_json=json.dumps([{'name':'a','physical_type':'VARCHAR'}]),lineage_json='{}',
          sha256=hashlib.sha256(file.read_bytes()).hexdigest(),file_bytes=file.stat().st_size))
    view=native.save_view(ViewSpec(asset_id=ident,name='区域销售',config={'group_by':['a'],'plugin':'Datagrid'}))
    assert native.get_view(view['view_id'])['config']['group_by']==['a']
    assert len(native.list_views(ident))==1
    with pytest.raises(NativeBIError):
        native.save_view(ViewSpec(asset_id=ident,name='too large',config={'x':'a'*66000}))


def test_legacy_migration_with_duckdb_missing(tmp_path):
    import importlib.util
    if importlib.util.find_spec('duckdb'):
        pytest.skip('Actual migration covered by optional DuckDB integration test')
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as c:
        res=c.post('/api/v1/bi/tables/upload',data={'name':'old'},files={'file':('old.csv',b'id,value\n001,2\n','text/csv')})
        assert res.status_code==201
        ident=res.json()['table_id']
        r=c.post('/api/v1/native-bi/assets/from-legacy/'+ident)
        assert r.status_code==503 and 'DuckDB' in r.json()['detail']
        assert c.get('/api/v1/bi/tables/'+ident).status_code==200


def test_real_duckdb_end_to_end_when_available(tmp_path):
    pytest.importorskip('duckdb')
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as c:
        original=c.post('/api/v1/bi/tables/upload',data={'name':'source'},files={'file':('rows.csv','编号,区域,销售额\n001,华南,10\n002,华南,20\n003,华东,5\n'.encode(),'text/csv')}).json()
        r=c.post('/api/v1/native-bi/assets/from-legacy/'+original['table_id'])
        assert r.status_code==201,r.text
        asset=r.json()['asset_id']
        rerun=c.post('/api/v1/native-bi/assets/from-legacy/'+original['table_id'])
        assert rerun.status_code==201 and rerun.json()['asset_id']==asset
        q=c.post('/api/v1/native-bi/query',json={'asset_id':asset,'columns':['编号','销售额'],'limit':10,'include_total':True})
        assert q.status_code==200,q.text
        assert q.json()['rows'][0]['编号']=='001' and q.json()['total']==3
        agg=c.post('/api/v1/native-bi/query',json={'asset_id':asset,'group_by':['区域'],
            'metrics':[{'column':'销售额','operation':'sum','alias':'revenue'}],'order_by':[{'field':'区域'}]})
        assert agg.status_code==200,agg.text
        assert {x['区域']:x['revenue'] for x in agg.json()['rows']}=={'华东':5,'华南':30}
        arrow=c.get('/api/v1/native-bi/assets/'+asset+'/sample.arrow')
        assert arrow.status_code==200
        assert len(arrow.content)>40
        assert c.get('/api/v1/native-bi/assets/'+asset+'/verify').json()['valid']


def test_llm_native_query_draft_read_only_and_schema_guard(tmp_path, monkeypatch):
    app=create_app(home=tmp_path,embedded_worker=False)
    from timepilot.llm import ModelGateway
    model=app.state.models
    dataset={'asset_id':'frozen_asset','name':'销量',
             'columns':[{'name':'区域','physical_type':'VARCHAR'},
                        {'name':'销量','physical_type':'BIGINT'}]}
    monkeypatch.setattr(model,'_message',lambda *_args,**_kw:{'content':json.dumps({
        'asset_id':'attempted_other_asset','group_by':['区域'],
        'metrics':[{'operation':'sum','column':'销量','alias':'sales'}],
        'limit':50}), 'usage':{'input':100}})
    draft=model.plan_native_query('按区域统计销量',dataset)
    assert draft['plan']['asset_id']=='frozen_asset' and not draft['executed']
    assert draft['plan']['metrics'][0]['alias']=='sales'
    monkeypatch.setattr(model,'_message',lambda *_args,**_kw:{'content':json.dumps({
        'asset_id':'frozen_asset','group_by':['意外字段'],
        'metrics':[{'operation':'sum','column':'销量','alias':'sales'}], 'limit':20}), 'usage':{}})
    with pytest.raises(NativeBIError):
        model.plan_native_query('操作恶意字段',dataset)


def test_native_to_timeseries_refuses_large_model_dataset(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    ident=str(uuid4())
    file=native._path(ident)
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(b'fake-parquet-for-metadata-only')
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIAssetRow(id=ident,name='large',row_count=1000000,
          columns_json=json.dumps([{'name':x,'physical_type':'VARCHAR'} for x in
              ['series_id','timestamp','target','available_at']]),lineage_json='{}',
          sha256=hashlib.sha256(file.read_bytes()).hexdigest(),file_bytes=file.stat().st_size))
    with pytest.raises(NativeBIError,match='200,000'):
        native.to_timeseries(ident,name='too_big',frequency='MS',timestamp_col='timestamp',
            target_col='target',entity_col='series_id',available_at_col='available_at',
            source_timezone='Asia/Shanghai',unit='vehicles')


def test_perspective_bundle_state_is_explicit(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    capabilities=app.state.native_bi.capabilities()
    assert isinstance(capabilities['perspective_bundle_ready'],bool)
    # Updated capabilities include the real Virtual Server readiness states.
    if not capabilities['duckdb']:
        assert capabilities['perspective_mode'] == 'unavailable'
    elif capabilities['perspective_virtual_server_ready']:
        assert capabilities['perspective_mode'] == 'virtual_duckdb_and_arrow_fallback'
        assert capabilities['perspective_bundle_ready'] is True
    else:
        assert capabilities['perspective_mode'] == 'build_or_dependency_required'

def test_vite_wasm_base_matches_fastapi_static_mount():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    vite=(root/'frontend/native-bi/vite.config.mjs').read_text(encoding='utf-8')
    assert "base: '/assets/native-bi/'" in vite
    assert (root/'frontend/native-bi/public/README.txt').is_file()
    assert 'assetsInlineLimit: 0' in vite
    assert 'rollupOptions:' in vite
    assert 'lib:' not in vite


def test_corrupted_or_missing_asset_is_reported_in_verification(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    ident=str(uuid4())
    path=native._path(ident)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'expected')
    with app.state.client.store.sessions.begin() as sess:
        sess.add(NativeBIAssetRow(id=ident,name='file-integrity',row_count=1,
            columns_json='[{"name":"x","physical_type":"VARCHAR"}]',lineage_json='{}',
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),file_bytes=path.stat().st_size))
    assert native.verify(ident)['valid'] is True
    path.write_bytes(b'altered!')
    assert native.verify(ident)['reason']=='checksum_mismatch'
    path.unlink()
    assert native.verify(ident)['reason']=='snapshot_missing'


def test_timeseries_bridge_rejects_duplicate_semantic_fields(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    native=app.state.native_bi
    ident=str(uuid4())
    file=native._path(ident)
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(b'fake-metadata-only')
    with app.state.client.store.sessions.begin() as s:
        s.add(NativeBIAssetRow(id=ident,name='demo',row_count=2,
            columns_json=json.dumps([{'name':x,'physical_type':'VARCHAR'} for x in ['series_id','timestamp','target','available_at']]),
            lineage_json='{}',sha256=hashlib.sha256(file.read_bytes()).hexdigest(),file_bytes=file.stat().st_size))
    with pytest.raises(NativeBIError, match='不能重复'):
        native.to_timeseries(ident,name='bad',frequency='MS',timestamp_col='timestamp',
            target_col='timestamp',entity_col='series_id',available_at_col='available_at',
            source_timezone='Asia/Shanghai',unit='vehicles')
