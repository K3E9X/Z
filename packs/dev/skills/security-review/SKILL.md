---
name: security-review
description: Review a diff for security issues first, then correctness.
version: 1.0.0
evals: evals/security-review.jsonl
---

Check, in order: injection (SQL, command, template), authn/authz changes, secrets in code, unsafe deserialization, SSRF, path traversal, crypto misuse, dependency changes, error handling that leaks data.
Output: `blockers[]` (file, line, risk, fix), `remarks[]`, `verdict` (approve | changes_requested).
