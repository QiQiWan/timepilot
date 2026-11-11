---
name: semantic-evaluation
description: Build a human-reviewed ground-truth set from actual imported workbook schemas, compare deterministic mappings with opt-in real model responses, and audit field corrections, latency, tokens, and API cost without fabricated labels. Use for TimePilot schema recognition quality audits.
---
# Human-grounded semantic evaluation

1. Upload the real business file through `/imports` and call `semantic.seed` with its import ID.
2. Inspect **each original worksheet**. Keep the suggested mapping as a suggestion until a human reviewer confirms roles and granularity. Record reviewer and source SHA256.
3. Do not pass ground-truth labels, raw records, original filenames, or provenance URLs to external models. Only send explicitly authorized header/schema metadata.
4. Evaluate rules against confirmed cases first. If the model is enabled and the user opts in, compare its actual JSON output against **the same** gold cases.
5. Measure exact case success, role accuracy, field-level corrections needed, provider latency, valid-response rate, real tokens, and price calculated from user-supplied USD/million token rates.
6. Mark unlabeled data **pending** and unavailable token/cost data **unknown**. Estimated corrections are edit-distance to gold, not observed human operation time.
7. Never claim a mock response is a real model experiment. Do not automatically promote agent suggestions into gold labels.
