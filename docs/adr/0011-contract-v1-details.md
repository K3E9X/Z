# ADR 0011 — Details fixed while writing contracts v1

Status: accepted

## Context
Writing `contracts/` from docs/03 required decisions the specification did not make explicitly.

## Decision
1. **Runner auth**: a single-use bootstrap job token (env / per-job Secret) fetches the job spec, which carries a
   session token valid until the job deadline. A leaked environment cannot be replayed after start.
2. **Runner control channel**: `GET /internal/jobs/{id}/control` (long poll) and the `JobControl` body returned by
   event and checkpoint posts deliver pause, cancel, "Guide the agent" messages, answers and action status.
3. **Extra internal endpoints**: workspace archive + manifest (server-side staging and judge scan), memory search,
   sub-agents, objective report. The judge scan of staged inputs runs in z-server, not in the runner, because the
   runner container also runs agent-controlled commands.
4. **`judge` event type** for verdicts that are not inline tool results (staged inputs, proposed actions, prompts).
5. **Tool names** for models are operation ids with `.` → `__` (OpenAI function-name charset).
6. **Write ids** have at least three segments (`WriteOperationId`); builtin read ops may have two (`web.fetch`).
7. **Connector `declared`** is a top-level key (docs/03 §4 canonical); the `cmdb` seed was fixed accordingly.
8. **Config substitution** happens on parsed YAML values (`${VAR}`, `${VAR:-default}`), whole-placeholder values are
   re-typed; `memory.embedding_dim` is configurable (pgvector column dimension).
9. **Playbook `baseline_minutes`** feeds "hours saved" in Analytics; **`args_map`** on a rule maps a canonical
   built-in payload to a connector's own argument names.
10. **Classifier** reads the verb (last id segment, split on `_`), so `merge_requests.create` is a write and
    `merge_requests.merge` destructive; at runtime an MCP tool is read only with `readOnlyHint: true` and a read verb.

## Consequences
The runner, gateway and UI share one precise protocol; the deviations are covered by contract tests.
