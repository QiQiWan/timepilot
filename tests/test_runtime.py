import json

import pandas as pd
import pytest

from timepilot.contracts import RunStatus, TaskSpec
from timepilot.demo import bootstrap
from timepilot.runtime import RuntimeEngine
from timepilot.storage import safe_uuid


def test_smoke_full_run(client):
    sample = bootstrap(client)
    handle = client.runs.get(sample["run_id"])
    handle.wait(timeout=30)
    report = handle.report()
    assert handle.status()["status"] == "succeeded"
    assert report["metrics"]["Count"] == 2.0
    assert report["validation_metrics"]["Count"] == 8.0
    assert report["status"] == "review_required"
    forecast = handle.forecast()
    assert len(forecast["test"]) == 2
    assert len(forecast["future"]) == 2
    assert {r["split"] for r in forecast["test"]} == {"test"}
    assert all(r["actual"] is None for r in forecast["future"])
    assert {r["unit"] for r in forecast["future"] + forecast["test"]} == {"vehicles"}
    assert len(forecast["models"]) == 2
    for row in forecast["future"]:
        saved = handle.predict(row["series_id"])
        assert saved[0]["mean"] == pytest.approx(row["mean"])
        assert saved[0]["unit"] == "vehicles"


def test_checkpoint_skipped_on_forced_reexecution(client):
    sample = bootstrap(client)
    handle = client.runs.get(sample["run_id"])
    handle.wait(timeout=30)
    client.store.update_run(handle.id, status="queued", progress=0, stage="queued", worker_id=None)
    client.engine.work_once(handle.id)
    assert handle.status()["status"] == "succeeded"
    skipped = [e for e in handle.events() if e["event"] == "stage_skipped"]
    assert {e["message"] for e in skipped} == {"Verified checkpoint: validate", "Verified checkpoint: backtest",
                                               "Verified checkpoint: forecast", "Verified checkpoint: quality"}


def test_cancel_queued(client):
    sample = bootstrap(client)
    assert client.engine.cancel(sample["run_id"]) == "cancel_requested"
    assert client.store.run(sample["run_id"]).status == "cancelled"
    assert client.engine.work_once(sample["run_id"]) is None


def test_retry_failed(client):
    sample = bootstrap(client)
    client.store.update_run(sample["run_id"], status="failed", error_message="simulated")
    assert client.engine.retry(sample["run_id"]) == sample["run_id"]
    client.engine.work_once(sample["run_id"])
    assert client.store.run(sample["run_id"]).status == "succeeded"


def test_invalid_plugin_before_queue(client):
    sample = bootstrap(client, run=False)
    task = TaskSpec(task_id="TP-ERR", name="broken plugin", model_candidates=["nonexistent"])
    with pytest.raises(KeyError):
        client.engine.queue(task, sample["dataset"]["dataset_id"])


def test_safe_path_id_rejects_traversal(client):
    with pytest.raises(ValueError):
        safe_uuid("../../etc/passwd")


def test_point_in_time_split_block(client):
    rows = pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=6, freq="MS", tz="UTC"),
                         "available_at": pd.date_range("2025-02-01", periods=6, freq="MS", tz="UTC")})
    # A target erroneously recorded as available in the previous training period.
    rows.loc[4, "available_at"] = rows.loc[3, "available_at"]
    with pytest.raises(ValueError, match="already known"):
        RuntimeEngine._window(rows, 4, 1, "MS")


def test_expired_lease_can_be_reclaimed(client):
    from datetime import timedelta
    from timepilot.db import utcnow
    sample = bootstrap(client)
    run_id = sample["run_id"]
    first_worker = RuntimeEngine(client.settings, client.store, client.plugins, worker_id="worker-one")
    second_worker = RuntimeEngine(client.settings, client.store, client.plugins, worker_id="worker-two")
    assert first_worker.claim(run_id) == run_id
    assert second_worker.claim(run_id) is None
    client.store.update_run(run_id, lease_until=utcnow() - timedelta(seconds=1))
    assert second_worker.claim(run_id) == run_id
    assert client.store.run(run_id).worker_id == "worker-two"
    assert second_worker.execute(run_id)


def test_tampered_stage_receipt_forces_recomputation(client):
    sample = bootstrap(client)
    run_id = sample["run_id"]
    client.runs.get(run_id).wait(timeout=30)
    receipt = client.engine.run_dir(run_id) / "stages" / "quality_result.json"
    receipt.write_text('{"broken":true}', encoding="utf-8")
    client.store.update_run(run_id, status="queued", progress=0, worker_id=None)
    client.engine.work_once(run_id)
    assert client.engine.report(run_id)["status"] == "review_required"
    assert any(e["event"] == "stage_started" and e["message"] == "quality"
               for e in client.engine.events(run_id))


def test_running_task_honors_cancel_at_boundary(client):
    sample = bootstrap(client)
    run_id = sample["run_id"]
    assert client.engine.claim(run_id) == run_id
    client.engine.cancel(run_id)
    assert not client.engine.execute(run_id)
    assert client.store.run(run_id).status == "cancelled"
