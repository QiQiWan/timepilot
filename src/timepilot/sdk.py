"""User-facing SDK: client.tasks.from_file(), client.runs.create().wait().report()."""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from timepilot.config import Settings
from timepilot.contracts import RunStatus, TaskSpec
from timepilot.data import create_dataset
from timepilot.db import Store
from timepilot.plugins import PluginRegistry
from timepilot.runtime import RuntimeEngine


class TaskManager:
    @staticmethod
    def from_file(path: str | Path) -> TaskSpec:
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return TaskSpec.model_validate(yaml.safe_load(content))
        if path.suffix.lower() == ".json":
            return TaskSpec.model_validate_json(content)
        raise ValueError("Task configuration must be YAML or JSON")


class RunHandle:
    def __init__(self, client, run_id: str):
        self.client = client
        self.id = run_id

    def status(self) -> dict:
        row = self.client.store.run(self.id)
        if not row:
            raise KeyError(self.id)
        return {"run_id": row.id, "status": row.status, "stage": row.stage, "progress": row.progress,
                "attempts": row.attempts, "error_code": row.error_code, "error_message": row.error_message}

    def wait(self, *, timeout: float | None = 300.0, poll: float = 0.5, execute_if_queued: bool = True):
        end = time.monotonic() + timeout if timeout is not None else None
        while True:
            record = self.status()
            if record["status"] == RunStatus.SUCCEEDED:
                return self
            if record["status"] in {RunStatus.FAILED, RunStatus.CANCELLED}:
                raise RuntimeError(f"Run {self.id}: {record['status']}: {record['error_message']}")
            if execute_if_queued and record["status"] == RunStatus.QUEUED:
                self.client.engine.work_once(self.id)
            if end is not None and time.monotonic() >= end:
                raise TimeoutError(f"Waiting for run {self.id} timed out; task remains persisted.")
            time.sleep(poll)

    def report(self) -> dict:
        return self.client.engine.report(self.id)

    def forecast(self) -> dict:
        return self.client.engine.forecast(self.id)

    def predict(self, series_id: str, *, horizon: int | None = None) -> list[dict]:
        return self.client.engine.predict_saved(self.id, series_id, horizon=horizon)

    def events(self) -> list[dict]:
        return self.client.engine.events(self.id)


class RunManager:
    def __init__(self, client):
        self.client = client

    def create(self, *, task: TaskSpec, dataset: str) -> RunHandle:
        run_id = self.client.engine.queue(task, dataset)
        return RunHandle(self.client, run_id)

    def get(self, run_id: str) -> RunHandle:
        if not self.client.store.run(run_id):
            raise KeyError(run_id)
        return RunHandle(self.client, run_id)


class DatasetManager:
    def __init__(self, client):
        self.client = client

    def from_file(self, path: str | Path, *, name: str | None = None, **options):
        p = Path(path)
        return create_dataset(self.client.store, self.client.settings, p.read_bytes(), p.name,
                              name=name or p.stem, **options)

    def list(self):
        return [json.loads(row.manifest_json) for row in self.client.store.list_datasets()]


class TimePilot:
    def __init__(self, *, home: str | Path | None = None):
        self.settings = Settings.from_env(home)
        self.settings.ensure_dirs()
        self.store = Store(self.settings.db_url)
        self.plugins = PluginRegistry()
        self.engine = RuntimeEngine(self.settings, self.store, self.plugins)
        self.tasks = TaskManager()
        self.runs = RunManager(self)
        self.datasets = DatasetManager(self)
