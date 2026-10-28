---
name: inbox-triage
description: Sort unread mail and draft replies for the user to approve.
version: 1.0.0
---

1. Read unread mail from the last 24 h (or the given window).
2. Classify: action needed, FYI, newsletter, automated.
3. For "action needed", draft a reply in the user's voice; propose `m365.mail.reply` per draft.
4. Output a short digest: what needs a decision today.
Never send, delete or move mail.
