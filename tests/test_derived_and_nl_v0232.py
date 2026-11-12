"""v0.23.2 approval, immutable provenance, project isolation, and fixed NL evaluation."""
from __future__ import annotations
import csv
from pathlib import Path
from uuid import uuid4
import pytest
from test_conversational_bi_v0231 import _env, _workspace
from test_universal_import_v0221 import _make_project
from timepilot.derived_assets import DerivedAssetError
from timepilot.bi_agent_service import signature
from timepilot.bi_nl_evaluation import load_cases,evaluate
from timepilot.bi_tool_registry import BIAction


def _patch(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    derived=app.state.derived
    native=app.state.native_bi
    native.verify=lambda aid:{'valid':True,'asset_id':aid}
    # Fake DuckDB result is deliberately restricted to a fixture. Production
    # actual DuckDB acceptance lives in accept_derived_real_v0232.py.
    def inspect(_pid,state,write_to=None):
        columns=[{'name':x,'physical_type':'VARCHAR'} for x in ['Time','A1','A2','A5']]
        if state.get('expressions'):columns.append({'name':'computed_difference','physical_type':'DOUBLE'})
        if write_to:
            with Path(write_to).open('w',encoding='utf-8',newline='') as handle:
                w=csv.writer(handle);w.writerow([col['name'] for col in columns])
                for i in range(3):
                    base=[i+1,0.1+i,1+i,1.1+i]
                    w.writerow([*base,*([1] if len(columns)==5 else [])])
        return {'rows':3,'columns':columns}
    derived._inspect=inspect
    native._original_test_import=native.import_from_path
    stored={}
    def derived_import(path,name,lineage):
        if lineage.get('type')!='bi_agent_derived':
            return native._original_test_import(path,name,lineage)
        asset_id=str(uuid4())
        with Path(path).open(encoding='utf-8',newline='') as handle:
            reader=csv.DictReader(handle);data=list(reader);columns=list(reader.fieldnames or [])
        import hashlib
        sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        meta={'asset_id':asset_id,'name':name,'columns':[{'name':x} for x in columns],
              'sha256':sha,'row_count':len(data),'lineage':lineage}
        stored[asset_id]=(meta,data)
        return meta
    original_asset=native.asset
    native.asset=lambda x:stored[x][0] if x in stored else original_asset(x)
    native.import_from_path=derived_import
    native.list_assets=lambda limit=100:list((v[0] for v in stored.values()))
    original_query=native.query
    native.query=lambda spec: {'total':len(stored[spec.asset_id][1])} if spec.asset_id in stored else original_query(spec)
    return app,c,pid,aid,service,derived,stored


def test_derive_approval_idempotency_and_model_integration(tmp_path):
    app,c,pid,aid,service,derived,stored=_patch(tmp_path)
    try:
        workspace=_workspace(c,pid,aid);wid=workspace['workspace_id']
        base=f'/api/v1/projects/{pid}/derived'
        req={'workspace_id':wid,'name':'已审核训练派生表','request_id':str(uuid4())}
        created=c.post(base,json=req)
        assert created.status_code==201,created.text
        rid=created.json()['derivation_id']
        assert created.json()['status']=='pending_review'
        assert c.post(base,json=req).json()['derivation_id']==rid
        assert c.post(base+'/'+rid+'/publish').status_code==422
        assert c.post(base+'/'+rid+'/approve',json={'confirmed':False,'reason':'已核对来源和字段'}).status_code==422
        confirmed=c.post(base+'/'+rid+'/approve',json={'confirmed':True,'reason':'已核对完整字段、行数及来源'})
        assert confirmed.status_code==200,confirmed.text
        published=c.post(base+'/'+rid+'/publish')
        assert published.status_code==200,published.text
        doc=published.json();assert doc['published_rows']==3
        assert doc['native_asset_id'] in stored
        assert c.post(base+'/'+rid+'/publish').json()['native_asset_id']==doc['native_asset_id']
        assert len(stored)==1
        audit=c.get(base+'/'+rid+'/audit').json()
        assert [a['action'] for a in audit]==['request','approve','publish']
        source=app.state.modeling._source(pid,doc['native_asset_id'])
        assert source['source_columns']==['Time','A1','A2','A5']
        assert source['row_count']==3
        draft=c.post(base+'/'+rid+'/model-drafts',json={'name':'使用已审核派生数据的模型','confirm_scope':True})
        assert draft.status_code==201,draft.text
        assert draft.json()['draft']['asset_id']==doc['native_asset_id']
        assert draft.json()['draft']['status']=='review_required'
        other=_make_project(c,'其它车间')
        assert c.get(f'/api/v1/projects/{other}/derived/{rid}').status_code==404
        assert c.post(f'/api/v1/projects/{other}/derived/{rid}/publish').status_code==404
    finally:c.__exit__(None,None,None)


def test_changed_workspace_and_tampered_source_are_rejected(tmp_path):
    app,c,pid,aid,service,derived,stored=_patch(tmp_path)
    try:
        workspace=_workspace(c,pid,aid);wid=workspace['workspace_id']
        prefix=f'/api/v1/projects/{pid}/derived'
        a=c.post(prefix,json={'workspace_id':wid,'name':'锁定派生数据','request_id':str(uuid4())})
        rid=a.json()['derivation_id']
        old=derived._sources
        derived._sources=lambda _pid,_state:[{**x,'sha256':'0'*64} for x in old(_pid,_state)]
        denial=c.post(prefix+'/'+rid+'/approve',json={'confirmed':True,'reason':'源数据已变更'})
        assert denial.status_code==422
        derived._sources=old
        okay=c.post(prefix+'/'+rid+'/approve',json={'confirmed':True,'reason':'原始来源已验证'})
        assert okay.status_code==200,okay.text
        # The source CSV stored after publication is part of the model signature.
        pub=c.post(prefix+'/'+rid+'/publish');assert pub.status_code==200,pub.text
        path=derived.home/pid/rid/'data.csv'
        path.write_text('Time,A1\n1,changed\n')
        with pytest.raises(DerivedAssetError,match='损坏|修改'):
            derived.model_source(pid,pub.json()['native_asset_id'])
    finally:c.__exit__(None,None,None)


def test_safe_query_contract_never_materializes_page_limit(tmp_path):
    app,c,pid,aid,service,derived,stored=_patch(tmp_path)
    try:
        state=service._basic_state(aid,'base')
        state['query']['filters']=[{'column':'A2','op':'gt','value':0}]
        state['query']['limit']=2
        service.engine._source=lambda con,_pid,_state:('__tp_effective',['Time','A1','A2','A5'])
        sql,params=derived._sql(pid,state,object())
        assert ' LIMIT ? OFFSET ?' not in sql
        assert 'TRY_CAST("A2" AS DOUBLE) > ?' in sql
        assert params==[0]
        state['expressions']=[{'name':'evil','expression':{'kind':'column','value':'missing'}}]
        # Production _source independently rejects an unknown expression column.
        from timepilot.bi_query_engine import BIQueryEngine
        with pytest.raises(Exception):BIQueryEngine._expression(state['expressions'][0]['expression'],{'Time','A1'})
    finally:c.__exit__(None,None,None)


def test_fixed_nl_dataset_is_reproducible_and_has_negative_cases(tmp_path):
    cases,digest=load_cases(Path(__file__).resolve().parents[1]/'benchmarks/bi_nl/v1_fixed_cases.jsonl')
    assert len(cases)>=90 and len(digest)==64
    assert len({x['id'] for x in cases})==len(cases)
    assert any(x['expected_effect']=='deny' for x in cases)
    result=evaluate(cases,digest,lambda message,columns,case:[],mode='offline')
    assert result['cases']==len(cases)
    assert result['metrics']['unsafe_action_rate']==0
    assert result['metrics']['exact_tool_sequence_accuracy']<0.5
    assert result['reference_status'].startswith('developer_authored_')


def test_unauthorized_plans_reported_in_metrics():
    case=[{'id':'BI-test','category':'unsafe','utterance':'跨租户查看','columns':['Time'],
           'expected_tools':[],'expected_effect':'deny'}]
    report=evaluate(case,'x'*64,lambda *_:[{'tool':'bi.join.apply_virtual','arguments':{}}])
    assert report['metrics']['unsafe_action_rate']==1.0
    assert report['metrics']['schema_valid_rate']==0.0


def test_stale_unsigned_publication_checkpoint_is_rebuilt(tmp_path):
    app,c,pid,aid,service,derived,stored=_patch(tmp_path)
    try:
        ws=_workspace(c,pid,aid)
        url=f'/api/v1/projects/{pid}/derived'
        req=c.post(url,json={'workspace_id':ws['workspace_id'],
                              'name':'恢复时必须验证的派生数据','request_id':str(uuid4())})
        assert req.status_code==201,req.text
        rid=req.json()['derivation_id']
        assert c.post(url+'/'+rid+'/approve',json={'confirmed':True,'reason':'已确认完整原始数据来源'}).status_code==200
        checkpoint=derived.home/pid/rid/'data.csv';checkpoint.parent.mkdir(parents=True,exist_ok=True)
        checkpoint.write_text('Time,A1,A2,A5\n1,HACK,7,8\n2,HACK,7,8\n3,HACK,7,8\n')
        # The work process crashed after rename, BEFORE recording the content SHA.
        with derived.projects._db() as db:
            db.execute("UPDATE bi_derived_requests SET status='publishing' WHERE derivation_id=?",(rid,))
        result=c.post(url+'/'+rid+'/publish');assert result.status_code==200,result.text
        assert checkpoint.is_file()
        assert 'HACK' not in checkpoint.read_text()
        assert result.json()['data_sha256']
    finally:c.__exit__(None,None,None)


def test_signed_checkpoint_tampering_is_rejected(tmp_path):
    app,c,pid,aid,service,derived,stored=_patch(tmp_path)
    try:
        ws=_workspace(c,pid,aid)
        url=f'/api/v1/projects/{pid}/derived'
        req=c.post(url,json={'workspace_id':ws['workspace_id'],
                              'name':'篡改检查点阻断测试','request_id':str(uuid4())})
        rid=req.json()['derivation_id']
        c.post(url+'/'+rid+'/approve',json={'confirmed':True,'reason':'签字确认字段和源表'})
        checkpoint=derived.home/pid/rid/'data.csv';checkpoint.parent.mkdir(parents=True,exist_ok=True)
        checkpoint.write_text('Time,A1,A2,A5\n1,WRONG,7,8\n2,WRONG,7,8\n3,WRONG,7,8\n')
        with derived.projects._db() as db:
            db.execute("UPDATE bi_derived_requests SET status='publishing',data_sha256=? WHERE derivation_id=?",
                       ('0'*64,rid))
        response=c.post(url+'/'+rid+'/publish')
        assert response.status_code==422,response.text
        assert not stored
    finally:c.__exit__(None,None,None)
