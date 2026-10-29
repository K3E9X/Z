# z — the Z command line

`z` does from a terminal what the Z web app does (docs/10-cli.md). It has three faces:

1. **`z`**: an interactive session, like Claude Code: start missions with `@agents`, watch every agent live,
   decide on writes and questions inline.
2. **`z top`**: a full-screen live dashboard of every agent (like k9s or htop).
3. **subcommands** for scripts and CI, with `--json` on every list and show command and documented exit codes.

`zctl` is installed as an alias for machines where `z` is taken (zoxide).

## Install

```bash
uv tool install ./cli --with ./packages/z-common    # from a checkout of the Z repository
# or
pipx install ./cli --preinstall ./packages/z-common
# from source, without installing:
uv run z --help
```

Shell completions: `z completions bash|zsh|fish|powershell` prints the script, for example
`z completions zsh > ~/.zfunc/_z` or `eval "$(z completions bash)"` (`--prog zctl` for the alias).
`z --install-completion` installs it for the current shell.

## Sign in

```bash
z login --url https://z.example     # GitLab device flow; the URL is saved to the active profile
z whoami                            # user, teams, effective roles, session
z logout                            # ends the Z session on the server and forgets the token
```

- `z login` asks Z for its sign-in settings (`GET /api/v1/auth/cli/config`), then runs the **OAuth device
  authorization grant** against GitLab (GitLab ≥ 17.9): it prints the code, opens the verification page and
  waits. On older GitLab, or with `--pkce`, it uses **authorization code + PKCE** on the fixed loopback redirect
  `http://127.0.0.1:8765/callback`. The GitLab tokens are exchanged for a **Z CLI token**
  (`POST /api/v1/auth/cli/exchange`, 30 days, bound to the device name) and then dropped.
- On a server in `auth.mode: dev`, `z login` signs in its local development user.
- Scripts and CI: create a scoped personal token (`z tokens create ci -s missions:read --days 30`) and either
  `echo "$TOKEN" | z login --with-token` or set `Z_TOKEN`.
- The token is stored in the **OS keyring** when one is available, else in `~/.config/z/token` (mode 0600,
  directory 0700). `Z_KEYRING=off` forces the file.

## Configuration

`~/.config/z/config.toml` (or `$XDG_CONFIG_HOME/z`, or `$Z_CONFIG_DIR`):

```toml
url = "https://z.example"          # the "default" profile
team = "Cyber Ops"
theme = "night"                    # night | day | slate | contrast
autonomy_default = "propose"       # propose | read_only
editor = "vim"                     # for `z review edit` (default: $VISUAL, $EDITOR, vi)
profile = "staging"                # active profile (optional)

[profiles.staging]
url = "https://z-staging.example"
```

```bash
z config set theme slate · z config get url · z config list
z config add staging https://z-staging.example --use · z config use default · z config profiles
z --profile staging missions list
```

Environment: `Z_URL`, `Z_TOKEN`, `Z_PROFILE`, `Z_CONFIG_DIR`, `Z_KEYRING=off`, `NO_COLOR=1`, `Z_WEB_URL` (links to the web app when it is not the API origin, e.g. compose: `http://127.0.0.1:8080`).

## The interactive session (`z`)

```
╭──────────────────────────────────────────────╮
│ ✻ Welcome to Z  v0.1.0                       │
│   Cyber Ops · local · ~/work                 │
│   inferX ● healthy · z-heavy · z-fast        │
╰──────────────────────────────────────────────╯
> summarize the meeting notes @summarizer #confluence
● summarize the meeting notes … · running
  ⎿ ◐ summarize   @summarizer
● Summarizer · running · z-fast · 15k tokens
  ⎿ read(path="notes.md")  → 40 lines  120ms
  ⎿ blocked curl https://nvd.nist.gov · network egress not allowed
```

- Mention an agent with `@handle` (and connectors with `#name`) to start a mission. While it runs, what you
  type **guides** it; `/new` starts another one.
- Approvals and questions appear inline as boxed prompts with numbered choices: *Approve all*, *Review one by
  one*, *Edit before approving*, *Reject, and tell the agent why*. Arrows or digits, Enter to pick. Every
  approval shows the preview, the **runs-as** identity, reversibility and the judge verdict first; destructive
  actions need the operation id typed back.
- `!command` runs a **local** shell command in the session's directory; it is never sent to agents.

| Key | Does |
|---|---|
| `Enter` / `Shift+Enter` (or `Ctrl+J`) | send / newline |
| `Esc` | interrupt the focused agent (asks: pause, stop, keep running) |
| `Shift+Tab` | cycle mode: `propose` → `read_only` → `plan first` |
| `Ctrl+R` | search history (`~/.local/state/z/history`) |
| `Ctrl+O` | expand the last tool output and every call |
| `Ctrl+T` | toggle the task list |
| `↑` `↓` / `Tab` | history; move in and accept completions |
| `?` | shortcuts (on an empty input) |

Slash commands: `/agents` `/missions` `/review` `/playbooks` `/connectors` `/usage` `/model` `/autonomy`
`/plan` `/theme` `/new` `/guide` `/stop` `/login` `/logout` `/whoami` `/resume` `/export` `/clear` `/help` `/quit`.

## `z top`

Header (workspace, inferX health), one row per agent (mission, state, step, tokens, time), connectors and
review lines, and a live pane with the selected agent's last events. Lists refresh every few seconds
(`--refresh`); events arrive over SSE and resume with `Last-Event-ID` after a drop.

Keys: `↵` open · `a` approve (opens the decision) · `l` logs · `p` pause/resume · `t` take over · `x` stop ·
`r` refresh · `/` filter · `:` command (`review`, `refresh`, `filter …`, `theme …`, `quit`) · `?` help · `q` quit.

## Subcommands

```bash
# missions
z run weekly-vuln-triage --input product=infra --input min_severity=critical --wait
z run summarize-file --input file=notes.md --file ./notes.md --wait   # --file uploads workspace inputs
z ask "summarize SEC-12 for the weekly report" @ticket-manager -c jira
z -p "triage critical findings for product X" --playbook weekly-vuln-triage --input product=X \
  --output-format text|json|stream-json                              # headless; exit code = mission result
z missions list --status running,waiting [--mine] [--json]
z missions show <mission> · z missions watch <mission> · z missions replay <mission>
z missions start|pause|resume|cancel <mission> [--reason …] · z missions guide <mission> "focus on prod"
z pause|resume|stop <mission>                                      # aliases
z logs <mission|job> [-f] [--json]

# jobs and artifacts
z jobs show <job> · z jobs retry <job> · z jobs artifacts <job> · z jobs download <artifact> [-o dir]

# review
z review                                   # one by one, in a terminal
z review list [--kind writes|questions] [--mission …] [--json]
z review show <action|question>            # preview, runs as, reversible, judge
z approve <action> [--comment …] [--yes]   # destructive: --confirm-destructive <operation id>
z reject <action> --reason "duplicate of SEC-12"
z review edit <action> [--set summary="…"] [--set labels:='["a"]'] [--payload-file f.json]  # else $EDITOR
z answer <question> "Skip it, the pin is intentional"

# catalog
z agents list|show|versions · z skills list|show|versions · z playbooks list|show|run
z connectors list|show|operations · z connectors approve-exposure jira --as-proposed [--expose …] [--hide …]
z connectors set-operation jira.issues.create --hide · z connectors test <name>

# audit and administration (docs/12)
z audit list --actor alice --since 7d · z audit export --since 30d --format json > audit.jsonl · z audit verify
z admin users list [--role admin] · z admin users show alice · z admin users suspend|reactivate|revoke-sessions alice
z admin roles list|show|permissions|who-can actions:approve|bindings --user alice
z admin roles grant alice approver --team "Cyber Ops" --expires 30d · z admin roles revoke alice approver --team "Cyber Ops"
z admin groups list · z admin groups test --user alice [--file mapping.yaml] · z admin groups dry-run|apply mapping.yaml
z admin sessions list|revoke [--user alice] · z admin tokens list|revoke <id> · z admin teams list|show|create|update
z admin audit search --actor alice --since 7d · z admin audit verify

# yours
z tokens list|create|revoke · z sessions list|revoke
z usage --by agent --since 7d · z inference status
```

`--input key=value` is typed from the playbook's input schema (integers, booleans, comma lists);
`key:=<json>` passes raw JSON.

`--json` prints the API's JSON with the contract's field names. Errors go to stderr; with `--json` they are
one JSON object `{"error", "exit_code", "status", "problem"}` on stderr.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (the mission succeeded) |
| 1 | failure: the mission failed, was cancelled or timed out; the server refused the request (conflict, separation of duties, not found); server or network error |
| 2 | usage error: bad arguments, invalid input (the server's 422), a confirmation needed without a terminal |
| 3 | needs attention: the mission waits for an approval, an answer or its plan (headless mode stops there and prints where to decide) |
| 4 | auth: not signed in, session expired or revoked, permission missing, step-up required |
| 5 | budget exceeded |
| 130 | interrupted (Ctrl+C) |

## Behavior worth knowing

- **Nothing is faked.** When the server does not serve an endpoint of the contract yet (a 404/405 on the route
  itself), `z` says "… is not available on this server yet" and exits 1. Today that covers: `POST
  /missions/draft-plan` (free-form planning without an `@agent`), `GET /sessions` (`z -c`, `z --resume`,
  `/resume`), `GET /stream` (`z top` falls back to per-mission streams), `GET /inference/health`, `GET /usage`,
  `POST /connectors/{id}/test`, `POST /missions/{id}/take-over`.
- **Step-up**: admin writes, exposure review, audit export and destructive approvals need a fresh
  re-authentication. Z offers step-up to browser sessions only today, so from the CLI they end with exit 4
  and a pointer to the web app until the server offers a CLI step-up.
- **Contracts**: every request body is validated with the z-common models before it is sent, and every
  response and event against the operation's model (extra keys from a newer server are tolerated).
- Missions started from the session use the session's autonomy (`propose` or `read_only`); the strictest of
  agent, playbook and mission wins on the server. `/model z-heavy` overrides the model of `@agent` missions
  started from the session (it builds a one-step plan with that model); playbooks keep theirs.

## Development

```bash
uv run ruff check cli && uv run ruff format --check cli && uv run mypy cli/src
uv run pytest cli/tests            # needs Docker (a throwaway pgvector Postgres) or Z_TEST_DATABASE_URL
```

The tests run the real z-server app under uvicorn on a free port (real HTTP and SSE) against the fake GitLab
OIDC provider (`tests/fakes/fake_gitlab_oidc`), play the agent side with the scripted runner of
`tests/support/missions.py`, drive `z` with Typer's `CliRunner` and the Textual apps with `run_test()` pilots.
