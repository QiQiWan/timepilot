"""Spreadsheet BI routes and curated LLM provider presets / protocols."""
from __future__ import annotations
from timepilot import __version__

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from timepilot.api import create_app


def make_api(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    return TestClient(create_app(home=tmp_path / "runtime", embedded_worker=False))


def upload(api, content: str, name="sample") -> str:
    response = api.post('/api/v1/bi/tables/upload', files={'file': ('sample.csv', content.encode(), 'text/csv')}, data={'name': name})
    assert response.status_code == 201, response.text
    return response.json()['table_id']


def test_sheet_paging_sort_filter_and_original_row_indexes(tmp_path, monkeypatch):
    with make_api(tmp_path, monkeypatch) as api:
        tid=upload(api,'name,amount,note\n张三,8,a\n李四,20,\n王五,13,b\n赵六,3,\n')
        path=f'/api/v1/bi/tables/{tid}/preview'
        result=api.get(path,params={'page_size':2,'sort_by':'amount','sort_dir':'desc'})
        assert result.status_code==200,result.text
        d=result.json()
        assert d['total']==4 and d['page_count']==2 and d['columns']==['name','amount','note']
        assert [row['amount'] for row in d['rows']]==['20','13']
        assert d['row_indexes']==[1,2]
        next_page=api.get(path,params={'page_size':2,'page':2,'sort_by':'amount','sort_dir':'desc'}).json()
        assert [row['amount'] for row in next_page['rows']]==['8','3']
        filtered=api.get(path,params={'filter_column':'note','filter_operator':'is_null'}).json()
        assert filtered['total']==2
        assert [r['name'] for r in filtered['rows']]==['李四','赵六']
        greater=api.get(path,params={'filter_column':'amount','filter_operator':'gt','filter_value':'10'}).json()
        assert greater['total']==2
        assert api.get(path,params={'sort_by':'non_existing'}).status_code==422
        assert api.get(path,params={'filter_operator':'bad','filter_column':'amount'}).status_code==422
        assert api.get(path,params={'sort_dir':'invalid','sort_by':'amount'}).status_code==422
        assert api.get(path,params={'filter_column':'amount','filter_operator':'gt','filter_value':'NaN!'}).status_code==422
        assert api.get(path,params={'search':'abc','page_size':3}).json()['rows']==[]


def test_sheet_draft_preview_and_cell_edit_is_immutable(tmp_path, monkeypatch):
    with make_api(tmp_path, monkeypatch) as api:
        tid=upload(api,'item,text,amount\nA,first,5\nB,second,10\nC,third,15\n')
        steps=[{'type':'set_cell','column':'text','row_index':1,'value':'修订内容'},
               {'type':'rename','column':'amount','new_name':'quantity'},
               {'type':'filter','column':'quantity','operator':'gt','value':'6'}]
        path=f'/api/v1/bi/tables/{tid}/transform'
        preview=api.post(path+'/preview',json={'name':'结果预览','steps':steps})
        assert preview.status_code==200,preview.text
        body=preview.json()
        assert body['before']['rows']==3 and body['after']['rows']==2
        assert body['rows'][0]['text']=='修订内容'
        assert body['preview_only'] is True
        assert len(api.get('/api/v1/bi/tables').json())==1
        saved=api.post(path,json={'name':'正式结果','steps':steps})
        assert saved.status_code==201,saved.text
        new_id=saved.json()['table_id']
        assert new_id!=tid
        assert api.get('/api/v1/bi/tables/'+tid+'/preview').json()['rows'][1]['text']=='second'
        new=api.get('/api/v1/bi/tables/'+new_id+'/preview').json()
        assert new['columns']==['item','text','quantity']
        assert new['rows'][0]['text']=='修订内容'
        invalid=api.post(path+'/preview',json={'name':'bad','steps':[{'type':'set_cell','column':'text','row_index':999,'value':'x'}]})
        assert invalid.status_code==422
        assert len(api.get('/api/v1/bi/tables').json())==2


def test_provider_catalogue_and_credential_isolation(tmp_path, monkeypatch):
    with make_api(tmp_path, monkeypatch) as api:
        presets=api.get('/api/v1/settings/llm/providers')
        assert presets.status_code==200
        items={x['id']:x for x in presets.json()}
        assert {'openai','deepseek','qwen-cn','zhipu','kimi','doubao','gemini','anthropic','openrouter','groq','ollama','custom'} <= items.keys()
        assert items['anthropic']['protocol']=='anthropic'
        assert items['deepseek']['base_url']=='https://api.deepseek.com'
        assert api.put('/api/v1/settings/llm',json={'provider_id':'unregistered'}).status_code==422
        old=api.put('/api/v1/settings/llm',json={'provider_id':'deepseek','provider_name':'DeepSeek','base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'old_provider_key','enabled':True})
        assert old.status_code==200
        assert old.json()['has_api_key'] is True
        new=api.put('/api/v1/settings/llm',json={'provider_id':'kimi','provider_name':'Kimi','base_url':'https://api.moonshot.cn/v1','model':'kimi-k2.5','enabled':True})
        assert new.status_code==200
        assert new.json()['has_api_key'] is False
        assert 'old_provider_key' not in str(api.get('/api/v1/settings/llm').json())
        assert api.get('/api/v1/settings/llm').json()['provider_id']=='kimi'


def test_anthropic_native_protocol_with_mock_server(tmp_path, monkeypatch):
    observed=[]
    def handler(request: httpx.Request):
        data=json.loads(request.content)
        observed.append((str(request.url),dict(request.headers),data))
        return httpx.Response(200,json={'model':data['model'],'content':[{'type':'text','text':'TimePilot OK'}], 'usage':{'input_tokens':5,'output_tokens':5}})
    with make_api(tmp_path, monkeypatch) as api:
        gateway=api.app.state.models
        gateway._http_client_factory=lambda **kwargs:httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
        saved=api.put('/api/v1/settings/llm',json={'provider_id':'anthropic','provider_name':'Anthropic · Claude',
           'base_url':'https://api.anthropic.com/v1','model':'claude-test-model','api_key':'anthropic-secret','enabled':True})
        assert saved.status_code==200,saved.text
        response=api.post('/api/v1/settings/llm/test')
        assert response.status_code==200,response.text
        assert response.json()['response']=='TimePilot OK'
        url,headers,payload=observed[0]
        assert url=='https://api.anthropic.com/v1/messages'
        assert headers['x-api-key']=='anthropic-secret'
        assert headers['anthropic-version']=='2023-06-01'
        assert payload['model']=='claude-test-model'
        assert isinstance(payload['messages'],list)


def test_frontend_sheet_assets_and_subroutes(tmp_path, monkeypatch):
    with make_api(tmp_path, monkeypatch) as api:
        tid=upload(api,'项目,内容\n年份,2020\n范围,部分结果\n')
        for route in ['/bi/table/'+tid, '/bi/table/'+tid+'/grid', '/bi/table/'+tid+'/fields',
                      '/bi/table/'+tid+'/prepare', '/bi/table/'+tid+'/reports', '/bi/table/'+tid+'/ai', '/bi/table/'+tid+'/forecast']:
            assert api.get(route).status_code==200,route
        for asset in ['bi-grid.js','bi-grid.css','settings-ui.js']:
            assert api.get('/assets/'+asset).status_code==200
        # Old analysis URL stays addressable, but its retired grid script is not loaded.
        assert f'/assets/project-bi-ui.js?v={__version__}' in api.get('/bi/table/'+tid).text
        assert '/assets/bi-grid.js?v=' not in api.get('/bi/table/'+tid).text
        assert api.get('/bi/table/'+tid+'/unexpected').status_code==404
