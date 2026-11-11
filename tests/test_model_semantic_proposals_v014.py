"""LLM role suggestions remain non-executable and source-column-bound."""
import json
from timepilot.llm import ModelGateway


def test_model_column_suggestions_are_restricted_to_schema(monkeypatch):
    model=ModelGateway.__new__(ModelGateway)
    def fake(*a,**kw):
        return {'content':json.dumps({'summary':'列名需要业务核实',
                  'recommended_analysis':['先确认实体'], 'cautions':[],
                  'field_roles':[
                    {'candidate_id':'abc','role':'series_id','column':'A2','reason':'可能是实体编码'},
                    {'candidate_id':'abc','role':'target','column':'nonexistent','reason':'无效字段'},
                    {'candidate_id':'abc','role':'shell','column':'A14','reason':'越权'},
                    {'candidate_id':'other','role':'target','column':'A14','reason':'越权表'},
                    {'candidate_id':'abc','role':'target','column':'A14','reason':'需人工确认'}]}),
                'model':'test-stub','usage':{'total_tokens':40}}
    monkeypatch.setattr(model,'_message',fake)
    result=model.propose_data_understanding('分析数据',[{'candidate_id':'abc','columns':['Time','A2','A14'],'kind':'ordinary_timeseries'}])
    assert len(result['field_roles'])==2
    assert all(x['status']=='suggested_unconfirmed' for x in result['field_roles'])
    assert not result['executed'] and result['schema_only']
    assert 'shell' not in json.dumps(result['field_roles'])
