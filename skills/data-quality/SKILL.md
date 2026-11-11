---
name: data-quality
description: Evaluate data completeness, availability timing, source consistency and time-series quality for uploaded candidates or an immutable DataAsset version.
---
# Data quality

1. Use `quality.evaluate` for import candidate previews; its sample statistics are not full-dataset guarantees.
2. Use `quality.asset` for a stored immutable version and selected business filters.
3. Keep record coverage separate from coverage of valid target values.
4. Treat mixed scopes, duplicate identities, missing availability timestamps and source conflicts as review findings; do not automatically remove observations.
