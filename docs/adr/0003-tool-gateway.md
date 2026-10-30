# ADR 0003 — A tool gateway owns credentials and policy

Status: accepted

## Context
Agents read untrusted content and can be manipulated. They must not hold credentials or reach tools directly.

## Decision
Each job gets one MCP endpoint served by `z-server gateway`. It lists only allowed operations, injects credentials, rate-limits, redacts, scans results with the judge and audits. Writes are only possible through the executor after approval.

## Consequences
Strong containment and one audit point. The gateway must be fast and highly available; it scales as its own deployment.
