# Contracts

Source of truth between components once tagged `contracts-v1` (CLAUDE.md). Changes are their own commit,
made by the main session; every consumer follows. Tests: `uv run pytest packages/z-common/tests`.

| File | What |
|---|---|
| `openapi.yaml` | Generated from `tools/build_openapi.py` (edit the script, regenerate; a test checks they match). |
| (content) | Public and internal REST + SSE API (OpenAPI 3.1). Every operation has `x-permission`, `x-owner`, and `x-audit` / `x-step-up` where they apply |
| `permissions.yaml` | Permission catalog and built-in roles (docs/12 §2) |
| `common.schema.json` | Shared enums and shapes (statuses, data classes, ids, budgets, problem details) |
| `agent.schema.json`, `playbook.schema.json`, `connector.schema.json`, `pack.schema.json`, `skill-frontmatter.schema.json`, `objective.schema.json`, `eval-case.schema.json`, `mission-plan.schema.json` | Pack manifests (docs/03 §4) |
| `action.schema.json` | Action envelope, previews, built-in payloads |
| `events.schema.json` | Event envelope and every payload (docs/03 §3) |
| `config.schema.json` | `config/z.yaml` after `${VAR}` substitution |
| `examples/<schema>/` | Example documents; `invalid-*` files must fail |

## Conventions
- **Ids**: server-assigned strings. **Timestamps**: RFC 3339 UTC.
- **Pagination**: `?cursor=&limit=` → `{items, next_cursor}`.
- **Errors**: `application/problem+json` (RFC 9457) with `type` one of
  `urn:z:problem:unauthenticated` (401), `urn:z:problem:step-up-required` (401, `step_up_url`),
  `urn:z:problem:forbidden` (403, `permission`), `urn:z:problem:separation-of-duties` (403),
  `urn:z:problem:not-found` (404), `urn:z:problem:conflict` (409), `urn:z:problem:locked` (409, locked guardrail),
  `urn:z:problem:validation` (422, `errors[]`), `urn:z:problem:rate-limited` (429),
  `urn:z:problem:budget-exceeded` (402), `urn:z:problem:unavailable` (503), `urn:z:problem:internal` (500, no stack trace).
- **Auth**: browser `__Host-z_session` cookie + `X-CSRF-Token` header (value of the `__Host-z_csrf` cookie) on
  state-changing requests; CLI/scripts `Authorization: Bearer <token>`; runners `Authorization: Bearer <job session token>`.
- **SSE**: `id: <seq>`, `event: <type>`, `data: <JSON>`; resume with `Last-Event-ID` (or `?after_seq=`).
  `GET /stream` sends `heartbeat` every 15 s.
- **Operation ids**: `<connector>.<resource>.<verb>`; writes always full ids (`WriteOperationId`, ≥ 3 segments);
  globs (`gateway:<connector>.<resource>.*`) only ever expand to read operations.
- **Tool names seen by models**: operation id with `.` replaced by `__` (`jira__issues__search`), because
  OpenAI-compatible function names cannot contain dots. Built-ins keep their names (`propose_action`, `ask_human`…).
- **Expressions** (`when`, `fanout.when`): a safe subset — literals, `== != < <= > >= in not in and or not`,
  attribute and index access over `inputs`, `item`, `steps.<id>.output`, `steps.<id>.outputs`, and
  `<step>_for(item)` = output of step `<step>` for the same fan-out item. No calls other than `<step>_for`.
- **Templates** (`prompt`, `repo`, trigger `inputs`): `{{ inputs.x }}`, `{{ item.x }}`, `{{ steps.<id>.output.x }}`,
  `{{ event.x }}`; rendered with a sandboxed engine, missing values are errors.

## Runner protocol (internal API)
1. The executor starts `z-runner --job <id>` with `Z_SERVER_URL`, `Z_JOB_ID` and a **single-use** bootstrap token
   `Z_JOB_TOKEN` (Kubernetes: a per-job Secret).
2. `GET /internal/jobs/{id}/spec` consumes the bootstrap token and returns `JobSpec`, including a `session_token`
   valid until the job deadline for every later internal and gateway call, and the mission's LiteLLM virtual key.
3. Workspace: `GET /internal/jobs/{id}/workspace` (tar.gz, judge-scanned server-side, see `.../workspace/manifest`).
4. Events: `POST /internal/jobs/{id}/events` returns `JobControl` (pause, cancel, guide messages, answers,
   action status, rotated LLM key). While blocked, poll `GET /internal/jobs/{id}/control?wait_s=`.
5. Checkpoint after every step; `complete` at the end. The runner never sends or chooses the data class.
