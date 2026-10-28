---
name: backlog-grooming
description: Find stale, duplicate and incomplete tickets and propose fixes.
version: 1.0.0
---

1. List open tickets older than the threshold (default 60 days) or with no owner or no due date.
2. Group duplicates; propose one to keep and comments to link the others.
3. For incomplete tickets, propose the missing owner (from the CMDB) and due date (from policy).
4. Propose closure only with a reason (fixed, obsolete asset, accepted risk with a reference).
Output a table artifact (CSV) and one proposed action per change.
