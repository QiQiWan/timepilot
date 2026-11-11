import pytest

from timepilot.metrics import calculate


def test_metrics_independent_formula():
    out = calculate([{"actual": 100, "mean": 90}, {"actual": 200, "mean": 190}])
    assert out["MAE"] == 10
    assert out["WAPE"] == pytest.approx(20 / 300)
    assert out["Bias"] == pytest.approx(-20 / 300)
    assert out["ME"] == pytest.approx(-10.0)


def test_zero_actual_wape_not_defined():
    out = calculate([{"actual": 0, "mean": 5}])
    assert out["WAPE"] is None
    assert out["MAE"] == 5
