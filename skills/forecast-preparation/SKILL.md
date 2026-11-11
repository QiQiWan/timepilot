---
name: forecast-preparation
description: Recommend and prepare an explicitly reviewed forecast from an immutable project data asset, pinned BI selection and timestamp-safe source mapping.
---
# Forecast preparation

1. Inspect project data with `project.assets.list`, then use `modeling.profile.inspect` and `modeling.tasks.suggest` for a **published immutable generic data asset**. Return column names exactly as saved; distinguish a sequence counter from a calendar timestamp.
2. Use `modeling.draft.prepare` only after the user authorizes creating a draft, explicitly pins the version and row-level filter/view, and acknowledges full-selection semantics. An existing saved Perspective view is optional; its visualization-only sample/pivot/limit/offset are never training rows.
3. Use `modeling.draft.inspect` and `modeling.draft.preflight` to review per-entity time continuity, target values, source availability, selection cardinality, algorithm support and rolling validation. These tools are read-only.
4. A human must approve the model field mapping, origin timing and available-at policy; actual dataset materialization and training submission are separate project API actions and must never be triggered by the Agent from a suggestion alone.
5. Do not treat minute/second, integer step indexes, multivariate features, labels or mechanisms as natively executable by the current monthly/daily/hourly single-target forecast runner. Ask for an explicit supported adapter/plugin and retain traceability.
