---
name: mechanism-learning
description: Evaluate a candidate first-order grey-box state-control mechanism from documented sensor trajectories, fixed sampling intervals, units, and maintenance event boundaries, with train/test separation and identifiability checks. Use for TimePilot physical mechanism evaluation without confusing synthetic evidence with real equipment verification.
---
# Physical mechanism validation

1. Require measured state AND control arrays, sample interval, physical units, provenance, and maintenance boundaries. Refuse to infer dynamics from a sales target alone.
2. Register immutable evidence using `MechanismStudy` (SHA256). Preserve a distinction between declared real equipment and independently verified performance.
3. Exclude transitions crossing known maintenance resets from one-step regression and holdout evaluation.
4. Fit a simple state-space hypothesis x[t+1] = a*x[t] + b*u[t] + c on past samples; test on chronological holdout. Report parameter identifiability and stability.
5. Compare against a persistence baseline and report RMSE, event coverage and known limitations. Reject singular excitation or too little independent data.
6. Leave `verified_on_real_equipment=false` until an independent engineering verification process exists. No automatic device deployment.
