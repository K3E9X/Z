# ADR 0029 — Every job may write its report; pack tool gaps closed

Status: accepted

## Context
The job result contract carries `report_artifact` (the job's `report.md`, uploaded as a `report` artifact), the
eval grader reads it as `report` (docs/08 §6), and 80 eval cases of the archi-isp and workplace packs check it.
But the runner never asked the agent to write it, and only agents with the `edit` tool could write a file at all:
12 of the 13 seed agents (all but dev-engineer) could not, so those cases could never pass and their missions
produced no report. The eval work also listed pack gaps left to the owners: no `read` tool for agents that receive
staged files, no tool for the browser-flows "CSV artifact" step, no Microsoft 365 operation behind meeting-prep's
"tasks". The owner asked for these to be decided without waiting for validation.

## Decision
1. **The report is part of every job.** The task message asks for `report.md` (answer first, then what was done,
   sources, and what could not be done or verified). An agent without `edit` gets an `edit` tool limited to
   `report.md`, plus `result.json` when the step has an output, exactly as `result.json` alone was before. No
   other path can be written, so least privilege on the workspace holds.
2. **Output schemas stay opt-in.** The archi-isp and workplace agents produce prose for people; their checks read
   `report`. Structured outputs are added where a later step or a tool consumes the result (vuln triage).
3. **`read` for every agent that can receive staged files** (assistant, researcher, agent-builder, browser-agent,
   orchestrator, ticket-manager): workspace-only, no external effect.
4. **browser-agent gets `documents`** so a flow's table becomes a CSV artifact, as its skill says.
5. **Microsoft To Do in the m365 subset**: `todo.lists.list` (read, `Tasks.Read`) and `todo.tasks.create` (write,
   `Tasks.ReadWrite`, closed body: title, text body, due date, importance), checked against the official Graph
   description (`microsoftgraph/msgraph-metadata`). meeting-prep proposes one task per action the person owns;
   actions owned by others go in the chat message, never into their task lists.

## Consequences
- Every mission now has a readable report per job, and the 80 report-based eval cases can pass.
- Agents and skills changed version (1.1.0); a learning-loop proposal against the old versions must be rebased.
- Google Tasks is not added (no Google Tasks connector yet); meeting-prep's tasks are Microsoft 365 only.
