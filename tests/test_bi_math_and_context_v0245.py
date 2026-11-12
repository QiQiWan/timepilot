"""Regression against the two real failing instructions shown in the UI."""
from timepilot.bi_nl_math import parse_math_request
from timepilot.bi_context_builder import select_fields,select_tools
from timepilot.bi_tool_registry import validate_action

FIELDS=[{'name':f'A{i}','physical_type':'DOUBLE'} for i in range(1,201)]


def test_column_arithmetic_and_true_operand_order():
    a,why,missing=parse_math_request('请将 A8 列与 A10 列相加，结果放在一个新的列',FIELDS)
    assert missing is None and len(a)==1
    assert a[0].tool=='bi.expression.create'
    args=validate_action(a[0])
    assert args['expression']['left']['value']=='A8' and args['expression']['right']['value']=='A10'
    assert args['expression']['op']=='+'
    b,_,_=parse_math_request('A10列减去A8列生成新列',FIELDS)
    assert b[0].arguments['expression']['left']['value']=='A10'
    assert b[0].arguments['expression']['right']['value']=='A8'


def test_many_window_default_is_record_based_and_causal():
    a,why,missing=parse_math_request('对A2和A4字段计算移动平均，到新的列上',FIELDS)
    assert missing is None and len(a)==1
    assert a[0].tool=='bi.window.compute_many'
    args=validate_action(a[0]);assert args['columns']==['A2','A4']
    assert args['time_field']=='__tp_rowid' and args['window']==5
    assert '源记录顺序' in why
    assert parse_math_request('对A2计算最近300条的移动平均',FIELDS)[0]==[]


def test_unknown_or_string_field_must_not_execute():
    c=FIELDS+[{'name':'device_id','physical_type':'VARCHAR'}]
    assert parse_math_request('A301和A1相加生成新列',c)[0]==[]
    actions,_,reason=parse_math_request('对device_id计算移动平均',c)
    assert actions==[] and '数值' in reason


def test_retrieval_does_not_drop_late_columns_or_tools():
    ctx=select_fields('请统计 A198 和 A200 的均值',FIELDS,limit=12)
    assert ctx['total']==200 and ctx['omitted_count']==188
    assert set(ctx['mentioned'])=={'A198','A200'}
    assert {'A198','A200'}<={f['name'] for f in ctx['fields']}
    tools=[{'tool':f'bi.chart.extra{i}','available':True,'effect_class':'presentation','input_schema':{}} for i in range(60)]
    tools.append({'tool':'bi.expression.create','available':True,'effect_class':'presentation','input_schema':{'type':'object'}})
    result=select_tools('相加生成新列',tools,limit=5)
    assert any(t['name']=='bi.expression.create' for t in result['tools'])


def test_two_screenshot_commands_execute_through_workspace_api(tmp_path):
    """HTTP -> planner -> validated action -> state; numeric SQL has separate live gate."""
    from uuid import uuid4
    from test_conversational_bi_v0231 import _env
    app,client,pid,aid,service=_env(tmp_path)
    try:
        ws=client.post(f'/api/v1/projects/{pid}/bi-agent/workspaces',
                       json={'asset_id':aid,'version_id':'base'}).json()
        wid=ws['workspace_id']
        base=f'/api/v1/projects/{pid}/bi-agent/workspaces/{wid}/turns'
        message='将 A1 列与 A2 列相加，结果放在一个新的列'
        res=client.post(base,json={'message':message,'expected_revision':1,
                     'request_id':str(uuid4()),'execute':True})
        assert res.status_code==200,res.text
        applied=res.json()['workspace']
        assert len(applied['state']['expressions'])==1
        assert applied['state']['expressions'][0]['expression']['op']=='+'
        assert applied['state']['expressions'][0]['name']=='A1_plus_A2'
        assert applied['revision']==2
        # The second command operates against the same immutable asset and
        # adds two independent causal windows without discarding the sum.
        res=client.post(base,json={'message':'对A1和A2字段计算移动平均，到新的列上',
                     'expected_revision':2,'request_id':str(uuid4()),'execute':True})
        assert res.status_code==200,res.text
        state=res.json()['workspace']['state']
        assert len(state['windows'])==2
        assert {x['column'] for x in state['windows']}=={'A1','A2'}
        assert {x['time_field'] for x in state['windows']}=={'__tp_rowid'}
        assert len(state['expressions'])==1
        assert res.json()['workspace']['revision']==3
    finally:
        client.__exit__(None,None,None)


def test_grouped_aggregation_does_not_become_elementwise_addition():
    columns=[{'name':'设备','physical_type':'VARCHAR'},
             {'name':'温度','physical_type':'DOUBLE'}]
    actions,explanation,clarify=parse_math_request('按设备分组，计算温度求和值',columns)
    assert actions==[] and clarify is None
    from timepilot.bi_agent_service import BIWorkspaceService
    planner=BIWorkspaceService.__new__(BIWorkspaceService)
    actions=planner._deterministic_plan('按设备分组，计算温度求和值',['设备','温度'])
    assert len(actions)==1 and actions[0].tool=='bi.aggregate.run'
    assert actions[0].arguments['group_by']==['设备']
    assert actions[0].arguments['column']=='温度'
