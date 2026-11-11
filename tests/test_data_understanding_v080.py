"""v0.8.0: permission, audit and real data-path regression for data agent."""
from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from timepilot.api import create_app
from timepilot.data_understanding import (Approval, DataUnderstandingService,
    ReadToolRequest, UnderstandingRequest)
from timepilot.smart_import import SmartImportError, SmartImportService


def book():
    wb=Workbook();w=wb.active;w.title='说明';w.append(['这是一份业务说明，不应进入训练'])
    w=wb.create_sheet('车型月度明细')
    w.append(['国家/地区','整车厂/品牌','车型','统计口径','来源','年份','1月','2月','3月'])
    w.append([' 中国 ','比亚迪','汉','销量','source-A',2024,12,13,None])
    w.append(['中国','比亚迪','海豚','产量','source-B',2024,6,-2,9])
    buf=io.BytesIO();wb.save(buf);return buf.getvalue()


def service(tmp_path, gateway=None):
    imports=SmartImportService(tmp_path)
    m=imports.ingest(io.BytesIO(book()),'2024销量.xlsx')
    return DataUnderstandingService(imports,gateway),imports,m


def poll(agent, iid, pid):
    for _ in range(100):
        outcome=agent.execution(iid,pid)
        if outcome['status'] not in {'queued','running'}:return outcome
        time.sleep(.05)
    pytest.fail('agent conversion job did not finish')


def test_deterministic_plan_is_read_only_and_tools_are_allowlisted(tmp_path):
    agent,imports,m=service(tmp_path)
    plan=agent.propose(m['import_id'],UnderstandingRequest())
    assert plan['advice']['origin']=='deterministic'
    assert plan['findings']['file.inspect']['total_files']==1
    assert plan['findings']['layout.detect']['structure']=='wide_monthly'
    assert plan['findings']['quality.evaluate']['basis']=='sample_only'
    assert plan['findings']['quality.evaluate']['counts']['missing']>=1
    assert any(i['code']=='scope_review' for i in plan['issues'])
    assert not m['versions']
    assert agent.plans(m['import_id'])[0]['plan_sha256']==plan['plan_sha256']
    assert agent.capabilities()['automatic_write'] is False
    with pytest.raises(SmartImportError,match='未授权'):
        agent.run_read_tool(m['import_id'],'series.build',ReadToolRequest())
    with pytest.raises(SmartImportError,match='未授权'):
        agent.run_read_tool(m['import_id'],'shell.execute',ReadToolRequest())
    assert agent.plan(m['import_id'],plan['plan_id'])==plan


def test_approval_requires_scope_consent_and_audit_version(tmp_path):
    agent,imports,m=service(tmp_path);iid=m['import_id']
    p=agent.propose(iid,UnderstandingRequest(name='中国月度数据'))
    with pytest.raises(SmartImportError,match='逐项'):
        agent.execute(iid,p['plan_id'],Approval(plan_sha256=p['plan_sha256'],confirm=True))
    with pytest.raises(SmartImportError,match='统计口径'):
        agent.execute(iid,p['plan_id'],Approval(plan_sha256=p['plan_sha256'],confirm=True,
                        acknowledged_issues=['scope_review','source_review']))
    decision=Approval(plan_sha256=p['plan_sha256'],confirm=True,
                      acknowledged_issues=['scope_review','source_review'],
                      scope_selection_mode='explicit',allowed_scopes=['销量'])
    agent.execute(iid,p['plan_id'],decision)
    done=poll(agent,iid,p['plan_id'])
    assert done['status']=='completed',done
    assert done['output_rows']==3
    assert done['training_started'] is False
    assert done['version_sha256']==imports.version(iid,done['version_id'])['sha256']
    assert done['next_url'].endswith('/workbench')
    actual=imports.preview_version(iid,done['version_id'])['rows']
    assert len(actual)==3
    assert actual[0]['country']=='中国'  # whitespace-cleaning is deterministic
    assert all(x['statistical_scope']=='销量' for x in actual)
    assert actual[0]['source_cell']=='G2'
    assert agent.execute(iid,p['plan_id'],decision)['version_id']==done['version_id']
    with pytest.raises(SmartImportError,match='不能变更'):
        agent.execute(iid,p['plan_id'],Approval(plan_sha256=p['plan_sha256'],confirm=True,
             acknowledged_issues=['scope_review','source_review'],scope_selection_mode='keep_separate'))


def test_keep_separate_scopes_no_silent_aggregation(tmp_path):
    agent,imports,m=service(tmp_path);iid=m['import_id']
    p=agent.propose(iid,UnderstandingRequest())
    reviews=[x['code'] for x in p['issues']]
    agent.execute(iid,p['plan_id'],Approval(plan_sha256=p['plan_sha256'],confirm=True,
                 acknowledged_issues=reviews,scope_selection_mode='keep_separate'))
    result=poll(agent,iid,p['plan_id'])
    assert result['status']=='completed',result
    assert result['scope_counts']=={'销量':3,'产量':3}
    assert imports.version(iid,result['version_id'])['counts']['negative_values']==1


def test_source_mutation_and_plan_tampering_are_rejected(tmp_path):
    agent,imports,m=service(tmp_path);iid=m['import_id']
    p=agent.propose(iid,UnderstandingRequest())
    plan_path=imports._dir(iid)/'understanding'/'plans'/f"{p['plan_id']}.json"
    obj=json.loads(plan_path.read_text());obj['name']='tampered'
    plan_path.write_text(json.dumps(obj))
    with pytest.raises(SmartImportError,match='已变动'):
        agent.plan(iid,p['plan_id'])
    plan_path.write_text(json.dumps(p,ensure_ascii=False))
    stored=imports._dir(iid)/m['files'][0]['stored']
    stored.write_bytes(stored.read_bytes()+b'corrupt')
    with pytest.raises(SmartImportError,match='校验失败'):
        agent.execute(iid,p['plan_id'],Approval(plan_sha256=p['plan_sha256'],confirm=True,
                     acknowledged_issues=[x['code'] for x in p['issues']],
                     scope_selection_mode='keep_separate'))


def test_ambiguous_code_columns_require_confirmed_target_and_series(tmp_path):
    csv=b'Time,A2,A14\n2023-01-01,M1,12\n2023-02-01,M2,25\n'
    imports=SmartImportService(tmp_path)
    m=imports.ingest(io.BytesIO(csv),'opaque.csv')
    agent=DataUnderstandingService(imports)
    plan=agent.propose(m['import_id'],UnderstandingRequest())
    assert 'semantic_review' in {i['code'] for i in plan['issues']}
    ack=[i['code'] for i in plan['issues']]
    with pytest.raises(SmartImportError,match='目标列'):
        agent.execute(m['import_id'],plan['plan_id'],Approval(
            plan_sha256=plan['plan_sha256'],confirm=True,acknowledged_issues=ack,
            allowed_scopes=['未标注']))
    decision=Approval(plan_sha256=plan['plan_sha256'],confirm=True,
                      acknowledged_issues=ack,allowed_scopes=['未标注'],
                      metric_column='A14',field_mapping={'series_id':'A2'})
    agent.execute(m['import_id'],plan['plan_id'],decision)
    result=poll(agent,m['import_id'],plan['plan_id'])
    assert result['status']=='completed',result
    data=imports.preview_version(m['import_id'],result['version_id'])['rows']
    assert [x['target'] for x in data]==['12','25']
    assert len({x['series_id'] for x in data})==2


def test_opt_in_model_only_sees_field_schema_not_original_values(tmp_path):
    class FakeGateway:
        def propose_data_understanding(self,request,schemas):
            self.request,self.schemas=request,schemas
            return {'origin':'llm_advisory','summary':'需要确认字段含义',
                    'recommended_analysis':['查看按月趋势'],'cautions':['不要混合产量与销量'],'executed':False}
    fake=FakeGateway();agent,imports,m=service(tmp_path,fake)
    p=agent.propose(m['import_id'],UnderstandingRequest(request='分析月度销量',use_model=True))
    assert p['advice']['origin']=='llm_advisory'
    assert fake.request=='分析月度销量'
    exposed=json.dumps(fake.schemas,ensure_ascii=False)
    assert '2024销量.xlsx' not in exposed
    assert 'source-A' not in exposed
    assert '中国' not in exposed
    assert '比亚迪' not in exposed
    assert agent.plans(m['import_id'])


def test_http_agent_plan_preview_consent_and_job(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as client:
        resp=client.post('/api/v1/smart-import/uploads',files={
            'file':('annual.xlsx',book(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert resp.status_code==201,resp.text
        m=resp.json();iid=m['import_id'];base=f'/api/v1/smart-import/imports/{iid}/agent'
        capability=client.get('/api/v1/smart-import/agent/capabilities')
        assert capability.status_code==200
        deny=client.post(base+'/tools/series.build/preview',json={})
        assert deny.status_code==422
        preview=client.post(base+'/tools/layout.detect/preview',json={})
        assert preview.status_code==200,preview.text
        p=client.post(base+'/plans',json={'name':'月度分析','candidate_ids':m['suggested_candidate_ids']})
        assert p.status_code==201,p.text
        plan=p.json()
        assert client.get(base+'/plans/'+plan['plan_id']).status_code==200
        assert client.get(base+'/plans').json()[0]['plan_id']==plan['plan_id']
        deny=client.post(base+'/plans/'+plan['plan_id']+'/execute',json={
            'plan_sha256':plan['plan_sha256'],'confirm':True})
        assert deny.status_code==422
        sent=client.post(base+'/plans/'+plan['plan_id']+'/execute',json={
            'plan_sha256':plan['plan_sha256'],'confirm':True,
            'acknowledged_issues':[x['code'] for x in plan['issues']],
            'scope_selection_mode':'keep_separate'})
        assert sent.status_code==202,sent.text
        for _ in range(100):
            finished=client.get(base+'/plans/'+plan['plan_id']+'/execution').json()
            if finished['status'] not in {'queued','running'}:break
            time.sleep(.05)
        assert finished['status']=='completed',finished
        assert client.get('/assets/smart-import-ui.js').status_code==200
        assert client.get(f'/imports/{iid}').status_code==200


def test_source_url_requires_review_even_when_first_sample_is_single_source(tmp_path):
    agent, imports, m=service(tmp_path)
    path=imports._dir(m['import_id'])/'manifest.json'
    obj=json.loads(path.read_text(encoding='utf-8'))
    for c in obj['candidates']:
        if c['kind']=='wide_monthly':
            # Real yearly workbooks identify the provider through 来源网址.
            c['columns']=[('来源网址' if x=='来源' else x) for x in c['columns']]
    path.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    plan=agent.propose(m['import_id'],UnderstandingRequest())
    assert {'scope_review','source_review'} <= {x['code'] for x in plan['issues']}
    assert plan['findings']['semantics.map']['fields'][0]['roles']['source_url']['suggested']=='来源网址'


def test_aggregate_and_detail_cannot_be_mixed_and_aggregate_requires_review(tmp_path):
    agent,imports,m=service(tmp_path)
    path=imports._dir(m['import_id'])/'manifest.json'
    obj=json.loads(path.read_text(encoding='utf-8'))
    candidate=next(c for c in obj['candidates'] if c['kind']=='wide_monthly')
    aggregate=dict(candidate)
    aggregate['candidate_id']='fake-aggregate'
    aggregate['sheet']='源表汇总'
    aggregate['columns']=list(candidate['columns']) + ['汇总标签']
    obj['candidates'].append(aggregate)
    path.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    with pytest.raises(SmartImportError,match='不能同批转换'):
        agent.propose(m['import_id'],UnderstandingRequest(candidate_ids=[candidate['candidate_id'],aggregate['candidate_id']]))
    plan=agent.propose(m['import_id'],UnderstandingRequest(candidate_ids=[aggregate['candidate_id']]))
    assert 'aggregate_review' in {x['code'] for x in plan['issues']}


def test_retry_revalidates_source_hash_before_starting_new_job(tmp_path,monkeypatch):
    agent,imports,m=service(tmp_path)
    p=agent.propose(m['import_id'],UnderstandingRequest())
    decision=Approval(plan_sha256=p['plan_sha256'],confirm=True,
                      acknowledged_issues=[x['code'] for x in p['issues']],
                      scope_selection_mode='keep_separate')
    # Simulate a previously audited job whose worker failed before conversion.
    receipt_dir=imports._dir(m['import_id'])/'understanding'/'executions'
    receipt_dir.mkdir(parents=True,exist_ok=True)
    from timepilot.data_understanding import _digest
    job_id='83abc86e-0ffd-48dd-a01e-564ca8cbf12a'
    receipt={'plan_id':p['plan_id'],'plan_sha256':p['plan_sha256'],
             'decision_sha256':_digest(decision.model_dump(mode='json')),
             'approval':decision.model_dump(mode='json'),
             'recipe_sha256':'x','source_sha256':p['source_sha256'],
             'job_id':job_id,'created_at':'2026-01-01T00:00:00+00:00','status':'queued'}
    receipt['receipt_sha256']=_digest(receipt)
    (receipt_dir/f"{p['plan_id']}.json").write_text(json.dumps(receipt),encoding='utf-8')
    job_dir=imports._dir(m['import_id'])/'jobs';job_dir.mkdir(exist_ok=True)
    (job_dir/f'{job_id}.json').write_text(json.dumps({'job_id':job_id,'import_id':m['import_id'],
        'status':'failed','version_id':None,'completed_candidates':0,
        'total_candidates':1,'output_rows':0,'error':'failure'}),encoding='utf-8')
    source=imports._dir(m['import_id'])/m['files'][0]['stored']
    source.write_bytes(source.read_bytes()+b'corrupt')
    def forbid(*a,**kw):raise AssertionError('must not start a new job with modified source')
    monkeypatch.setattr(imports,'start_transform',forbid)
    with pytest.raises(SmartImportError,match='校验失败'):
        agent.retry(m['import_id'],p['plan_id'],decision)


def test_untrusted_model_response_is_bounded_and_cannot_trigger_tool_execution(monkeypatch,tmp_path):
    from timepilot.llm import ModelGateway
    gateway=ModelGateway.__new__(ModelGateway)
    monkeypatch.setattr(gateway,'_message',lambda *args,**kwargs: {
        'content':json.dumps({'summary':'解释' * 300,'recommended_analysis':
                     ['只看时序']*7,'cautions':['尚未确认业务口径'],
                     'tool':'shell.execute','execute':True}),
        'model':'stub','usage':{}})
    out=gateway.propose_data_understanding('审查', [{'columns':['A2','A14'],'kind':'ordinary_timeseries'}])
    assert len(out['summary'])==400 and len(out['recommended_analysis'])==3
    assert 'tool' not in out and out['executed'] is False and out['schema_only'] is True
    monkeypatch.setattr(gateway,'_message',lambda *args,**kw:{'content':'not json','model':'stub','usage':{}})
    with pytest.raises(ValueError):gateway.propose_data_understanding('审查',[])
