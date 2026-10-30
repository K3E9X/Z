---
name: flow-matrix-check
description: Check a flow matrix against segmentation rules.
version: 1.0.0
evals: evals/flow-matrix-check.jsonl
---

1. Load the matrix (CSV/XLSX): source, destination, port, protocol, justification.
2. Flag: any-any, flows crossing zones without justification, clear-text admin protocols, internet to internal flows, missing owner.
3. Output a table of findings with the row, the rule broken and a proposed fix.
