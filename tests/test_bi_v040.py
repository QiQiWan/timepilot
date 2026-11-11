"""v0.4 BI formula/pivot/workflow regression and write safety."""
import json
from fastapi.testclient import TestClient
from timepilot.api import create_app
from timepilot.llm import ModelGateway


def api(tmp_path):
    return TestClient(create_app(home=tmp_path / "runtime", embedded_worker=False))


def upload(client, csv: str, name="订单事实表"):
    r=client.post("/api/v1/bi/tables/upload", files={"file": ("rows.csv", csv.encode("utf-8"), "text/csv")}, data={"name":name})
    assert r.status_code==201, r.text
    return r.json()["table_id"]


def post(client, path, payload, status=200):
    r=client.post(path,json=payload)
    assert r.status_code==status, r.text
    return r.json()


def test_calculated_column_preview_and_immutability(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"月份,销售额,成本,品类\n2026-01,100,60,A\n2026-02,50,0,B\n2026-03,30,,A\n")
        steps=[{"type":"calculate","new_name":"毛利","left_column":"销售额","right_column":"成本","operator":"subtract"},
               {"type":"calculate","new_name":"成本率","left_column":"成本","right_column":"销售额","operator":"percent"}]
        base=f"/api/v1/bi/tables/{table}/transform"
        r=post(client,base+"/preview",{"name":"preview","steps":steps})
        assert r["preview_only"] and r["rows"][0]["毛利"]==40.0
        assert r["rows"][0]["成本率"]==60.0
        assert r["rows"][2]["毛利"] is None
        assert len(client.get("/api/v1/bi/tables").json())==1
        saved=post(client,base,{"name":"新增毛利指标","steps":steps},201)
        actual=client.get(f"/api/v1/bi/tables/{saved['table_id']}/preview").json()
        assert float(actual["rows"][0]["毛利"])==40.0
        original=client.get(f"/api/v1/bi/tables/{table}/preview").json()
        assert "毛利" not in original["columns"]


def test_calculated_column_validation_and_divide_by_zero(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"id,a,b\nx,10,2\ny,9,0\nz,abc,1\n")
        path=f"/api/v1/bi/tables/{table}/transform/preview"
        good={"name":"preview","steps":[{"type":"calculate","new_name":"比率","left_column":"a","right_column":"b","operator":"divide"}]}
        assert client.post(path,json=good).status_code==422  # abc -> numeric error
        good["steps"][0]["left_column"]="b"
        good["steps"][0]["right_column"]="b"
        r=post(client,path,good)
        assert r["rows"][1]["比率"] is None
        bad=[{"type":"calculate","new_name":"unsafe","left_column":"a","right_column":"b","operator":"__import__"}]
        assert client.post(path,json={"name":"x","steps":bad}).status_code==422


def test_batch_edit_and_trim_replace(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"id,label\nA,  west  \nB, east \n")
        changes=[{"row_index":0,"column":"id","value":"A100"},{"row_index":1,"column":"label","value":"north "}]
        steps=[{"type":"batch_edit","changes":changes},{"type":"trim","column":"label"},
               {"type":"replace","column":"label","old":"west","new":"西部"}]
        r=post(client,f"/api/v1/bi/tables/{table}/transform/preview",{"name":"x","steps":steps})
        assert r["rows"][0]["id"]=="A100"
        assert r["rows"][0]["label"]=="西部"
        assert r["rows"][1]["label"]=="north"
        invalid={"name":"fail","steps":[{"type":"batch_edit","changes":[{"row_index":0,"column":"id","value":"A"}]*2}]}
        assert client.post(f"/api/v1/bi/tables/{table}/transform",json=invalid).status_code==422
        assert len(client.get("/api/v1/bi/tables").json())==1


def test_pivot_total_and_invalid_dimensions(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"month,category,amount\nJan,A,10\nJan,B,30\nFeb,A,20\nFeb,B,40\n")
        path=f"/api/v1/bi/tables/{table}/pivot"
        result=post(client,path,{"group_by":["month"],"pivot_column":"category","metric":{"column":"amount","operation":"sum"}})
        assert result["total_groups"]==2
        assert result["columns"]==["month","A","B"]
        data={r["month"]:r for r in result["rows"]}
        assert data["Jan"]["B"]==30
        assert data["Feb"]["A"]==20
        assert client.post(path,json={"group_by":["month"],"pivot_column":"month","metric":{"operation":"count"}}).status_code==422
        report=post(client,"/api/v1/bi/reports",{"name":"透视报表","table_id":table,"chart_type":"pivot", "pivot":{"group_by":["month"],"pivot_column":"category","metric":{"column":"amount","operation":"sum"}}},201)
        opened=client.get("/api/v1/bi/reports/"+report["report_id"]).json()
        assert opened["result"]["rows"]==result["rows"]


def test_pivot_category_cap(tmp_path):
    with api(tmp_path) as client:
        csv="group,value,amount\n"+"".join(f"all,cat{i},1\n" for i in range(31))
        table=upload(client,csv)
        r=client.post(f"/api/v1/bi/tables/{table}/pivot",json={"group_by":["group"],"pivot_column":"value", "metric":{"operation":"count"}})
        assert r.status_code==422


def test_workflow_preview_approval_atomic_new_snapshot(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"region,amount,cost\nEast,100,80\nEast,80,60\nWest,20,10\n")
        plan={"name":"收入分析结果","steps":[
            {"tool":"bi.transform","arguments":{"steps":[{"type":"calculate","left_column":"amount","right_column":"cost","operator":"subtract","new_name":"profit"}]}},
            {"tool":"bi.aggregate","arguments":{"group_by":["region"],"metric":{"column":"profit","operation":"sum"}}},
            {"tool":"bi.pivot","arguments":{"group_by":["region"],"pivot_column":"cost","metric":{"operation":"count"}}}
        ]}
        base=f"/api/v1/bi/tables/{table}/workflow"
        preview=post(client,base+"/preview",plan)
        assert preview["after"]["columns"]==4
        assert preview["steps"][1]["result"]["rows"]
        assert len(client.get("/api/v1/bi/tables").json())==1
        assert client.post(base+"/execute",json=plan).status_code==409
        result=post(client,base+"/execute?confirmed=true&preview_token="+preview["preview_token"],plan,201)
        assert client.post(base+"/execute?confirmed=true&preview_token="+preview["preview_token"],json=plan).status_code==422
        saved=result["saved_table"]
        assert saved["lineage"]["type"]=="workflow"
        assert len(client.get("/api/v1/bi/tables").json())==2
        assert "profit" in client.get(f"/api/v1/bi/tables/{saved['table_id']}/preview").json()["columns"]


def test_workflow_preflight_failure_cannot_create_partial_data(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"a,b\n4,2\n")
        plan={"name":"bad","steps":[{"tool":"bi.transform","arguments":{"steps":[{"type":"rename","column":"a","new_name":"new"}]}},
                                     {"tool":"bi.pivot","arguments":{"group_by":["unknown"],"pivot_column":"b","metric":{"operation":"count"}}}]}
        base=f"/api/v1/bi/tables/{table}/workflow"
        assert client.post(base+"/preview",json=plan).status_code==422
        assert client.post(base+"/execute?confirmed=true",json=plan).status_code==422
        assert len(client.get("/api/v1/bi/tables").json())==1
        assert client.post(base+"/execute?confirmed=true",json={"name":"hack","steps":[{"tool":"unsafe.python","arguments":{}}]}).status_code==422


def test_readonly_workflow_cannot_create_snapshot(tmp_path):
    with api(tmp_path) as client:
        table=upload(client,"group,a\nA,1\n")
        plan={"name":"report","steps":[{"tool":"bi.aggregate","arguments":{"group_by":["group"],"metric":{"operation":"count"}}}]}
        assert post(client,f"/api/v1/bi/tables/{table}/workflow/preview",plan)["writes_data"] is False
        assert client.post(f"/api/v1/bi/tables/{table}/workflow/execute?confirmed=true",json=plan).status_code==422


def test_assistant_bounded_workflow_proposal(tmp_path, monkeypatch):
    with api(tmp_path) as client:
        table=upload(client,"month,value\nJan,3\n")
        service=client.app.state.models
        service._message=lambda *_args, **_kw: {"content":json.dumps({"name":"只读分析","steps":[{"tool":"bi.aggregate","arguments":{"group_by":["month"],"metric":{"operation":"count"}}}]}),"usage":{}}
        result=service.plan_bi_workflow("按月份计数",client.app.state.bi.table(table))
        assert result["executed"] is False
        assert result["plan"]["steps"][0]["tool"]=="bi.aggregate"
        assert len(client.get("/api/v1/bi/tables").json())==1
        service._message=lambda *_args, **_kw: {"content":json.dumps({"name":"bad","steps":[{"tool":"bi.run_python","arguments":{}}]}),"usage":{}}
        import pytest
        with pytest.raises(ValueError,match="未授权"):
            service.plan_bi_workflow("run",client.app.state.bi.table(table))


def test_preview_token_is_bound_to_plan_and_expires(tmp_path):
    from timepilot.db import BIWorkflowPreviewRow
    from datetime import datetime, timezone, timedelta
    with api(tmp_path) as client:
        tid=upload(client,"x,y\n10,2\n")
        base=f"/api/v1/bi/tables/{tid}/workflow"
        plan={"name":"A","steps":[{"tool":"bi.transform","arguments":{"steps":[{"type":"calculate","left_column":"x","right_column":"y","operator":"subtract","new_name":"result"}]}}]}
        staged=post(client,base+"/preview",plan)
        tampered={**plan,"name":"B"}
        path=base+"/execute?confirmed=true&preview_token="+staged["preview_token"]
        assert client.post(path,json=tampered).status_code==422
        assert len(client.get('/api/v1/bi/tables').json())==1
        assert post(client,path,plan,201)['saved_table']['row_count']==1
        assert client.post(path,json=plan).status_code==422
        expired=post(client,base+"/preview",plan)
        store=client.app.state.client.store
        with store.sessions.begin() as sess:
            sess.get(BIWorkflowPreviewRow, expired['preview_token']).expires_at=datetime.now(timezone.utc)-timedelta(minutes=1)
        assert client.post(base+"/execute?confirmed=true&preview_token="+expired['preview_token'],json=plan).status_code==422
        assert len(client.get('/api/v1/bi/tables').json())==2


def test_xlsx_export_retains_identifiers_and_blocks_excel_formulas(tmp_path):
    from openpyxl import load_workbook
    from io import BytesIO
    with api(tmp_path) as client:
        table=upload(client, 'code,text,amount\n0001,"=2+3",4\n0002,"+cmd",5\n')
        response=client.get(f'/api/v1/bi/tables/{table}/export.xlsx')
        assert response.status_code==200
        wb=load_workbook(BytesIO(response.content),read_only=False)
        ws=wb['数据表']
        assert ws['A2'].value=='0001'
        assert ws['B2'].value=='=2+3'
        assert ws['B2'].data_type=='s'
        assert ws['C2'].value==4.0
        assert ws['B3'].value=='+cmd'
        assert '来源说明' in wb.sheetnames
        # the same snapshot can create an independently downloadable report
        report=post(client,'/api/v1/bi/reports',{'name':'按编码统计','table_id':table,'chart_type':'table','aggregate':{'group_by':['code'],'metric':{'operation':'count'}}},201)
        response=client.get('/api/v1/bi/reports/'+report['report_id']+'/export.xlsx')
        assert response.status_code==200
        wbr=load_workbook(BytesIO(response.content))
        assert wbr.active.max_row>=2
