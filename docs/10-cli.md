# 10 — The `z` command line

Mockup: `design/mockups/CLI.html` and `design/screenshots/CLI.png`.

The CLI does everything the web app does. It has three faces:
1. **`z`**: an interactive session that feels like Claude Code;
2. **`z top`**: a full-screen live dashboard of every agent;
3. **subcommands** for scripts and CI, with `--json` everywhere.

Stack: Python, Typer (subcommands), **Textual** (interactive session in inline mode, `z top` full screen), Rich (output), `httpx` + SSE client generated from `contracts/openapi.yaml`. Install with `uv tool install` or `pipx`. Ships shell completions. If `z` collides with another tool on a machine (e.g. zoxide), `zctl` is installed as an alias.

## 1. Interactive session (`z`)
Layout, top to bottom (see the mockup):
- **Welcome box**: version, workspace (team, environment), current directory, inferX health and aliases.
- **Transcript**: the user's prompt, then a live tree per agent: plan steps with status marks (✓ done, ◐ running, ○ pending), tool calls with short args and durations, judge verdicts, blocked calls, sub-agents.
- **Inline decisions**: approvals and questions appear as boxed prompts with numbered choices (Approve all / Review one by one / Edit before approving / Reject and tell the agent why). Arrow keys or numbers.
- **Input box**: `@` completes agents by their handle (`@atlas`), `#` completes connectors, `/` opens slash commands, `!` runs a local shell command (never sent to agents).
- **Status line**: autonomy mode, agents running, pending decisions, tokens today, hint keys.

Keys: `Enter` send · `Shift+Enter` newline · `Esc` interrupt the focused agent (asks before stopping) · `Shift+Tab` cycle mode (`propose` → `read_only` → `plan first`) · `Ctrl+R` search history · `Ctrl+O` expand the last tool output · `Ctrl+T` toggle the task list · `?` shortcuts.

Plan first (`/plan` or `Shift+Tab`): the mission gets `plan_first: true`; Z drafts the plan and waits; nothing runs until you accept. Autonomy values are those of 03 §2; the strictest of agent, playbook and session wins.

### Slash commands
| Command | Does |
|---|---|
| `/agents` | Roster with status; pick one to inspect |
| `/missions` | Running and recent missions; switch focus |
| `/review` | Pending approvals and questions, one by one |
| `/playbooks` | List and run a playbook |
| `/connectors` | Health and latency; test one |
| `/usage` | Tokens today by model and agent; budget left |
| `/model` | Override the model alias for this session |
| `/autonomy` | Set `propose` or `read_only` for missions started from this session |
| `/theme` | night, day, slate, contrast (matches the web themes) |
| `/login`, `/logout`, `/whoami` | Auth |
| `/resume` | Pick a past session |
| `/export` | Save the transcript as Markdown |
| `/help` | Everything |

### Sessions
A session is a conversation that can start missions. Sessions are stored server-side; `z -c` continues the last one, `z --resume` lists them.

## 2. `z top`
Full-screen, refreshes over SSE (like k9s/htop):
- Header: workspace, inferX health, tokens/s, queue, p95 latency, budget used.
- Agent table: agent, mission, state, step, tokens, time; selected row highlighted.
- Connectors line with health marks; Review line with counts and oldest age.
- Live pane for the selected agent's last events.
- Keys: `↵` open · `a` approve (opens the decision) · `l` logs · `p` pause · `t` take over (pauses the job with a read-only take-over and prints the link to the web take-over page; the CLI does not open a shell itself) · `x` stop · `/` filter · `:` command · `?` help.

## 3. Subcommands
```bash
z login [--url https://z.example]        # GitLab OIDC: device flow (GitLab ≥ 17.9) or browser + PKCE; Z CLI token in the OS keyring
z logout · z whoami                      # whoami shows user, teams, effective roles
z admin …                                # see 12-identity-access-and-admin.md §3
z -p "triage critical findings for product X" --playbook weekly-vuln-triage \
  --output-format text|json|stream-json  # headless; exit code reflects the mission result
z run weekly-vuln-triage --input product=infra --wait
z missions [--status running] [--json]
z logs <mission|job> -f
z review                                  # interactive one-by-one
z approve <action-id> [--comment …]  ·  z reject <action-id> --reason "…"
z answer <question-id> "Skip it, the pin is intentional"
z pause|resume|stop <mission>
z agents [show|create|rename|duplicate] <handle>   # create: --from-template vuln-analyst --name Vega
z agents create --describe "an agent that keeps our Jira SECOPS backlog clean"
z objectives [list|show|add|pause] --agent @vega
z assign @vega jira_project:SECOPS group:infra/
z connect m365                            # personal delegated connection to Microsoft 365 (browser opens)
z skills [list|show|versions|rollback <skill> --to <version>]   # diff: not built yet
z playbooks [list|show|run]                 # save-from <mission>: not built yet (API: POST /missions/{id}/save-as-playbook)
z connectors [list|show|operations|test <name>|approve-exposure|set-operation]   # import, record-browser: not built yet (web: Connector builder, Browser recorder)
z usage --by agent|model|mission|team --since 7d
z inference status
z eval <agent|skill> --models z-fast,z-heavy
z learn proposals · z learn apply <id> · z learn reject <id>
z audit export --since 30d --format json > audit.jsonl
z mcp serve                               # expose Z as an MCP server on stdio
z config get|set <key> <value>
```
- `--json` on every list or show command; stable field names from the contracts.
- Exit codes: `0` success, `1` mission failed, `2` usage error, `3` needs attention (headless mode stops and prints the review URL), `4` auth, `5` budget exceeded.

## 4. Config
`~/.config/z/config.toml`: `url`, `team`, `theme`, `autonomy_default`, `editor`. Token in the OS keyring (fallback: `~/.config/z/token` mode 0600). Env overrides: `Z_URL`, `Z_TOKEN`.

## 5. Tests
- Textual pilot tests (`App.run_test`) of the session and `z top`. Snapshot tests (`pytest-textual-snapshot`) are not built yet.
- Contract tests against a mock server generated from `openapi.yaml`.
- Headless mode tested in `cli/tests` against a faked API; not yet in the Compose e2e suite.
