import pytest
from pydantic import ValidationError

from timepilot.contracts import EvaluationSpec, TaskSpec
from timepilot.plugins import LastValue, PluginRegistry


def test_task_serialization_round_trip():
    task = TaskSpec(task_id="TP-001", name="monthly demand", forecast_horizon=2,
                    evaluation=EvaluationSpec(test_horizon=2))
    assert TaskSpec.model_validate_json(task.model_dump_json()) == task


def test_horizon_mismatch_rejected():
    with pytest.raises(ValidationError):
        TaskSpec(task_id="TP-001", name="monthly demand", forecast_horizon=2)


def test_duplicate_candidates_rejected():
    with pytest.raises(ValidationError):
        TaskSpec(task_id="TP-001", name="monthly demand", model_candidates=["last_value", "last_value"])


def test_unknown_plugin_rejected():
    with pytest.raises(KeyError):
        PluginRegistry().get("not_registered")


def test_duplicate_plugin_rejected():
    p = PluginRegistry()
    with pytest.raises(ValueError):
        p.register(LastValue())


def test_auto_gluon_optional_catalog():
    ids = [x["plugin_id"] for x in PluginRegistry().catalog()]
    assert "autogluon_timeseries" in ids
    assert "seasonal_naive" in ids


def test_reject_incompatible_contract():
    with pytest.raises(ValidationError):
        TaskSpec(schema_version="2.0.0", task_id="TP-XXX", name="new incompatible contract")
