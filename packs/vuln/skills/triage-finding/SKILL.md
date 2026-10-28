---
name: triage-finding
description: Qualify one finding in context and output a structured triage result.
version: 1.0.0
---

Input: one finding id.

1. Get the finding with defectdojo.findings.get (title, CVE, component, version, asset, scanner severity).
2. Get the asset with cmdb.assets.get: environment, exposure (internet, internal, isolated), criticality, owner team.
3. Decide exploitability in this context:
   - Is the vulnerable code path reachable? Is the service exposed? Is there a public exploit or KEV entry?
4. Apply `contextual-cvss` to get the contextual priority.
5. Write `result.json` matching `schemas/triage-result.json`, and a 5-line `report.md`.

Example `result.json`:
```json
{"finding_id": "18422", "title": "log4j-core 2.14 RCE", "cve": "CVE-2021-44228",
 "asset": "bo-pay-03", "component": "log4j-core 2.14.1", "priority": "critical", "exploitable_in_context": true,
 "owner_team": "Payments Platform", "fix": "Upgrade log4j-core to 2.24.3",
 "deadline_days": 3, "evidence": ["Internet-facing", "Public exploit", "CVSS 10.0"]}
```
Rules: never invent an owner (use null); never lower a priority without stating the compensating control.
