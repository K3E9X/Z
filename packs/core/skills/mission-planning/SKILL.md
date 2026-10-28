---
name: mission-planning
description: Turn a request into a plan of steps with agents, fan-out and named writes.
version: 1.0.0
---

1. Restate the goal in one sentence.
2. Check the playbook list. If one matches, use it and say which.
3. Otherwise write 2–8 steps. Each step: title, agent, inputs, output, depends_on.
4. Use fan-out when the same work applies to many items (repos, findings, tickets). Set concurrency ≤ 8 unless told otherwise.
5. List every write the plan will propose (connector + operation). If there is none, say "no writes".
6. Estimate tokens roughly: small (<1M), medium (1–5M), large (>5M).

Output (JSON):
```json
{"goal": "...", "playbook": null, "steps": [{"id": "s1", "title": "...", "agent": "vuln-analyst", "fanout": null, "depends_on": []}], "writes": ["jira.issues.create"], "size": "medium"}
```
