---
name: project-pre-review
description: Draft a project security pre-review from its project file.
version: 1.0.0
evals: evals/project-pre-review.jsonl
---

Sections:
1. Context (purpose, users, data handled and their class, hosting).
2. Architecture and data flows (who talks to whom, protocols, trust boundaries).
3. Risks (scenario, impact, likelihood, proposed measure), most severe first.
4. Open questions for the project team.
5. Security requirements (numbered, testable).
Deliver as `pre-review.docx` and `pre-review.md`. Mark assumptions with "Assumption:".
