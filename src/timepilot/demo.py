"""Deterministic, synthetic DEMO ONLY. Never mix with enterprise data."""
from __future__ import annotations

from datetime import timedelta
from io import StringIO
from uuid import uuid4

import numpy as np
import pandas as pd

from timepilot.contracts import EvaluationSpec, TaskSpec
from timepilot.data import create_dataset


def demo_csv(n_months: int = 48) -> bytes:
    rng = np.random.default_rng(20261109)
    months = pd.date_range("2022-01-01", periods=n_months, freq="MS", tz="UTC")
    rows = []
    for series_id, offset in [("example_A", 0), ("example_B", 170)]:
        for i, ts in enumerate(months):
            value = round(1100 + offset + 5 * i + 190 * np.sin(i * 2 * np.pi / 12) + rng.normal(0, 16))
            available = ts + pd.offsets.MonthEnd(1) + pd.Timedelta(days=7)
            rows.append({"series_id": series_id, "timestamp": ts.isoformat(), "target": int(value),
                         "available_at": available.isoformat()})
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8")


def bootstrap(client, *, run: bool = True):
    name = f"demo_sales_{uuid4().hex[:8]}"
    manifest = create_dataset(client.store, client.settings, demo_csv(), "demo_sales.csv", name=name,
                              frequency="MS", unit="vehicles")
    task = TaskSpec(task_id="TP-DEMO-001", name="Synthetic monthly demand forecast (DEMO)",
                    forecast_horizon=1, model_candidates=["seasonal_naive", "moving_average", "last_value"],
                    evaluation=EvaluationSpec(validation_windows=4, test_horizon=1, min_history=12))
    result = {"dataset": manifest.model_dump(mode="json"), "task": task.model_dump(mode="json")}
    if run:
        result["run_id"] = client.engine.queue(task, manifest.dataset_id)
    return result
