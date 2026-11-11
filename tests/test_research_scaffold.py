import pytest

from timepilot.agents.planner import suggest_next_actions
from timepilot.contracts import QualityManifest, ValidationIssue
from timepilot.enterprise.pangoo import PangooAdapter, PangooNotConfigured
from timepilot.mechanisms.catalog import first_order_step, simulated_maintenance_series


def test_synthetic_mechanism_is_reproducible():
    a = simulated_maintenance_series(seed=2)
    b = simulated_maintenance_series(seed=2)
    assert a["observed_state"].tolist() == b["observed_state"].tolist()
    assert not a["hypothesis"].verified_on_real_equipment


def test_wrong_mechanism_injection():
    a = simulated_maintenance_series(faulty_model=False)
    b = simulated_maintenance_series(faulty_model=True)
    assert a["hypothesis"].parameters["decay"] != b["hypothesis"].parameters["decay"]
    with pytest.raises(ValueError):
        first_order_step(1.0, 0.2, decay=2.0, gain=1.0)


def test_agent_proposal_requires_approval():
    report = QualityManifest(run_id="test", dataset_id="dataset", protocol_id="p", model_id="naive",
                             status="review_required", metrics={"Bias": -0.12}, validation_metrics={},
                             issues=[ValidationIssue(code="TQE-SMALL-TEST", severity="warning", message="small")])
    plan = suggest_next_actions(report)
    assert plan.requires_approval
    assert all(not act.approved for act in plan.actions)
    assert len(plan.actions) == 2


def test_enterprise_adapter_does_not_fabricate_access():
    adapter = PangooAdapter()
    with pytest.raises(PangooNotConfigured):
        adapter.list_authorized_datasets("someone")
