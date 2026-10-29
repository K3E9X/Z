---
name: jira-dedup
description: Find existing tickets for the same problem before proposing a new one.
version: 1.0.0
evals: evals/jira-dedup.jsonl
---

1. Search open issues in the target project with the CVE or the key words of the title and the asset name.
2. Treat as duplicate when the same problem and the same asset or owner team appear in an open ticket.
3. For a duplicate, propose a comment on the existing ticket instead of a new ticket, and list it as excluded.
