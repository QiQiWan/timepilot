"""Runtime configuration. Server access is local-only by default."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

TARGET_PYTHON = (3, 12, 12)


@dataclass(frozen=True)
class Settings:
    home: Path
    db_url: str
    local_mode: bool
    max_upload_bytes: int
    worker_poll_seconds: float
    lease_seconds: int
    default_budget_seconds: int
    min_free_gb: float

    @classmethod
    def from_env(cls, home: str | Path | None = None) -> Settings:
        p = Path(home or os.getenv("TIMEPILOT_HOME") or (Path.home() / ".timepilot")).expanduser().resolve()
        db = os.getenv("TIMEPILOT_DB_URL") or f"sqlite:///{(p / 'metadata.sqlite3').as_posix()}"
        return cls(
            home=p,
            db_url=db,
            local_mode=os.getenv("TIMEPILOT_LOCAL_MODE", "1") == "1",
            max_upload_bytes=int(os.getenv("TIMEPILOT_MAX_UPLOAD_MB", "25")) * 1024 * 1024,
            worker_poll_seconds=float(os.getenv("TIMEPILOT_WORKER_POLL_SECONDS", "2")),
            lease_seconds=int(os.getenv("TIMEPILOT_LEASE_SECONDS", "120")),
            default_budget_seconds=int(os.getenv("TIMEPILOT_DEFAULT_BUDGET_SECONDS", "180")),
            min_free_gb=float(os.getenv("TIMEPILOT_MIN_FREE_GB", "0.5")),
        )

    def ensure_dirs(self) -> None:
        for d in ["datasets", "runs", "temp", "reports", "bi", "secrets"]:
            (self.home / d).mkdir(parents=True, exist_ok=True)


def python_check(*, strict: bool = False) -> tuple[bool, str]:
    current = sys.version_info[:3]
    exact = current == TARGET_PYTHON
    target = ".".join(map(str, TARGET_PYTHON))
    seen = ".".join(map(str, current))
    if current[:2] == TARGET_PYTHON[:2]:
        return True, f"Python {seen}: supported Python 3.12 (validated reference: {target})."
    return False, f"Python {seen}: unsupported; use the existing Python 3.12 environment (reference: {target})."
