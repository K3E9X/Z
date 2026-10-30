---
name: summarize
description: Summarize one text file in three bullet points with a title.
version: 1.0.0
evals: evals/summarizer.jsonl
---

Input: the path of one file in the workspace.

1. Read the file with the `read` tool. Read it fully before writing.
2. Write a title of at most 8 words.
3. Write exactly 3 bullet points, each one sentence, each based on the file.
4. If the file is empty or unreadable, say so in the title and leave `bullets` empty.
5. Output `result.json` matching `schemas/summary.json`.

Example `result.json`:
```json
{"title": "Q4 risk review for the payments platform", "bullets": ["Two critical findings are still open.", "The log4j upgrade is planned for next week.", "No incident was reported this quarter."], "source": "notes.md"}
```
