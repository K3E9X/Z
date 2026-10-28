# 08 — Learning loop and evals

Z improves agents without training models: it changes **skills**, **memory** and **routing**, and only after tests and human approval.

## 1. The loop
```
Observe ──► Propose ──► Evaluate ──► Validate ──► Apply (versioned) ──► Observe
 signals     z-heavy      evals +       human        new version,
             drafts a     shadow        approves     rollback anytime
             change       replay
```

## 2. Signals (Observe)
| Signal | Example |
|---|---|
| `action_edited` | The user changed a ticket before approving (diff stored) |
| `action_rejected` | Reject with a reason |
| `question_answered` | The answer to an agent question |
| `mission_reassigned` | The user moved a task to another agent |
| `retry` / `repair` | Output repaired, tool call corrected |
| `failure` | Job failed, with error class |
| `guardrail_hit` | Denied call, judge flag |
| `feedback` | Thumbs or comment on a result |
| `connector_error` | Repeated tool failure for a reason the agent could avoid (e.g. wrong mirror) |

Signals are cheap rows; the learner batches them by agent, skill and pattern.

## 3. Proposals (Propose)
The learner runs on a schedule (default nightly) or on demand and asks `z-heavy` to look at clusters of signals with the current skill/agent text. It can propose:
| Kind | Change |
|---|---|
| `skill_edit` | A diff to a `SKILL.md` (new step, clarified rule, example) → new minor version |
| `memory_fact` | A fact for org/team/agent memory ("builds use the internal Maven mirror") |
| `routing_rule` | "Dependency findings go to Dev Engineer" |
| `playbook_edit` | Change a step, a fan-out, a default input |

Each proposal carries its evidence (signal ids), the diff, and expected effect.

## 4. Evaluation (Evaluate)
1. **Eval suite** of the target: run before and after on the same model and seed settings.
2. **Shadow replay**: replay N recent real missions (inputs only, read-only connectors, writes captured not applied) with the new version; compare outputs to what humans approved.
3. **Gates**: no regression on any eval case, score not lower overall, token cost increase under the configured limit (default +15 %). A proposal that fails a gate is `discarded (regression)` and shown as such.

## 5. Validation and apply
- Review shows the diff, evidence, before/after scores, regressions, cost delta. Actions: **Apply**, **Try on one mission**, **Reject**.
- Apply creates a new version of the skill/agent/playbook/memory, records who approved, and announces it in History.
- Rollback restores the previous version in one click (`POST /skills/{id}/rollback`).
- Role: `builder` for the team's assets; `admin` for org-wide memory and routing.

## 6. Evals
- Format: JSONL per agent or skill, validated by `contracts/eval-case.schema.json`: `{id, input, context_fixtures?, expected?, rubric[], checks[]}`. Fixture paths are relative to the pack (`evals/fixtures/…`).
- **Checks** are deterministic. Enum: `json_schema {schema}`, `field_equals {path, value}`, `contains {path, value}`, `regex {path, pattern}`, `artifact_exists {name}`, `diff_contains {value}`, `tests_pass`, `asked_human`, `count_actions {operation, equals}`, `no_action {operation}`, `no_write_tools`, `has_sources {min}`, `duplicate_detected`.
- **Rubric** items are graded by `z-judge` with a 0–2 scale and a short justification; graders are themselves spot-checked by humans weekly.
- `z eval <agent|skill> --models z-fast,z-heavy` compares models; results show on the agent's Evals tab and drive the default model choice per agent.
- Minimum per seed agent: 10 cases; per skill: 5.

## 7. Memory
- Scopes: org, team, agent. Stored with embeddings (`z-embed`, pgvector).
- Retrieval: top-k facts by similarity to the step prompt, injected in a fixed "Known facts" block (stable position for prefix caching).
- Every fact has source, approver, version, optional expiry; C3 content is refused.
