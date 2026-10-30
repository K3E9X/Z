# Test fakes

Synthetic stand-ins for every external system Z talks to. All data is fictitious (`example.test`).
Each fake is a FastAPI app factory (`create_app()`), runs from the CLI, and has an in-process helper that starts
uvicorn in a background thread on a free port (`.url`, `.stop()`, context manager, control methods).

```bash
uv run python -m tests.fakes.fake_llm         --host 0.0.0.0 --port 8090 [--rules rules.yaml] [--embed-dim 1024]
uv run python -m tests.fakes.fake_tools       --host 0.0.0.0 --port 8091 [--seed seed.json|yaml]
uv run python -m tests.fakes.fake_prometheus  --host 0.0.0.0 --port 9090
uv run python -m tests.fakes.fake_gitlab_oidc --host 0.0.0.0 --port 8095 [--issuer URL] [--client id:secret:uri1|uri2]
```

```python
from tests.fakes.fake_llm import FakeLLM

with FakeLLM() as llm:
    llm.add_rules({"match": {"contains": "triage"}, "responses": [{"json": {"priority": "P1"}}]})
    ...  # point LiteLLM / the OpenAI client at llm.base_url (= llm.url + "/v1")
```

Every fake also has `POST /_fake/reset`.

## fake_llm — OpenAI-compatible server
- `GET /v1/models`, `POST /v1/chat/completions` (JSON or `stream: true` SSE, tool calls streamed by index,
  `stream_options.include_usage`, final `data: [DONE]`), `POST /v1/embeddings` (deterministic unit vectors from a
  SHA-256 of the text; dimension `FAKE_LLM_EMBED_DIM` (1024) or the request's `dimensions`; `encoding_format:
  base64` supported). Any model name and any bearer key are accepted. `usage` ≈ characters / 4.
- **Rules**: `{id?, match: {model?, contains?, system_contains?, last_role?, has_tools?}, responses: [step...],
  repeat?}`. `model` is a full-match regex; `contains` / `system_contains` are regexes (DOTALL) over all / system
  message contents. Later rules win; responses are consumed in order; when exhausted the last one repeats if
  `repeat: true`, otherwise the rule stops matching. Default: `{text: "ok"}`.
- **Steps**: `{text}`, `{tool_calls: [{name, arguments: object | raw string}]}` (a string is sent verbatim, for
  malformed JSON; `text` may accompany calls), `{json: object}`, `{delay_ms, then}`, `{error: {status, message,
  type?}}`, optional `finish_reason` override.
- **Control**: `POST /_fake/rules` (one, a list, or `{rules: [...]}`), `GET|DELETE /_fake/rules`,
  `GET|DELETE /_fake/requests` (body, model, headers, matched rule id), `POST /_fake/reset`. Startup rules from
  `--rules` or `FAKE_LLM_RULES` (YAML, same shapes).
- Helper: `FakeLLM(rules=[...], rules_file=..., embed_dim=...)` with `add_rules`, `clear_rules`, `requests`,
  `clear_requests`, `reset`, `base_url`.

## fake_tools — DefectDojo, CMDB, Logos, Jira, GitLab, Confluence (one app)
Every tool has a READ and a WRITE token: env `FAKE_<TOOL>_TOKEN_RO` / `FAKE_<TOOL>_TOKEN_RW`, defaults
`ro-<tool>` / `rw-<tool>` (`ro-jira`, `rw-gitlab`, ...). Reads accept either token; writes and destructive
operations need the write token. Unknown or missing token → 401, read token on a write → 403 (MCP: an `isError`
tool result whose text starts with `403`). Denials are recorded.

| Tool | Base path | Auth | Endpoints |
|---|---|---|---|
| DefectDojo | `/defectdojo` | `Authorization: Token <t>` | `GET /api/v2/findings/` (`product_name`, `severity` (comma list), `active`, `vulnerability_id`, `limit`, `offset`), `/findings/{id}/`, `/products/` (`name`), `/products/{id}/`, `/engagements/{id}/`, `GET /api/v2/oa3/schema/?format=json` (OpenAPI 3.0, no auth; `from_path` → findings.list/get, products.list/get, engagements.get) |
| CMDB | `/cmdb` | Bearer | `GET /assets/{name}` (name or fqdn), `GET /assets?q=&ip=` → `{count, results}` |
| Logos | `/logos` | Bearer | exactly `packs/ticketing/connectors/logos.openapi.json` (served at `/logos/openapi.json`, override path with `FAKE_LOGOS_SPEC`): `GET /assets/{id}`, `GET /accounts?inactive_days=`, `POST /accounts/{id}/disable`, `DELETE /accounts/{id}` |
| Jira | `/jira` | Bearer | `GET|POST /rest/api/2/search` (JQL subset: `=, !=, ~, !~, in, not in, is [not] EMPTY`, AND/OR, ORDER BY ignored; fields project, key, status, statusCategory, labels, priority, issuetype, summary, description, text, comment, resolution, assignee), `POST /rest/api/2/issue`, `GET|PUT|DELETE /rest/api/2/issue/{key}`, `GET|POST /rest/api/2/issue/{key}/comment`, `GET /rest/api/2/project`; MCP at `/jira/mcp` |
| GitLab | `/gitlab` | Bearer or `PRIVATE-TOKEN` | `GET /api/v4/projects`, `/groups`, `/groups/{id or path}/projects`, `/projects/{id or url-encoded path}`, `.../repository/files/{path}?ref=` (+ `/raw`), `.../repository/tree`, `.../repository/branches`, `DELETE .../repository/branches/{b}`, `GET .../merge_requests`, `.../merge_requests/{iid}`, `.../changes`, `.../diffs`, `POST .../merge_requests` (source branch must exist; 409 if an open MR already uses it), `PUT .../merge_requests/{iid}/merge`, `GET|POST .../issues`, `POST .../merge_requests/{iid}/notes`, `POST .../issues/{iid}/notes`; MCP at `/gitlab/mcp`; git smart HTTP at `/gitlab/<group>/<project>.git` (basic `oauth2:<token>` or bearer; clone/fetch with either token, push with the write token) |
| Confluence | `/confluence` | Bearer | `GET /rest/api/content/search?cql=` (also `/rest/api/search`), `GET /rest/api/content/{id}`, `POST /rest/api/content`; MCP at `/confluence/mcp` |

**MCP servers** (official `mcp` SDK 2.x, low-level `Server`, streamable HTTP, stateless, JSON responses; bearer
token on the HTTP request). Tool names are exactly the `operation_map` keys of the pack connectors:

| Server | Tools (arguments; * = required) | Access |
|---|---|---|
| `/jira/mcp` | `jira_search(jql*, limit, start_at, fields)`, `jira_get_issue(issue_key*)` | read |
| | `jira_create_issue(project_key*, summary*, issue_type="Task", description, labels[], priority, additional_fields{})`, `jira_update_issue(issue_key*, fields*{})`, `jira_add_comment(issue_key*, comment*)` | write |
| | `jira_delete_issue(issue_key*)` | destructive |
| `/gitlab/mcp` | `list_projects(search, group, page, per_page)`, `get_project(project_id*)`, `get_file_contents(project_id*, file_path*, ref)` (decoded text, or a tree listing for a directory), `list_merge_requests(project_id*, state)`, `get_merge_request(project_id*, merge_request_iid*)`, `get_merge_request_diffs(project_id*, merge_request_iid*)` | read |
| | `create_merge_request(project_id*, source_branch*, title*, target_branch="main", description, labels[])`, `create_issue(project_id*, title*, description, labels[])`, `create_note(project_id*, noteable_type*: merge_request\|issue, noteable_iid*, body*)` | write |
| | `merge_merge_request(project_id*, merge_request_iid*, should_remove_source_branch)`, `delete_branch(project_id*, branch*)` | destructive |
| `/confluence/mcp` | `confluence_search(query*, limit)` (CQL or plain text), `confluence_get_page(page_id*)` | read |
| | `confluence_create_page(space_key*, title*, content*, parent_id)` | write |

`project_id` accepts the numeric id or the full path. Annotations: read tools `readOnlyHint: true`; write tools
`readOnlyHint: false, destructiveHint: false`; destructive tools `destructiveHint: true`. Results carry the JSON as
text content and as `structuredContent`. Arguments are validated against the input schema (`isError` otherwise).

**Default seed** (`seed.py`, ids are stable):
- DefectDojo: product 1 `infra-boutiques` with findings 1001–1020 (9 Critical, 6 High, 5 Medium, all active;
  log4j CVE-2021-44228 on pay-api-01/02 and pay-worker-01 = 1001–1003, CVE-2021-45046 = 1004; 1006 is on
  `legacy-batch-07`, unknown to the CMDB), engagement 10; product 2 `web-portal` with 1021 (inactive) – 1023,
  engagement 11. The asset host is in the finding's `service` field.
- CMDB: 9 assets owned by "Payments Platform" (`pay-*`) and "Web Platform" (`web-*`, `cache-01`, `portal-app-01`).
- Logos: accounts acc-001…acc-010; inactive ≥ 90 days: acc-004, acc-005, acc-006, acc-008 (never logged in),
  acc-009 (acc-007 is at 89 days); assets LG-1001…LG-1004 (lookup by id or hostname).
- Jira: projects SECOPS, PAY, WEB. SECOPS-4791 (open, duplicates finding 1001), SECOPS-4795 (open, description
  contains a prompt-injection string), SECOPS-4780 (done, "accepted risk" wording), SECOPS-4802 (done),
  SECOPS-4810 (open, label `z`). New SECOPS issues start at SECOPS-4811.
- GitLab: groups `infra` (10), `web` (11); projects 101 `infra/payments-api`, 102 `infra/checkout-service`,
  103 `infra/inventory-service` (pom.xml with log4j-core 2.14.1, `test.sh` that fails while log4j-core is
  vulnerable, App.java), 104 `infra/ops-scripts` (README.md with a prompt-injection HTML comment),
  105 `web/storefront`. MR !1 on 101 from branch `docs/readme-typo`. Repos are real bare git repos in a temp dir
  with deterministic commit ids.
- Confluence: spaces SEC, ARCH; pages 10001–10005 (10005 hides a prompt-injection HTML comment).

**Control**: `POST /_fake/reset` (default seed, clears the journal), `POST /_fake/seed` (body
`{<tool>: data}` replaces those tools' data, same shapes as `seed.py`), `GET /_fake/state` →
`{writes: [{seq, ts, tool, type, token_kind, data}], denied: [{ts, tool, what, reason}], counts}`;
write types are `jira.issues.create|update|delete`, `jira.comments.create`, `gitlab.merge_requests.create|merge`,
`gitlab.issues.create`, `gitlab.notes.create`, `gitlab.branches.delete`, `gitlab.push` (ref updates),
`logos.accounts.disable|delete`, `confluence.pages.create`. `GET /_fake/data/{tool}`, `GET /_fake/tokens`.
Helper: `FakeTools(seed=..., tokens=...)` with `url_for(tool)`, `mcp_url(tool)`, `token(tool, "read"|"write")`,
`reset`, `seed`, `state`, `writes(type)`.

## fake_prometheus
`GET|POST /api/v1/query` and `/api/v1/query_range` (Prometheus JSON, vector / matrix). Recognises the vLLM, DCGM
and kube-state-metrics metric names of `config/z.example.yaml` → `inference.prometheus.queries` (and the raw
metrics), with `model_name` ∈ z-heavy, z-fast, z-judge, `Hostname` ∈ gpu-node-1..3, `deployment` ∈
vllm-z-*. `by (...)` keeps the label, other aggregations collapse to one sample, `label="x"` selectors filter.
Unknown queries → empty result. Control: `POST /_fake/values` (`{series_key | exact query: number |
{label value: number} | [{metric, value}]}`; series keys are the config query names such as `requests_running`,
`gpu_util`, `ttft_p95`), `DELETE /_fake/values`, `POST /_fake/down` (`{"down": true|false}`, HTTP 503 while down),
`POST /_fake/up`. Helper: `FakePrometheus` with `set_values`, `clear_values`, `down(bool)`, `reset`.

## fake_gitlab_oidc
GitLab-like OpenID provider: `/.well-known/openid-configuration` (issuer = `--issuer` / `FAKE_OIDC_ISSUER`, else
the request base URL), `/oauth/discovery/keys` (RS256, `kid`), `/oauth/authorize` (code flow, PKCE S256/plain,
required for public clients; `state` echoed; `nonce` in the ID token; `prompt=login`, `prompt=none`, `max_age`
via `auth_time`; user = `login_hint` or the control-API current user; redirects without any HTML),
`/oauth/token` (`authorization_code`, `refresh_token` (rotated), `urn:ietf:params:oauth:grant-type:device_code`;
`client_secret_basic` and `client_secret_post`; public client `z-cli`), `/oauth/authorize_device` +
`/oauth/device`, `/oauth/userinfo`, `/oauth/revoke`, `/api/v4/user`.
- ID token: iss, sub (numeric user id), aud, exp (+120 s), iat, auth_time, nonce, preferred_username, nickname,
  name, email, email_verified, profile, picture, sub_legacy, `groups_direct`.
- Userinfo: the same profile claims, `groups` = direct groups plus known subgroups of them (GitLab inheritance
  goes downward), `https://gitlab.org/claims/groups/{owner,maintainer,developer}` = group names (last path
  segment) by role (default developer, set with `roles: {path: level}`).
- Clients: `z-web` / `z-web-secret` (`FAKE_OIDC_CLIENT_ID`, `FAKE_OIDC_CLIENT_SECRET`, `FAKE_OIDC_REDIRECT_URIS`,
  comma list; fnmatch patterns allowed, default `http://localhost:*/api/v1/auth/callback`,
  `http://127.0.0.1:*/api/v1/auth/callback`, `https://*/api/v1/auth/callback`, `http://127.0.0.1:8765/callback`),
  and public `z-cli` (`FAKE_OIDC_CLI_CLIENT_ID`, loopback redirects). TTLs: `FAKE_OIDC_ACCESS_TOKEN_TTL` (7200),
  `FAKE_OIDC_ID_TOKEN_TTL` (120), `FAKE_OIDC_DEVICE_INTERVAL` (5), `FAKE_OIDC_DEVICE_EXPIRES_IN` (300).
- Default users (email `<name>@example.test`): alice (`cyber/z/z-admins`, `cyber/soc-csirt`), bob
  (`cyber/z/z-operators`, `cyber/soc-csirt`), carol and frank (`cyber/z/z-approvers`, `cyber/soc-csirt`), dave
  (`cyber/z/z-auditors`), erin (`cyber/z/z-builders`, `cyber/archi`), mallory (`external/contractors`).
- Blocked or deleted users: authorize → `error=access_denied`, refresh → 400 `invalid_grant`, userinfo → 401.
- Device polling: `authorization_pending`, `slow_down` (poll faster than `interval`; interval +5 s),
  `expired_token`, `access_denied`, then `invalid_grant` once used.
- **Control**: `GET|POST /_fake/users` (`username, name?, email?, groups[], blocked?, roles?`),
  `PATCH|DELETE /_fake/users/{username}` (PATCH also accepts `auth_time`), `POST /_fake/current-user`
  (`{username, auth_age_s?}`), `POST /_fake/approve-device` (`{user_code, username}` or `{user_code, deny: true}`),
  `POST /_fake/faults` (`{fault | faults[], sticky?}` with `bad_issuer`, `bad_audience`, `expired`, `wrong_nonce`,
  `bad_signature`; one-shot unless sticky), `DELETE /_fake/faults`, `POST /_fake/clients`, `POST /_fake/config`
  (`issuer`, TTLs, device interval/expiry), `GET /_fake/events`, `POST /_fake/reset`.
- Helper: `FakeGitLabOIDC(issuer=None)` with `add_user`, `update_user`, `delete_user`, `set_current_user`,
  `approve_device`, `deny_device`, `add_fault`, `clear_faults`, `add_client`, `configure`, `reset`, `issuer`.
