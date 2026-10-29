---
name: environment-context
description: Get the context of an asset that changes a finding's risk.
version: 1.0.0
evals: evals/environment-context.jsonl
---

1. Query cmdb.assets.get (or cmdb.assets.search by IP) with the asset name or IP.
2. Record: environment (prod/preprod/dev), exposure, criticality (1–4), owner team, data class.
3. If the CMDB has no record, say "unknown asset" and flag it in evidence.
