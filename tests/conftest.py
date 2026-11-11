from __future__ import annotations

import pytest

from timepilot.sdk import TimePilot


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TIMEPILOT_DB_URL", raising=False)
    return TimePilot(home=tmp_path / "workspace")
