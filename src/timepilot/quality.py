"""Independent quality evidence; test data are never used for model selection."""
from __future__ import annotations

from timepilot.contracts import QualityManifest, ValidationIssue
from timepilot.metrics import calculate


def make_quality(*, run_id: str, dataset_id: str, protocol_id: str, model_id: str,
                 validation: list[dict], test: list[dict], evidence_files: list[str]) -> QualityManifest:
    test_metrics = calculate(test)
    val_metrics = calculate(validation)
    grouped: dict[str, list[dict]] = {}
    for row in test:
        grouped.setdefault(row["series_id"], []).append(row)
    slices = {key: calculate(rows) for key, rows in grouped.items()}
    issues: list[ValidationIssue] = []
    if test_metrics["Count"] < 1:
        issues.append(ValidationIssue(code="TQE-EMPTY-TEST", severity="blocker", message="No independent test predictions."))
    if test_metrics["WAPE"] is None:
        issues.append(ValidationIssue(code="TQE-ZERO-DENOM", severity="warning",
                                      message="WAPE is undefined because the test target has zero absolute sum."))
    if test_metrics["Count"] < 12:
        issues.append(ValidationIssue(code="TQE-SMALL-TEST", severity="warning",
                                      message="Independent test has fewer than 12 forecast points; review statistical uncertainty."))
    status = "failed" if any(x.severity == "blocker" for x in issues) else "review_required"
    # 'passed' needs business thresholds and approval, not an arbitrary score cutoff.
    return QualityManifest(run_id=run_id, dataset_id=dataset_id, protocol_id=protocol_id,
                           model_id=model_id, metrics=test_metrics, validation_metrics=val_metrics,
                           slice_metrics=slices, issues=issues, status=status, evidence_files=evidence_files)
