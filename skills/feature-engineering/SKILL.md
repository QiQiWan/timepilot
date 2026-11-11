---
name: feature-engineering
description: Preview leakage-aware lag features on immutable, singly sourced monthly time series and export an auditable, hashed derived feature file only with explicit approval.
---
# Feature engineering

1. Check `quality.asset` first, using the same selection as feature construction.
2. Run `features.preview` to inspect lag completeness and suppression based on historical publication timestamps.
3. Require one source and one confirmed statistical scope; never mix entities or impute missing data implicitly.
4. Run `features.export` only after explicit approval. The result is an exploratory derived artifact, not an automatically approved training dataset.
5. On interrupted writes, reconcile the content-hashed artifact and its metadata before retrying.
