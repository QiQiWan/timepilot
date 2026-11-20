"""TimePilot v0.36.0: fail-closed evidence evaluation for a physical release.

A test command returning exit code zero is insufficient: pytest may have skipped
all physical tests, and a browser receipt may refer to a different data asset.
This module never manufactures an external-data, native-browser or production
claim; it only validates evidence collected by the dedicated acceptance runner.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"^[0-9a-f]{64}$")
VALID = {"passed", "blocked", "failed"}
REQUIRED_GATES = (
    "strict_source", "python_regression", "frontend_node",
    "physical_duckdb", "worker_crash_recovery", "migration_compatibility",
    "performance", "real_source_pipeline", "native_browser",
)


def digest_file(path: Path) -> str:
    """Read through a streaming hash; do not load large Parquet into memory."""
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pytest_output(output: str, returncode: int, *, no_skip: bool = True) -> tuple[str, str]:
    """Require genuine executed tests: 0 collected or skipped is not passing."""
    if returncode != 0:
        return "failed", f"pytest exited {returncode}"
    passed = re.search(r"(?<!\d)(\d+) passed\b", output)
    if not passed or int(passed.group(1)) < 1:
        return "blocked", "no executed passing tests in pytest summary"
    skipped = re.search(r"(?<!\d)(\d+) skipped\b", output)
    if no_skip and skipped and int(skipped.group(1)):
        return "blocked", f"{skipped.group(1)} physical tests were skipped"
    return "passed", f"{passed.group(1)} executed tests passed"


def same_source_evidence(backend: dict[str, Any], browser: dict[str, Any]) -> list[str]:
    """Match the actual artifact by bytes, provenance and BI runtime evidence."""
    problems = []
    if backend.get("status") != "passed_backend_same_source_browser_pending":
        problems.append("real backend pipeline incomplete")
    if browser.get("status") != "passed":
        problems.append("native browser integration incomplete")
    source = backend.get("source") or {}
    artifact = backend.get("artifact") or {}
    evaluated = backend.get("evaluation") or {}
    origin_sha = source.get("sha256")
    artifact_sha = artifact.get("sha256")
    if not isinstance(origin_sha, str) or not SHA256.fullmatch(origin_sha):
        problems.append("missing real source SHA256")
    if not isinstance(artifact_sha, str) or not SHA256.fullmatch(artifact_sha):
        problems.append("missing materialized Parquet SHA256")
    for label, actual, expected in (
        ("backend artifact source", artifact.get("source_sha256"), origin_sha),
        ("model source", evaluated.get("source_sha256"), origin_sha),
        ("model artifact", evaluated.get("evaluated_artifact_sha256"), artifact_sha),
        ("browser source", browser.get("source_sha256"), origin_sha),
        ("browser artifact", browser.get("artifact_sha256"), artifact_sha),
    ):
        if not expected or actual != expected:
            problems.append(label + " mismatched")
    if not evaluated.get("evaluated_artifact_id") or not artifact.get("artifact_id"):
        problems.append("artifact identity missing")
    elif evaluated["evaluated_artifact_id"] != artifact["artifact_id"]:
        problems.append("model evaluated different artifact")
    folds = evaluated.get("folds")
    if not isinstance(folds, list) or not folds:
        problems.append("model has no evaluated fold evidence")
    for flag in ("native_bundle_verified", "installed_chrome", "websocket_opened", "refresh_restored"):
        if browser.get(flag) is not True:
            problems.append(f"browser {flag} not proven")
    if not isinstance(browser.get("panel_count"), int) or browser["panel_count"] < 2:
        problems.append("native Perspective requires at least two verified panels")
    return problems


def summarize_gates(gates: list[dict[str, Any]], *, browser_evidence: dict | None = None,
                    backend_evidence: dict | None = None) -> dict[str, Any]:
    """Each gate must be present once and passed; missing reports are BLOCKED."""
    by_name = {g.get("name"): g for g in gates if g.get("name")}
    findings = []
    if len(gates) != len(by_name):
        findings.append("duplicate or unnamed release gates")
    for name in REQUIRED_GATES:
        if name not in by_name:
            findings.append(f"missing gate: {name}")
        elif by_name[name].get("status") not in VALID:
            findings.append(f"invalid status for {name}")
        elif by_name[name]["status"] != "passed":
            findings.append(f"{name}: {by_name[name]['status']}")
    if backend_evidence is not None or browser_evidence is not None:
        findings.extend(same_source_evidence(backend_evidence or {}, browser_evidence or {}))
    status = "passed" if not findings else (
        "failed" if any(g.get("status") == "failed" for g in gates) else "blocked"
    )
    return {"status": status, "fully_accepted": status == "passed",
            "passed_gates": [g["name"] for g in gates if g.get("status") == "passed"],
            "blocking_findings": findings}
