from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.demo import demo_csv


def test_local_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    app = create_app(home=tmp_path / "api", embedded_worker=False)
    with TestClient(app) as http:
        assert http.get("/api/v1/health").json()["status"] == "ok"
        assert http.get("/").status_code == 200
        assert "workbench" in http.get("/assets/app.js").text
        payload = http.post("/api/v1/demo/bootstrap")
        assert payload.status_code == 201, payload.text
        run_id = payload.json()["run_id"]
        assert http.get("/api/v1/runs").json()[0]["run_id"] == run_id
        app.state.client.engine.work_once(run_id)
        report = http.get(f"/api/v1/runs/{run_id}/quality")
        assert report.status_code == 200, report.text
        assert report.json()["metrics"]["Count"] == 2
        dl = http.get(f"/api/v1/runs/{run_id}/forecasts.csv")
        assert dl.status_code == 200 and "series_id" in dl.text


def test_upload_errors_are_readable(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    with TestClient(create_app(home=tmp_path / "api", embedded_worker=False)) as http:
        bad = http.post("/api/v1/datasets/upload", files={"file": ("x.csv", b"timestamp,target\n2026-01-01,1", "text/csv")},
                        data={"name": "bad"})
        assert bad.status_code == 422
        assert "series_id" in str(bad.json())
        not_found = http.get("/api/v1/runs/00000000-0000-0000-0000-000000000000")
        assert not_found.status_code == 404


def test_task_is_validated_before_queue(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    with TestClient(create_app(home=tmp_path / "api", embedded_worker=False)) as http:
        demo = http.post("/api/v1/demo/bootstrap").json()
        task = demo["task"]
        task["frequency"] = "D"
        result = http.post("/api/v1/tasks/validate", json={"task": task, "dataset": demo["dataset"]["dataset_id"]})
        assert result.status_code == 422
