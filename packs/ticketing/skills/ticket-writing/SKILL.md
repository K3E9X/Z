---
name: ticket-writing
description: Write a ticket a team can act on without asking questions.
version: 1.0.0
evals: evals/ticket-writing.jsonl
---

Ticket body, in this order:
1. Summary of the problem in one sentence.
2. Affected assets, with the owning team from the CMDB.
3. Fix deadline from the remediation policy (critical: 3 days, high: 7, medium: 30, low: 90, unless memory says otherwise).
4. How to fix, in numbered steps.
5. Evidence and references.
If no owner is found, say so; never guess one.
Title format: `[Priority] <problem> on <asset or group>`.
