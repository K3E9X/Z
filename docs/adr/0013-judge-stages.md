# ADR 0013 — Judge stages: pattern floor, raise-only model, pre-action checks

Status: accepted

## Context
docs/04 §6 describes `z-judge` as a small model that scans tool results and staged inputs, checks proposed
actions, routes and grades. The text it reads is untrusted by definition (invariant 6), so a model judge can
itself be talked to. The config also exposes an unlocked `judge_injection_scan` guardrail, while invariant 5
says platform guardrails cannot be disabled.

## Decision
1. **Two stages.** The deterministic pattern scan (`judge.scan`, corpus in `tests/fixtures/injection/`) is the
   platform floor and always runs on tool results, staged inputs, intake events and proposed payloads. The
   model stage (`judge_model.review`, alias `judge.model`, default `z-judge`) runs after it and **can only raise**
   the verdict (clean → flagged → blocked). A model answer of "clean" never lowers a pattern verdict.
2. **The guardrail controls the model stage only.** `judge_injection_scan: false` (or `judge.model_stage: false`)
   turns the model stage off; the pattern scan cannot be turned off. The guardrail description says so.
3. **Hardening of the model call.** The text is fenced between two lines carrying a random marker (removed from
   the text first), the system prompt says the content is data, the answer must be JSON naming a known
   verdict, the sample is capped (6000 characters, head and tail), timeout 8 s. Failures keep the pattern
   verdict and are logged. Tokens are platform usage (`z:judge`) attributed to the mission's owner.
4. **Staged files**: at most 20 text files per job go to the model (all go through the patterns); verdicts are
   cached by job and content hash because the manifest and the archive are staged separately.
5. **Pre-action checks** run when an agent proposes a write and when a reviewer edits one: injection (patterns +
   model on the payload text), data class of the mission vs. the destination connector, fields (description,
   assignee, due date for issues; non-empty comments), duplicates (same change pending in Z, or an open issue
   with the same title found through the connector's read search). The result is the action's
   `judge_verdict` (`ok` / `warn` / `block` + checks). A **blocked payload cannot be approved**; the reviewer
   rejects it or edits it, and the edited version is checked again. Checks that need a tool say "not checked"
   when it is unavailable and never fail the proposal.

## Consequences
- The judge stays useful when the model is down or fooled; the model adds recall, not a way around the floor.
- The judge model is evaluated like any other model (the injection corpus doubles as its eval set).
- Routing and eval grading reuse the same platform-key path (`llm/chat.py`).
