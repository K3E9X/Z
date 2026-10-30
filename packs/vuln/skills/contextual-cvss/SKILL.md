---
name: contextual-cvss
description: Map scanner severity and context to a priority and a deadline.
version: 1.0.0
evals: evals/contextual-cvss.jsonl
---

| Context | Priority | Deadline |
|---|---|---|
| Internet-facing AND (public exploit OR KEV) | critical | 3 days |
| Internet-facing OR (internal AND public exploit) | high | 7 days |
| Internal, no known exploit | medium | 30 days |
| Isolated or not reachable | low | 90 days |
Apply the remediation policy of the organisation if memory has a different table; memory wins.
