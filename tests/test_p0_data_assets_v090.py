"""P0 invariants + zero-copy canonical DataAsset bridge."""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.smart_import import SmartImportError, SmartImportService, TransformRecipe
from timepilot.data_assets import DataAssetCatalog, GridRequest, GroupRequest, PivotRequest, SemanticRevision, asset_id_for
from timepilot.smart_workbench import SaveView, SmartWorkbench, ViewSelection, DraftOptions


def source_csv(rows):
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['Time','A2','A14','available_at','source_name','统计口径'])
    writer.writerows(rows)
    return output.getvalue().encode('utf-8')


def setup(tmp_path,rows):
    imports=SmartImportService(tmp_path)
    manifest=imports.ingest(io.BytesIO(source_csv(rows)),'opaque_sales.csv')
    recipe=TransformRecipe(candidate_ids=manifest['suggested_candidate_ids'],
                           field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'})
    version=imports.transform(manifest['import_id'],recipe)
    workbench=SmartWorkbench(imports)
    catalog=DataAssetCatalog(imports,workbench)
    return imports,manifest,version,workbench,catalog


def test_real_available_at_and_entity_identity_are_preserved(tmp_path):
    records=[['2024-01-01','M1',10,'2024-02-05','provider-A','销量'],
             ['2024-01-01','M2',20,'2024-02-05','provider-A','销量'],
             ['2024-02-01','M1',12,'2024-03-05','provider-A','销量'],
             ['2024-02-01','M2',23,'2024-03-05','provider-A','销量']]
    imports,manifest,version,workbench,catalog=setup(tmp_path,records)
    with imports.version_file(manifest['import_id'],version['version_id']).open(encoding='utf-8') as handle:
        data=list(csv.DictReader(handle))
    assert [r['available_at'] for r in data]==['2024-02-05','2024-02-05','2024-03-05','2024-03-05']
    assert len({r['series_id'] for r in data})==2
    assert len({r['entity_id'] for r in data})==2
    assert version['counts'].get('conflicting_overlap',0)==0
    assert version['counts']['real_publication_times']==4
    analysis=workbench.analyze(manifest['import_id'],version['version_id'],ViewSelection())
    assert analysis['summary']['unpublished']==0
    assert analysis['summary']['conflict']==0


def test_opaque_entity_unmapped_refuses_transform_before_version_creation(tmp_path):
    imp=SmartImportService(tmp_path)
    source=imp.ingest(io.BytesIO(source_csv([
        ['2024-01-01','M1',10,'2024-02-05','provider-A','销量'],
        ['2024-01-01','M2',20,'2024-02-05','provider-A','销量']])), 'opaque.csv')
    with pytest.raises(SmartImportError,match='series_id'):
        imp.transform(source['import_id'],TransformRecipe(candidate_ids=source['suggested_candidate_ids']))
    assert imp.manifest(source['import_id'])['versions']==[]


def test_duplicate_is_same_entity_month_scope_not_other_entity(tmp_path):
    records=[['2024-01-01','M1',10,'2024-02-01','A','销量'],
             ['2024-01-01','M2',99,'2024-02-01','A','销量'],
             ['2024-01-01','M1',10,'2024-02-01','B','销量'],
             ['2024-01-01','M1',12,'2024-02-01','C','销量']]
    imports,manifest,version,_,_=setup(tmp_path,records)
    assert version['counts']['identical_overlap']==1
    assert version['counts']['conflicting_overlap']==1
    with imports.version_file(manifest['import_id'],version['version_id']).open(encoding='utf-8') as handle:
        result=list(csv.DictReader(handle))
    assert 'conflict_review' not in result[1]['quality_flags']
    assert 'duplicate_identical' not in result[1]['quality_flags']
    assert 'duplicate_identical' in result[2]['quality_flags']
    assert 'conflict_review' in result[3]['quality_flags']


def test_invalid_publication_and_missing_mapped_entity_fail_closed(tmp_path):
    for row,term in [(['2024-01-01','M1',10,'bad-date','A','销量'],'发布时间'),
                     (['2024-03-01','M1',10,'2024-02-01','A','销量'],'早于观测'),
                     (['2024-01-01','',10,'2024-02-01','A','销量'],'实体标识')]:
        imp=SmartImportService(tmp_path)
        m=imp.ingest(io.BytesIO(source_csv([row])),'bad.csv')
        with pytest.raises(SmartImportError,match=term):
            imp.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids'],
                field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'}))
        assert imp.manifest(m['import_id'])['versions']==[]


def test_asset_is_zero_copy_and_shares_workbench_view_and_filters(tmp_path):
    records=[['2024-01-01','M1',10,'2024-02-05','A','销量'],
             ['2024-02-01','M1',20,'2024-03-05','A','销量'],
             ['2024-01-01','M2',30,'2024-02-05','B','产量']]
    imp,m,v,workbench,catalog=setup(tmp_path,records)
    asset_id=asset_id_for(m['import_id']);version_id=v['version_id']
    assert catalog.asset(asset_id)['zero_copy']
    assert catalog.version(asset_id,version_id)['data_sha256']==v['sha256']
    assert not list((tmp_path/'data_assets').rglob('*.csv'))
    selection=ViewSelection(filters={'source_name':['A'],'statistical_scope':['销量']})
    result=catalog.analyze(asset_id,version_id,selection)
    assert result['summary']['rows']==2
    assert catalog.grid(asset_id,version_id,GridRequest(selection=selection))['rows'][0]['target']=='10'
    grouped=catalog.group(asset_id,version_id,GroupRequest(selection=selection,group_by='timestamp',operation='sum'))
    assert [r['value'] for r in grouped['rows']]==[10.,20.]
    with pytest.raises(SmartImportError,match='单一统计口径'):
        catalog.group(asset_id,version_id,GroupRequest(operation='sum'))
    view=catalog.save_view(asset_id,version_id,SaveView(name='A销量',selection=selection))
    assert catalog.view(asset_id,version_id,view['view_id'])['view_sha256']==view['view_sha256']
    assert workbench.views(m['import_id'],version_id)[0]['view_id']==view['view_id']
    assert str(imp.version_file(m['import_id'],version_id)) in catalog.native_sql_source(asset_id,version_id)


def test_semantics_is_append_only_and_pinned_to_source_hash(tmp_path):
    imp,m,v,_,catalog=setup(tmp_path,[['2024-01-01','M1',10,'2024-02-05','A','销量']])
    aid=asset_id_for(m['import_id']);vid=v['version_id']
    assert catalog.semantics(aid,vid)['confirmed'] is False
    with pytest.raises(SmartImportError,match='明确确认'):
        catalog.revise_semantics(aid,vid,SemanticRevision(roles={'time':'timestamp'}))
    one=catalog.revise_semantics(aid,vid,SemanticRevision(
        roles={'entity':'entity_id','time':'timestamp','availability':'available_at'},
        confirmed=True,note='业务方已复核'))
    assert one['confirmed'] and one['roles']['time']=='timestamp'
    assert len(list((tmp_path/'data_assets'/aid/vid/'semantics').glob('*.json')))==2
    assert catalog.semantics(aid,vid)['revision_sha256']==one['revision_sha256']
    from json import loads,dumps
    path=tmp_path/'data_assets'/aid/vid/'semantics'/(one['revision_sha256']+'.json')
    record=loads(path.read_text());record['roles']['time']='target';path.write_text(dumps(record))
    with pytest.raises(SmartImportError,match='SHA256'):
        catalog.semantics(aid,vid)


def test_http_unified_asset_view_workflow(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        upload=client.post('/api/v1/smart-import/uploads',files={'file':('x.csv',source_csv([
            ['2024-01-01','M1',10,'2024-02-05','A','销量'],
            ['2024-02-01','M1',20,'2024-03-05','A','销量']]),'text/csv')})
        assert upload.status_code==201,upload.text
        imported=upload.json();iid=imported['import_id']
        converted=client.post(f'/api/v1/smart-import/imports/{iid}/transform',json={
            'candidate_ids':imported['suggested_candidate_ids'],
            'field_mapping':{'series_id':'A2','target':'A14','source_name':'source_name'}})
        assert converted.status_code==201,converted.text
        vid=converted.json()['version_id'];aid=asset_id_for(iid)
        root=f'/api/v1/data-assets/{aid}/versions/{vid}'
        assert any(x['asset_id']==aid for x in client.get('/api/v1/data-assets').json())
        assert client.get(root).status_code==200
        analysis=client.post(root+'/analyze',json={'filters':{'source_name':['A']}})
        assert analysis.status_code==200,analysis.text
        assert analysis.json()['summary']['rows']==2
        grid=client.post(root+'/grid',json={'columns':['timestamp','series_id','available_at'],'limit':2})
        assert grid.status_code==200,grid.text
        assert grid.json()['rows'][0]['available_at']=='2024-02-05'
        assert client.post(root+'/group',json={'group_by':'timestamp','operation':'sum'}).status_code==200
        save=client.post(root+'/views',json={'name':'全部销量','selection':{'filters':{}}})
        assert save.status_code==201,save.text
        assert client.get(root+'/views/'+save.json()['view_id']).status_code==200
        invalid=client.post(root+'/grid',json={'columns':['illegal_sql; DROP TABLE observations']})
        assert invalid.status_code==422
        assert client.get('/data-assets').status_code==200
        assert client.get('/assets/data-assets-ui.js').status_code==200
        # Legacy APIs remain compatible; the project BI is the sole user-facing entry.
        assert 'project-bi-ui.js' in client.get('/').text
        assert 'data-assets-ui.js' not in client.get('/').text


def test_numeric_identifier_must_be_confirmed_even_if_other_column_is_numeric(tmp_path):
    """A2 (numeric production code) must never become an unverified target."""
    imp=SmartImportService(tmp_path)
    data=source_csv([['2024-01-01',101,10,'2024-02-05','A','销量'],
                     ['2024-01-01',102,20,'2024-02-05','A','销量']])
    manifest=imp.ingest(io.BytesIO(data),'numeric_identifiers.csv')
    with pytest.raises(SmartImportError,match='series_id'):
        imp.transform(manifest['import_id'],TransformRecipe(
            candidate_ids=manifest['suggested_candidate_ids'],field_mapping={'target':'A14'}))
    assert imp.manifest(manifest['import_id'])['versions']==[]
    version=imp.transform(manifest['import_id'],TransformRecipe(
        candidate_ids=manifest['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14'}))
    assert version['series_count']==2
    assert version['counts'].get('conflicting_overlap',0)==0


def test_timezone_naive_publication_requires_review(tmp_path):
    imp,m,v,workbench,catalog=setup(tmp_path,[
        ['2024-01-01','M1',10,'2024-02-05T08:30:00','A','销量']])
    with imp.version_file(m['import_id'],v['version_id']).open(encoding='utf-8') as f:
        row=next(csv.DictReader(f))
    assert row['available_at']=='2024-02-05T08:30:00'
    assert 'publication_timezone_unverified' in row['quality_flags']
    _,summary=workbench._evaluate_series(m['import_id'],v['version_id'],ViewSelection(),DraftOptions(min_history=2)),None
    # Regardless of history length, an unconfirmed time zone must be a blocker for this series.
    assert 'publication_timezone_unverified' in str(_)


def test_shared_view_route_and_native_reconnect_are_wired():
    root=Path(__file__).resolve().parents[1]
    frontend=(root/'src/timepilot/static/data-assets-ui.js').read_text(encoding='utf-8')
    workbench=(root/'src/timepilot/static/smart-workbench-ui.js').read_text(encoding='utf-8')
    assert "data-asset=\"load-view\"" in frontend
    assert '/workbench?view_id=' in frontend
    assert 'const id=new URLSearchParams(location.search)' in workbench
    assert 'async function nativePage(id,vid){await release();' in frontend
    assert frontend.index('await release();asset=')<frontend.index('await nativeModule.mountPerspectiveRemote(el,url)')
    assert '/api/v1/native-bi/linked/' in frontend


def test_native_view_uses_the_same_selection_and_escapes_literals(tmp_path):
    imports,m,v,_,catalog=setup(tmp_path,[['2024-01-01','M1',10,'2024-02-05','A','销量']])
    aid=asset_id_for(m['import_id'])
    selection=ViewSelection(filters={'source_name':["O'Brian"], 'statistical_scope':['销量']},
                            date_from='2024-01',date_to='2024-12')
    sql=catalog.native_sql_source(aid,v['version_id'],selection)
    assert "\"source_name\" IN ('O''Brian')" in sql
    assert "\"statistical_scope\" IN ('销量')" in sql
    assert "SUBSTR(\"timestamp\",1,7) >= '2024-01'" in sql
    assert "SUBSTR(\"timestamp\",1,7) <= '2024-12'" in sql
    assert 'ROW_NUMBER() OVER ()' in sql
    frontend=(Path(__file__).resolve().parents[1]/'src/timepilot/static/data-assets-ui.js').read_text()
    assert 'selection=${encodeURIComponent(JSON.stringify(selected))}' in frontend


def test_unified_pivot_reuses_selection_and_blocks_mixed_sum(tmp_path):
    imp,m,v,_,catalog=setup(tmp_path,[
        ['2024-01-01','M1',10,'2024-02-05','A','销量'],
        ['2024-02-01','M1',20,'2024-03-05','A','销量'],
        ['2024-01-01','M2',50,'2024-02-05','B','产量']])
    aid=asset_id_for(m['import_id']);vid=v['version_id']
    pivot=catalog.pivot(aid,vid,PivotRequest(row_field='source_name',column_field='statistical_scope'))
    assert sum(x['records'] for x in pivot['rows'])==3
    with pytest.raises(SmartImportError,match='单一统计口径'):
        catalog.pivot(aid,vid,PivotRequest(row_field='source_name',column_field='statistical_scope',operation='sum'))
    pivot=catalog.pivot(aid,vid,PivotRequest(row_field='series_id',column_field='timestamp',operation='sum',
        selection=ViewSelection(filters={'source_name':['A']})))
    assert [x['value'] for x in pivot['rows']]==[10.,20.]


def test_generic_bi_is_zero_copy_linked_and_uses_same_filters(tmp_path):
    """All old BI tools read the same immutable DataAsset, with bounded selection."""
    app=create_app(home=tmp_path,embedded_worker=False)
    imports=app.state.smart_import
    manifest=imports.ingest(io.BytesIO(source_csv([
        ['2024-01-01','M1',10,'2024-02-05','A','销量'],
        ['2024-01-01','M2',20,'2024-02-05','B','销量'],
        ['2024-02-01','M1',12,'2024-03-05','A','销量'],
        ['2024-03-01','M1',14,'2024-04-05','A','销量'],
    ])),'original.csv')
    version=imports.transform(manifest['import_id'],TransformRecipe(
        candidate_ids=manifest['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'}))
    aid=asset_id_for(manifest['import_id']);vid=version['version_id']
    root=f'/api/v1/data-assets/{aid}/versions/{vid}'
    with TestClient(app) as client:
        response=client.post(root+'/legacy-bi-link',json={
            'selection':{'filters':{'source_name':['A']},'date_from':'2024-01','date_to':'2024-02'}})
        assert response.status_code==201,response.text
        link=response.json();tid=link['table_id'];assert link['row_count']==2
        assert link['zero_copy'] and link['readonly_source']
        assert not (tmp_path/'bi'/tid/'data.csv').exists()
        assert client.post(root+'/legacy-bi-link',json={
            'selection':{'filters':{'source_name':['A']},'date_from':'2024-01','date_to':'2024-02'}}).json()['table_id']==tid
        grid=client.get(f'/api/v1/bi/tables/{tid}/preview')
        assert grid.status_code==200,grid.text
        rows=grid.json()['rows'];assert len(rows)==2
        assert all(x['source_name']=='A' for x in rows)
        assert [x['timestamp'] for x in rows]==['2024-01-01','2024-02-01']
        assert rows[0]['available_at']=='2024-02-05'
        profile=client.get(f'/api/v1/bi/tables/{tid}/profile')
        assert profile.status_code==200,profile.text
        assert profile.json()['rows']==2
        aggregated=client.post(f'/api/v1/bi/tables/{tid}/aggregate',json={
            'group_by':['source_name'],'metric':{'column':'target','operation':'sum'}})
        assert aggregated.status_code==200,aggregated.text
        converted=client.post(f'/api/v1/bi/tables/{tid}/transform',json={
            'name':'用户确认后的派生版本','steps':[{'type':'filter','column':'timestamp','operator':'eq','value':'2024-01-01'}]})
        assert converted.status_code==201,converted.text
        derived=converted.json();assert derived['row_count']==1
        assert (tmp_path/'bi'/derived['table_id']/'data.csv').exists()
        assert not (tmp_path/'bi'/tid/'data.csv').exists()
        assert imports.version_file(manifest['import_id'],vid).is_file()


def test_generic_bi_link_detects_tampered_source(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    imp=app.state.smart_import
    m=imp.ingest(io.BytesIO(source_csv([['2024-01-01','M1',10,'2024-02-05','A','销量']])), 'a.csv')
    v=imp.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'}))
    aid=asset_id_for(m['import_id']);vid=v['version_id']
    with TestClient(app) as client:
        r=client.post(f'/api/v1/data-assets/{aid}/versions/{vid}/legacy-bi-link',json={})
        assert r.status_code==201,r.text
        tid=r.json()['table_id']
        path=imp.version_file(m['import_id'],vid)
        with path.open('a',encoding='utf-8') as out:out.write('\n')
        preview=client.get(f'/api/v1/bi/tables/{tid}/preview')
        assert preview.status_code==422
        assert 'SHA256' in preview.text or '校验' in preview.text


def test_legacy_link_rejects_selection_over_memory_limit_without_sampling(tmp_path,monkeypatch):
    from timepilot.bi import MAX_ROWS
    app=create_app(home=tmp_path,embedded_worker=False)
    imp=app.state.smart_import
    m=imp.ingest(io.BytesIO(source_csv([['2024-01-01','M1',10,'2024-02-05','A','销量']])), 'a.csv')
    v=imp.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'}))
    app.state.workbench._connect(m['import_id'],v['version_id']).close()
    from timepilot.data_assets import DataAssetCatalog
    cat=app.state.data_assets
    class CountCursor:
        def fetchone(self):return (MAX_ROWS+1,)
    class CountConnection:
        def execute(self,*a):return CountCursor()
        def close(self):pass
    monkeypatch.setattr(cat.workbench,'_connect',lambda *a:CountConnection())
    with pytest.raises(SmartImportError,match='上限'):
        cat.link_legacy_bi(asset_id_for(m['import_id']),v['version_id'],ViewSelection())
    assert not app.state.bi.list_tables()


def test_cross_bi_navigation_preserves_unsaved_filters_on_refresh():
    root=Path(__file__).resolve().parents[1]
    frontend=(root/'src/timepilot/static/data-assets-ui.js').read_text(encoding='utf-8')
    assert 'data-asset="open-native"' in frontend
    assert "query.has('selection')" in frontend
    assert "U(JSON.stringify(selected))" in frontend
    assert 'data-asset="generic-bi"' in frontend
    assert 'legacy-bi-link' in frontend


def test_legacy_bi_rejects_unsafe_mixed_scope_metrics_and_training(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    imp=app.state.smart_import
    m=imp.ingest(io.BytesIO(source_csv([
        ['2024-01-01','M1',10,'2024-02-05','A','销量'],
        ['2024-01-01','M1',20,'2024-02-05','B','产量'],
    ])),'mixed.csv')
    v=imp.transform(m['import_id'],TransformRecipe(candidate_ids=m['suggested_candidate_ids'],
        field_mapping={'series_id':'A2','target':'A14','source_name':'source_name'}))
    aid=asset_id_for(m['import_id'])
    with TestClient(app) as client:
        linked=client.post(f'/api/v1/data-assets/{aid}/versions/{v["version_id"]}/legacy-bi-link',json={})
        assert linked.status_code==201,linked.text
        tid=linked.json()['table_id']
        count=client.post(f'/api/v1/bi/tables/{tid}/aggregate',json={'metric':{'operation':'count'}})
        assert count.status_code==200,count.text
        unsafe=client.post(f'/api/v1/bi/tables/{tid}/aggregate',json={'metric':{'column':'target','operation':'sum'}})
        assert unsafe.status_code==422
        assert '统计口径' in unsafe.text
        pivot=client.post(f'/api/v1/bi/tables/{tid}/pivot',json={
            'group_by':['country'],'pivot_column':'source_name','metric':{'column':'target','operation':'sum'}})
        assert pivot.status_code==422
        attempt=client.post(f'/api/v1/bi/tables/{tid}/to-timeseries',json={
            'name':'不安全训练集','frequency':'MS','timestamp_col':'timestamp',
            'target_col':'target','entity_col':'series_id','available_at_col':'available_at',
            'source_timezone':'Asia/Shanghai','unit':'unit'})
        assert attempt.status_code==422
        assert '时序工作台' in attempt.text
        assert not app.state.client.store.list_datasets()
