"""Pangoo integration contract. No endpoint, credential or data path is invented."""
from __future__ import annotations

from pathlib import Path

from timepilot.extensions import PluginDescriptor


class PangooNotConfigured(RuntimeError):
    pass


class PangooAdapter:
    def describe(self):
        return PluginDescriptor(plugin_id="enterprise.pangoo", plugin_version="0.1.0",
                                description="Pangoo data and compute adapter (requires enterprise interface specification)",
                                supported_task_families=["demand_forecast", "equipment_state"],
                                needs_network=True, side_effects=["enterprise_data_read", "artifact_publish"])

    def list_authorized_datasets(self, principal: str):
        raise PangooNotConfigured("Pangoo credentials, authorization and dataset API have not been supplied")

    def get_snapshot(self, dataset_ref: str, *, principal: str) -> Path:
        raise PangooNotConfigured("Pangoo dataset snapshot API and access policy are not configured")

    def submit_artifact(self, local_path: Path, *, approval_token: str):
        raise PangooNotConfigured("Pangoo model publication API and approval policy are not configured")
