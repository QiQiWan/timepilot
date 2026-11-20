"""Independent unit tests for fail-closed v0.36.0 release classification."""
from __future__ import annotations

from timepilot.featurelab360_gate import (
    REQUIRED_GATES, check_pytest_output, same_source_evidence, summarize_gates,
)

SHA = "a" * 64
ART = "b" * 64


def matching_evidence():
    backend = {
        "status": "passed_backend_same_source_browser_pending",
        "source": {"sha256": SHA},
        "artifact": {"source_sha256": SHA, "sha256": ART, "artifact_id": "artifact-123"},
        "evaluation": {"source_sha256": SHA, "evaluated_artifact_sha256": ART,
                       "evaluated_artifact_id": "artifact-123", "folds": [{"fold": 1}]},
    }
    browser = {"status": "passed", "source_sha256": SHA, "artifact_sha256": ART,
               "native_bundle_verified": True, "installed_chrome": True,
               "websocket_opened": True, "refresh_restored": True, "panel_count": 2}
    return backend, browser


def test_same_source_runtime_proof_matches():
    backend, browser = matching_evidence()
    assert same_source_evidence(backend, browser) == []


def test_browser_different_artifact_cannot_pass():
    backend, browser = matching_evidence()
    browser["artifact_sha256"] = "c" * 64
    assert "browser artifact mismatched" in same_source_evidence(backend, browser)


def test_missing_browser_runtime_evidence_is_rejected():
    backend, browser = matching_evidence()
    browser["websocket_opened"] = False
    browser["panel_count"] = 1
    assert len(same_source_evidence(backend, browser)) == 2


def test_missing_fold_evidence_cannot_prove_model():
    backend, browser = matching_evidence()
    backend["evaluation"]["folds"] = []
    assert "model has no evaluated fold evidence" in same_source_evidence(backend, browser)


def test_skipped_physical_suite_is_blocked_even_with_success_exit():
    assert check_pytest_output("2 passed, 3 skipped", 0) == ("blocked", "3 physical tests were skipped")
    assert check_pytest_output("3 skipped in 0.01s", 0)[0] == "blocked"
    assert check_pytest_output("1 passed in 0.03s", 0)[0] == "passed"


def test_failure_exit_and_no_tests_are_not_passed():
    assert check_pytest_output("1 failed, 1 passed", 1)[0] == "failed"
    assert check_pytest_output("no tests ran", 0)[0] == "blocked"


def test_gates_require_all_physical_and_browser_proofs():
    gates = [{"name": n, "status": "passed"} for n in REQUIRED_GATES]
    backend, browser = matching_evidence()
    assert summarize_gates(gates, backend_evidence=backend, browser_evidence=browser)["fully_accepted"]
    gates[3]["status"] = "blocked"
    assert not summarize_gates(gates, backend_evidence=backend, browser_evidence=browser)["fully_accepted"]
    assert not summarize_gates(gates[:-1])["fully_accepted"]


def test_duplicate_gate_names_are_not_accepted():
    gates = [{"name": n, "status": "passed"} for n in REQUIRED_GATES]
    assert not summarize_gates(gates + [gates[0]])["fully_accepted"]
