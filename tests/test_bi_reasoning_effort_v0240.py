"""Reasoning opt-in remains provider gated and does not expose thoughts."""
from timepilot.llm import ModelGateway

class Reply:
    def raise_for_status(self):return None
    def json(self):return {'choices':[{'message':{'content':'{"actions":[]}'}}],'model':'test-model','usage':{'total_tokens':12}}

class FakeClient:
    def __init__(self,collector,**kwargs):self.collector=collector
    def __enter__(self):return self
    def __exit__(self,*exc):return False
    def post(self,url,*,headers,json):
        self.collector['url']=url
        self.collector['body']=json
        return Reply()

def fake_gateway(provider,effort):
    gathered={}
    gateway=object.__new__(ModelGateway)
    gateway.config=lambda:{'enabled':True,'provider_id':provider,'model':'test-model',
        'base_url':'https://api.example.com/v1','reasoning_effort':effort,
        'temperature':0.3,'max_tokens':4096}
    gateway._key=lambda:'a-test-secret'
    gateway._http_client_factory=lambda **kw:FakeClient(gathered,**kw)
    return gateway,gathered

def test_reasoning_effort_only_for_explicit_openai_provider():
    gateway,wire=fake_gateway('openai','high')
    result=gateway._message([{'role':'user','content':'只输出 JSON'}],max_tokens=2800)
    assert result['model']=='test-model'
    assert wire['body']['reasoning_effort']=='high'
    assert wire['body']['max_completion_tokens']==2800
    assert 'temperature' not in wire['body'] and 'max_tokens' not in wire['body']
    assert 'reasoning_content' not in result
    gateway,wire=fake_gateway('qwen-cn','high')
    gateway._message([{'role':'user','content':'只输出 JSON'}])
    assert 'reasoning_effort' not in wire['body']
    assert wire['body']['max_tokens']==4096
    gateway,wire=fake_gateway('openai','off')
    gateway._message([{'role':'user','content':'只输出 JSON'}])
    assert 'reasoning_effort' not in wire['body']
