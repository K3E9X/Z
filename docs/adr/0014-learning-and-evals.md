# ADR 0014 — Learning loop and evals: how they run for real

Status: accepted

## Context
docs/08 describes signals, proposals, evaluation gates, shadow replay and evals graded by checks and a
rubric. Running them needs decisions the spec leaves open: who eval missions run as, what they may write,
how a candidate skill is tested without being applied, and when the learner runs.

## Decision
1. **Eval cases are real missions.** An eval run creates one mission per case for the target agent with the
   run's model; the case input becomes the prompt, `context_fixtures` the workspace files. Missions carry
   `eval_run_id` (migration 0002) and are left out of the missions list.
2. **Eval missions never change anything.** Their proposed writes are recorded with status `rejected` and
   `result.captured = true` (so `count_actions` checks still work) and never reach Review or the executor;
   their questions are answered at once (the case's `expected.answer`, else a "best judgement" note);
   they emit no learning signals.
3. **Grading** happens when every case mission has ended (learning worker): deterministic checks against the
   job output, artifacts, proposed actions, questions and events; then each rubric item by `z-judge`
   (0 to 2, JSON answer, any failure scores 0). A case passes when every check passes and no rubric item is 0;
   a run passes when every case passes.
4. **Signals** are derived where they happen: from mission events as they are appended (denied calls and
   judge flags → `guardrail_hit`, output repairs → `repair`, failed jobs → `failure`, answered questions,
   rejected actions, failed tool calls → `connector_error`) and from edits in Review (`action_edited` with
   before/after payloads).
5. **Proposals**: the learner runs on `learning.schedule` (default 02:00 UTC) over signals no proposal has used,
   per agent, once there are `learning.min_signals` (default 3). The planner model drafts at most three; each
   is validated (known target, real change, schema-valid playbook, judge-clean memory text) and stored with
   its diff, evidence and the candidate to write (`proposals.candidate`).
6. **Candidates are tested, not applied.** A skill edit with an eval suite runs the suite twice on the agent's
   model, as `learning.run_as` (a real user with `learning:apply`; no hidden service account): once as is,
   once with the candidate text (`eval_runs.overrides`, swapped into the job spec of those missions only).
   Gates: no case that passed fails, no fewer cases pass, token cost increase within
   `max_token_cost_increase`. Without `run_as` the proposal waits and says why. Kinds without an eval suite
   (memory facts, routing rules, playbook edits, skills without evals) are ready at once and say "not
   evaluated".
7. **Apply** writes a new minor version (skill with a `learned` `SkillVersion`, playbook with a
   `PlaybookVersion`), a routing rule with origin `learned`, or an embedded memory fact (org scope needs an
   org-wide grant); rollback uses the existing version history. **Try** runs the candidate on the prompt of a
   recent mission from the evidence, as a one-case eval run (writes captured).
8. **Routing policy**: config `routing_policy` overlaid by admin edits; C2/C3 stay in `never_external` and C3
   in `never_in_memory` whatever is saved (refused otherwise). Rules (`match` → agent/playbook) are versioned,
   deactivated rather than deleted, and suggest who handles incoming tasks; learned rules rank after
   configured ones. The effective policy drives the LiteLLM key allowlists and the default mission class.

## Consequences
- Every claim the learner makes is backed by the same eval suites people run by hand.
- Shadow replay of real missions (docs/08 §4.2) is not implemented yet: "Try" covers one mission on demand.
- Eval runs cost real tokens; they are counted like any mission's.
