"""Mechanism cards and a deterministic linear state simulator for research fixtures.

A scientific research scaffold; no real device dynamics are inferred here.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class MechanismCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str
    version: str = "1.0.0"
    family: Literal["first_order", "state_space", "sparse_dynamics", "hybrid_dynamics"]
    state_variables: list[str]
    control_variables: list[str] = Field(default_factory=list)
    parameters: dict[str, float] = Field(default_factory=dict)
    reference: str
    applicability: str
    known_limitations: list[str] = Field(default_factory=list)
    verified_on_real_equipment: bool = False


def first_order_step(state: float, control: float, *, decay: float, gain: float, bias: float = 0.0) -> float:
    """x[t+1] = decay*x[t] + gain*u[t] + bias. Used for simulations only."""
    if not (0 <= decay <= 1):
        raise ValueError("For this demo, decay must be between 0 and 1")
    return float(decay * state + gain * control + bias)


def simulated_maintenance_series(*, length=200, seed=42, maintenance_step=100, faulty_model=False):
    """Synthetic, reproducible baseline with one maintenance state reset and regime shift."""
    if length < 12 or not 3 < maintenance_step < length - 2:
        raise ValueError("invalid length or maintenance step")
    rng = np.random.default_rng(seed)
    u = rng.normal(0.5, 0.2, size=length)
    x = np.zeros(length, dtype=float)
    true_decay = np.where(np.arange(length) < maintenance_step, 0.88, 0.72)
    for t in range(1, length):
        if t == maintenance_step:
            x[t-1] = 0.2 * x[t-1]  # known reset at a maintenance event
        x[t] = first_order_step(x[t-1], u[t-1], decay=float(true_decay[t]), gain=0.8)
        x[t] += rng.normal(0, 0.02)
    proposed_decay = 0.25 if faulty_model else 0.88
    card = MechanismCard(card_id="synthetic_linear_state_v1", family="first_order",
                         state_variables=["state"], control_variables=["control"],
                         parameters={"decay": proposed_decay, "gain": 0.8},
                         reference="TimePilot synthetic fixture; not enterprise data",
                         applicability="Single state with control and event observations",
                         known_limitations=["Synthetic system only", "No physical unit validation"])
    return {"control": u, "observed_state": x, "maintenance_step": maintenance_step,
            "true_decay": true_decay, "hypothesis": card}
