# ADR 0030 — Recorded Future: read-only enrichment, internal indicators never sent out

Status: accepted

## Context
The owner asked for a Recorded Future integration through its API. Triage of a finding (vuln pack) needs
exploitation evidence beyond CVSS: is the CVE exploited in the wild, linked to ransomware, what lifecycle stage.
Recorded Future's site and API docs are not reachable from the build sandbox; its official SDK `psengine` (2.14.0,
published by Recorded Future on PyPI) is, and it was the reference: base URL `https://api.recordedfuture.com`, the
`X-RFToken` header, `GET /v2/{ip|domain|hash|url|vulnerability|malware}/{value}?fields=…` answering
`{"data": …}` (404 when nothing is known), `GET /v2/{type}/riskrules`. Searches (analyst notes, playbook alerts,
SOAR batch enrichment) are POSTs.

## Decision
1. A `recordedfuture` openapi connector in the `vuln` pack, read-only (one token, held by the gateway; no write
   credential, no write operation). The vuln analyst gets `gateway:recordedfuture.*`; triage-finding uses
   `vulnerability.get` as evidence and treats a 404 as "nothing known", never as low risk.
2. Lookups send the indicator to an external service, so the operation schemas refuse what must stay inside:
   private, loopback, link-local, CGNAT and ULA addresses, internal domain suffixes (`.local`, `.internal`, `.lan`,
   `.corp`, `.home`, `.intranet`) and URLs on bare IPs. An admin who needs stricter rules leaves `domain.get` and
   `url.get` unexposed.
3. POST searches are not included: Z classifies POST operations as writes (rules only raise a class), and a
   search must not go through human approval. They come back if a read-POST exception is ever designed.

## Consequences
- Triage reports can cite Recorded Future risk rules; the data stays in Z at C2 (licensed intelligence).
- Analyst notes, playbook alerts and risk lists are not available to agents yet.
