"""Evidence-grounded deterministic planning placeholder; external LLM is deliberately disabled.

Never auto-execute a plan without explicit budget/permission checks.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from timepilot.contracts import QualityManifest


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["review_data", "extend_test", "try_model", "review_bias", "stop"]
    reason: str
    evidence: list[str] = Field(default_factory=list)
    expected_compute_seconds: int = Field(default=0, ge=0)
    approved: bool = False


class AgentActionPlan(BaseModel):
    schema_version: str = "1.0.0"
    run_id: str
    actions: list[AgentAction]
    total_budget_seconds: int = Field(default=0, ge=0)
    requires_approval: bool = True
    source: Literal["deterministic_rules", "llm_proposal"] = "deterministic_rules"


def suggest_next_actions(report: QualityManifest) -> AgentActionPlan:
    """Produces reviewable proposals; no optimization job is submitted."""
    actions = []
    issue_ids = {issue.code for issue in report.issues}
    if "TQE-SMALL-TEST" in issue_ids:
        actions.append(AgentAction(action="extend_test", reason="Increase independent forecast test coverage before operational acceptance.",
                                   evidence=["TQE-SMALL-TEST"]))
    if "TQE-ZERO-DENOM" in issue_ids:
        actions.append(AgentAction(action="review_data", reason="All test targets are zero; choose suitable intermittent-demand metrics.",
                                   evidence=["TQE-ZERO-DENOM"]))
    bias = report.metrics.get("Bias")
    if bias is not None and abs(bias) > 0.1:
        actions.append(AgentAction(action="review_bias", reason="Systematic bias exceeds 10% of absolute test demand; review forecast slices.",
                                   evidence=["metrics.Bias"]))
    if not actions:
        actions.append(AgentAction(action="stop", reason="No further automated action recommended; request business approval."))
    return AgentActionPlan(run_id=report.run_id, actions=actions)
