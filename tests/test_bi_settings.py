"""BI public workflow, immutable lineage and LLM configuration/permission tests."""
from pathlib import Path

from fastapi.testclient import TestClient

from timepilot.api import create_app


def make_http(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    return TestClient(create_app(home=tmp_path / "timepilot", embedded_worker=False))


def upload(http, name, csv):
    resp = http.post("/api/v1/bi/tables/upload", data={"name": name},
                     files={"file": (name + ".csv", csv.encode("utf-8"), "text/csv")})
    assert resp.status_code == 201, resp.text
    return resp.json()["table_id"]


def test_bi_import_profile_preview_transform_aggregate_report(tmp_path, monkeypatch):
    with make_http(tmp_path, monkeypatch) as http:
        table = upload(http, "销量明细", "product,month,sales,comment\n001,2026-01,12,x\n002,2026-01,18,\n001,2026-02,20,x\n")
        profile = http.get(f"/api/v1/bi/tables/{table}/profile")
        assert profile.status_code == 200
        fields = {f["name"]: f for f in profile.json()["fields"]}
        assert fields["sales"]["type"] == "numeric"
        assert fields["comment"]["null_count"] == 1
        preview = http.get(f"/api/v1/bi/tables/{table}/preview")
        assert preview.json()["rows"][0]["product"] == "001"  # IDs preserved
        aggregate = http.post(f"/api/v1/bi/tables/{table}/aggregate", json={
            "group_by": ["month"], "metric": {"column": "sales", "operation": "sum"}})
        assert aggregate.status_code == 200, aggregate.text
        assert sorted(r["sum_sales"] for r in aggregate.json()["rows"]) == [20, 30]
        cleaned = http.post(f"/api/v1/bi/tables/{table}/transform", json={"name": "非空销量", "steps": [
            {"type": "drop_nulls", "columns": ["comment"]},
            {"type": "rename", "column": "sales", "new_name": "actual_sales"},
            {"type": "filter", "column": "actual_sales", "operator": "gt", "value": "15"}
        ]})
        assert cleaned.status_code == 201, cleaned.text
        after_id = cleaned.json()["table_id"]
        assert cleaned.json()["row_count"] == 1
        assert cleaned.json()["lineage"]["source_table_id"] == table
        assert http.get(f"/api/v1/bi/tables/{table}/preview").json()["total"] == 3
        assert http.get(f"/api/v1/bi/tables/{after_id}/preview").json()["rows"][0]["actual_sales"] == "20"
        report = http.post("/api/v1/bi/reports", json={"name": "月度走势", "table_id": table,
            "chart_type": "bar", "aggregate": {"group_by": ["month"], "metric": {"column": "sales", "operation": "sum"}}})
        assert report.status_code == 201, report.text
        report_data = http.get("/api/v1/bi/reports/" + report.json()["report_id"])
        assert report_data.status_code == 200
        assert len(report_data.json()["result"]["rows"]) == 2
        # snapshot integrity
        snapshot = tmp_path / "timepilot" / "bi" / table / "data.csv"
        snapshot.write_bytes(snapshot.read_bytes() + b"tamper")
        changed = http.get(f"/api/v1/bi/tables/{table}/preview")
        assert changed.status_code == 422 and "校验失败" in changed.text


def test_bi_join_safeguards_and_exports(tmp_path, monkeypatch):
    with make_http(tmp_path, monkeypatch) as http:
        a = upload(http, "main", "id,value\n01,12\n02,13\n")
        b = upload(http, "mapping", "id,desc\n01,one\n02,two\n")
        joined = http.post("/api/v1/bi/tables/join", json={"name": "joined", "left_table_id": a,
             "right_table_id": b, "keys": ["id"], "how": "left"})
        assert joined.status_code == 201, joined.text
        assert joined.json()["row_count"] == 2
        assert http.get(f"/api/v1/bi/tables/{joined.json()['table_id']}/export.csv").status_code == 200
        duplicates = upload(http, "map_duplicate", "id,desc\n01,a\n01,b\n")
        bad = http.post("/api/v1/bi/tables/join", json={"name": "bad", "left_table_id": a,
                       "right_table_id": duplicates, "keys": ["id"], "how": "left"})
        assert bad.status_code == 422 and "重复" in bad.text
        assert http.post(f"/api/v1/bi/tables/{a}/aggregate", json={"group_by": ["unknown"],
            "metric": {"operation": "count"}}).status_code == 422


def test_bi_convert_into_forecast_dataset(tmp_path, monkeypatch):
    with make_http(tmp_path, monkeypatch) as http:
        csv = "factory,period,sales,available\n"
        for month in range(1, 13):
            csv += f"line-A,2025-{month:02d}-01,{100+month},2025-{month:02d}-10\n"
        table = upload(http, "raw", csv)
        result = http.post(f"/api/v1/bi/tables/{table}/to-timeseries", json={
            "name": "factory_forecast", "frequency": "MS", "timestamp_col": "period",
            "target_col": "sales", "entity_col": "factory", "available_at_col": "available",
            "source_timezone": "Asia/Shanghai", "unit": "pieces"})
        assert result.status_code == 201, result.text
        assert result.json()["record_count"] == 12
        assert http.get("/api/v1/datasets").json()[0]["name"] == "factory_forecast"


def test_model_credentials_encrypted_and_not_exposed(tmp_path, monkeypatch):
    with make_http(tmp_path, monkeypatch) as http:
        update = http.put("/api/v1/settings/llm", json={"model": "demo-model", "enabled": True,
            "base_url": "http://127.0.0.1:11434/v1", "api_key": "secret-123"})
        assert update.status_code == 200, update.text
        assert update.json()["has_api_key"] is True
        assert "secret-123" not in str(http.get("/api/v1/settings/llm").json())
        files = list((tmp_path / "timepilot").rglob("*"))
        assert not any("secret-123" in p.read_text(errors="ignore") for p in files if p.is_file())
        assert http.put("/api/v1/settings/llm", json={"base_url": "http://remote-host/v1"}).status_code == 422
        assert http.post("/api/v1/settings/llm/test").status_code == 422  # no local mock server
        proposal = http.post("/api/v1/bi/tools/execute", json={"tool": "bi.transform",
            "arguments": {"table_id": "123", "name": "bad", "steps": []}, "confirmed": False})
        assert proposal.status_code == 409
        assert http.post("/api/v1/bi/tools/execute", json={"tool": "run_python", "confirmed": True}).status_code == 422
        tools = http.get("/api/v1/bi/tools").json()
        assert any(t["name"] == "bi.aggregate" for t in tools)
        assert all(t['input_schema']['type']=='object' for t in tools)
        assert http.get("/api/v1/settings/runtime").json()["python_target"] == "3.12.12"


def test_full_routes_and_assets(tmp_path, monkeypatch):
    with make_http(tmp_path, monkeypatch) as http:
        for route in ["/", "/overview", "/new", "/datasets", "/bi", "/bi/reports",
                      "/bi/table/uuid", "/bi/table/uuid/grid", "/bi/table/uuid/fields", "/bi/table/uuid/prepare", "/bi/table/uuid/reports", "/bi/table/uuid/ai", "/bi/table/uuid/forecast", "/bi/report/uuid", "/runs", "/runs/uuid",
                      "/plugins", "/settings", "/settings/models", "/settings/general",
                      "/settings/runtime", "/help"]:
            assert http.get(route).status_code == 200, route
        for route in ["/not-a-route", "/api/v1/typo", "/settings/unknown"]:
            assert http.get(route).status_code == 404, route
        html = http.get("/bi").text
        for asset in ["bi-ui.js", "settings-ui.js", "bi-settings.css", "app.js"]:
            assert http.get("/assets/"+asset).status_code == 200
        assert 'href="/projects"' in html and 'href="/settings"' in html
        # BI remains accessible via project assets, not a duplicated global navigation link.
        assert 'requirements.txt' in (Path(__file__).parents[1]/"README.md").read_text(encoding="utf-8")


def test_real_model_gateway_plan_with_mock_http_and_schema_only(tmp_path, monkeypatch):
    """Exercise HTTP Chat Completions integration without making an external request."""
    import httpx
    import json
    seen=[]

    def handler(req: httpx.Request) -> httpx.Response:
        body=json.loads(req.content)
        seen.append({"url": str(req.url), "messages": body["messages"],
                     "auth": req.headers.get("authorization")})
        if len(body["messages"]) == 1:
            return httpx.Response(200,json={"model":"mock-model","choices":[{"message":{"content":"TimePilot OK"}}],"usage":{"total_tokens":3}})
        if "任务" in body["messages"][0]["content"] and "task_family" in body["messages"][0]["content"]:
            output={"name":"下月销量", "task_family":"demand_forecast", "horizon":1}
        else:
            output={"tool":"bi.aggregate","arguments":{"table_id":seen[-1]["messages"][1]["content"].split('"table_id": "')[1].split('"')[0],
                    "group_by":["month"],"metric":{"column":"sales","operation":"sum"}},"reason":"先按月份汇总"}
        return httpx.Response(200,json={"model":"mock-model","choices":[{"message":{"content":json.dumps(output,ensure_ascii=False)}}],
                                         "usage":{"total_tokens":20}})

    with make_http(tmp_path,monkeypatch) as api:
        table=upload(api,"clean", "month,sales\n2026-01,120\n2026-02,130\n")
        models=api.app.state.models
        models._http_client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw)
        assert api.put('/api/v1/settings/llm',json={"model":"mock-model","enabled":True,
              "api_key":"example-credential","base_url":"https://llm.example/v1"}).status_code==200
        plan=api.post('/api/v1/assistant/bi/plan',json={"request":"按月份汇总销量"})
        assert plan.status_code==200,plan.text
        assert plan.json()['executed'] is False
        assert plan.json()['tool']=='bi.aggregate'
        assert plan.json()['arguments']['table_id']==table
        assert seen[0]['url']=='https://llm.example/v1/chat/completions'
        assert seen[0]['auth']=='Bearer example-credential'
        assert '"sample"' not in str(seen[0]['messages'])
        test=api.post('/api/v1/settings/llm/test')
        assert test.status_code==200
        assert test.json()['ok'] is True
        api.put('/api/v1/settings/general',json={"assistant_mode":"off"})
        assert api.post('/api/v1/assistant/bi/plan',json={"request":"分析"}).status_code==409


def test_readonly_database_source_and_whitelist(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    db_file = tmp_path / "business.db"
    eng = create_engine("sqlite:///" + str(db_file))
    with eng.begin() as db:
        db.execute(text("CREATE TABLE approved_sales (month TEXT, sales INTEGER)"))
        db.execute(text("CREATE TABLE private_records (secret TEXT)"))
        db.execute(text("INSERT INTO approved_sales VALUES ('2026-01', 120), ('2026-02', 130)"))
        db.execute(text("INSERT INTO private_records VALUES ('restricted')"))
    eng.dispose()
    monkeypatch.setenv("TIMEPILOT_BI_SOURCE_URL", "sqlite:///" + str(db_file))
    monkeypatch.setenv("TIMEPILOT_BI_ALLOWED_TABLES", "approved_sales")
    with make_http(tmp_path, monkeypatch) as api:
        source = api.get('/api/v1/bi/sources')
        assert source.status_code == 200, source.text
        assert source.json()['tables'] == ['approved_sales']
        blocked = api.post('/api/v1/bi/sources/snapshot', json={"table_name": "private_records", "name": "bad"})
        assert blocked.status_code == 422
        snapshot = api.post('/api/v1/bi/sources/snapshot', json={"table_name": "approved_sales", "name": "approved_snapshot"})
        assert snapshot.status_code == 201, snapshot.text
        assert snapshot.json()['row_count'] == 2
        assert 'restricted' not in snapshot.text
        assert snapshot.json()['lineage']['type'] == 'sql_snapshot'
