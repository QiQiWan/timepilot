---
name: autonomous-modeling
description: Run a budget-bounded and audited rolling-origin forecasting comparison on an immutable TimePilot DataAsset, using only permitted historical targets and real publication timestamps. Use when a user explicitly requests reviewed autonomous feature engineering, model comparison, and experiment selection.
---
# Audited autonomous modeling

1. Verify a specific DataAsset version, **one source**, **one statistical scope** and a bounded number of series.
2. Check that monthly history is continuous, unique by series/time, complete and finite, and every `available_at` has an explicit timezone. Reject assumed publication dates, unreviewed scope conflicts and duplicates.
3. Execute `automodel.plan` first. Check origin rules and the number of candidate fits. Run `automodel.evaluate` only after user approval of its exact parameters.
4. Compare `last_value`, `moving_average`, `seasonal_naive`, and causal `lag_ridge` on identical rolling time splits. Feature at t may use observations strictly before t; recursively predict the future.
5. Compute WAPE only with a nonzero denominator. Report MAE, RMSE, sMAPE, bias, failed candidates, improvement relative to last_value, and actual CPU wall time.
6. Save source SHA, parameter digest, leaderboard, per-fold predictions and model artifacts in a content-addressed experiment directory. Never overwrite the original data.
7. A leaderboard winner is only an experiment recommendation; never deploy or create an approved production run automatically. Engineering sign-off is still required.
