---
name: browser-flows
description: Run recorded browser flows safely.
version: 1.0.0
evals: evals/browser-flows.jsonl
---

1. Run only flows listed in your tools. Pass parameters exactly.
2. After each flow step, compare the page with the expected assertion. If it differs, stop and call ask_human with a screenshot reference.
3. Never enter or reveal credentials; sessions are injected by the gateway.
4. Extract data as tables (CSV) into an artifact, not into your messages.
