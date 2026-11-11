"""Independent, deterministic metric calculations (TQE foundation)."""
from __future__ import annotations

import math

import numpy as np


def calculate(rows: list[dict]) -> dict[str, float | None]:
    valid = [(float(x["actual"]), float(x["mean"])) for x in rows if x.get("actual") is not None]
    if not valid:
        return {"MAE": None, "RMSE": None, "WAPE": None, "MASE": None, "Bias": None, "ME": None, "Count": 0.0}
    y = np.array([x[0] for x in valid], dtype=float)
    p = np.array([x[1] for x in valid], dtype=float)
    err = p - y
    scaled = [abs(float(x["mean"]) - float(x["actual"])) / float(x["mase_scale"])
              for x in rows if x.get("actual") is not None and x.get("mase_scale") is not None
              and float(x["mase_scale"]) > 0]
    denom = float(np.abs(y).sum())
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err**2))),
        "WAPE": float(np.abs(err).sum() / denom) if denom else None,
        "MASE": float(np.mean(scaled)) if scaled else None,
        "Bias": float(err.sum() / denom) if denom else None,
        "ME": float(np.mean(err)),  # physical-unit signed error; Bias remains a relative ratio
        "Count": float(len(valid)),
    }


def ranking_score(metrics: dict, primary: str) -> float:
    score = metrics.get(primary)
    if score is None or not math.isfinite(score):
        score = metrics.get("MAE")
    return float(score) if score is not None else float("inf")
