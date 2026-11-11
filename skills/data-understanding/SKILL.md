---
name: data-understanding
description: Inspect local uploaded workbooks, identify layouts and business field roles, preview safe cleaning and series rules, and create immutable versions only after business approval.
---
# Data understanding

1. Call `file.inspect`, then `layout.detect` and `semantics.map` to identify actual source structure.
2. Branch to `quality.evaluate` if field roles or layouts are ambiguous; use `series.preview` and `data.clean_preview` to show transformation implications.
3. Treat header aliases, sampled scopes, source URLs and opaque codes as evidence requiring confirmation; never infer a business primary key from a numeric code alone.
4. Call `series.build` only with an already valid plan and explicit user approval. Retain SHA-256 source evidence and never overwrite raw files.
5. On a failed write, stop for reconciliation. Do not rerun the write blindly.
