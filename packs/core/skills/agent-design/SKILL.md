---
name: agent-design
description: Design a least-privilege agent profile from a description.
version: 1.0.0
evals: evals/agent-design.jsonl
---

1. Identify the job in one sentence and the objects the agent will look after.
2. Choose a short human name and a handle (lowercase, no spaces). Suggest an avatar color.
3. Pick existing skills first; propose a new skill only if nothing fits, with a one-line purpose.
4. Pick tools: read operations only, the fewest that do the job. Writes are listed as proposals the agent may make.
5. Pick the model: z-fast for high-volume extraction and triage, z-heavy for reasoning, code and writing.
6. Suggest 0–3 objectives with a measurable metric and a cadence.
7. Output the agent YAML (see contracts/agent.schema.json).
