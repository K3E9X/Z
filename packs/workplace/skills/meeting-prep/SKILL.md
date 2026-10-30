---
name: meeting-prep
description: Prepare for a meeting and follow it up.
version: 1.1.0
evals: evals/meeting-prep.jsonl
---

Before: read the invite, related mails and documents; produce a one-page brief (goal, attendees, open points, decisions needed).
After (from the Teams transcript when available): decisions, actions with owners and dates; propose `m365.teams.messages.post` to the meeting chat, and when asked, one `m365.todo.tasks.create` per action the person owns (title, due date; find their list with `m365.todo.lists.list`). Actions owned by others go in the chat message, never into their task lists.
