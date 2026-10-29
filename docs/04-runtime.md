# 04 — Runtime: runner, orchestrator, guardrails, judge

## 1. z-runner
`z-runner --job <id>` runs one agent on one input and exits. Same image under Docker and Kubernetes.

1. Fetch the job spec with the one-time job token: agent profile @version, skills @version, memory snippets, plan step, input, allowed tools, budget, model alias, LiteLLM virtual key, gateway URL.
2. Prepare the workspace: `none`, `git_clone` (the gateway-side clone helper fetches the repo with the read credential and streams it in; the token never enters the runner), or `files` (uploaded inputs and artifacts of earlier steps). **Staged inputs are scanned by the judge** (text files, docs, READMEs, comments over a size threshold are sampled) and the system prompt labels all workspace content as untrusted data.
3. Build the agent through `AgentBackend` and run the loop.
4. Stream events, push artifacts, checkpoint after every step.
5. Validate the output, repair if needed, complete the job.

### `AgentBackend`
```python
class AgentBackend(Protocol):
    def start(self, spec: JobSpec, sink: EventSink, tools: ToolSet) -> None: ...
    def step(self) -> StepResult: ...            # one model turn + tool calls
    def checkpoint(self) -> bytes: ...
    def restore(self, blob: bytes) -> None: ...
    def inject_message(self, text: str) -> None: ...   # "Guide the agent"
    def stop(self, reason: str) -> None: ...
```
v1 ships `OpenHandsBackend` (OpenHands Software Agent SDK: LLM configured with LiteLLM `base_url`, the job's virtual key and the alias; skills loaded from `SKILL.md`; tools = gateway MCP server + local `read`/`edit`/`terminal` when the workspace allows; security analyzer and confirmation on). Verify the SDK's current class names before coding.

### Neutral tool names
| Name | Becomes |
|---|---|
| `read`, `edit` | Workspace file tools (inside `/workspace` only) |
| `terminal` | Sandboxed shell, only with `git_clone` or `files` workspaces |
| `browser` | Lists the flows of `browser`-kind connectors attached to the mission. Flows run **gateway-side** in the `z-browser` worker (Playwright + Chromium, egress only to the target apps); the runner receives results and screenshots, never the session |
| `gateway:<connector>.<operation-glob>` | Operations listed by the gateway MCP server for this job |
| `gateway:@read` | Every exposed read operation of the connectors attached to the mission |
| `code` | Sandboxed Python (pandas, matplotlib, openpyxl, python-docx, python-pptx, reportlab) for analysis, charts and file conversion, in the workspace, no network |
| `documents` | Produce `.md`, `.docx`, `.pptx`, `.xlsx`, `.pdf`, `.csv`, Mermaid/SVG diagrams as artifacts from structured content |
| `ask_human`, `search_memory`, `report_objective` | Built-ins, implicit for every agent |
| `propose_action` | Built-in, implicit, usable only for the `writes` listed on the current step |
| `spawn_subagent` | Built-in, must be listed (Orchestrator by default) |

## 2. Orchestrator and sub-agents
- The **Orchestrator** agent drafts plans for free-form missions: it picks agents from the roster (using routing rules), splits work, decides fan-out and names the writes. The user sees and can edit the plan before start (plan mode).
- `spawn_subagent(agent, input, budget_share)` creates child jobs. The scheduler enforces the parent's remaining budget and concurrency. Results return to the parent as artifacts.
- Fan-out steps in playbooks skip the orchestrator: the scheduler expands `fanout.over` into jobs directly.
- A mission can mix agents: e.g. Vuln Analyst qualifies, Ticket Manager drafts tickets, Reviewer checks them.

## 3. Objectives: proactive agents
An objective turns an agent from "does what it is told" into "looks after something".
1. On the objective's cadence (cron or a triggering event such as a new finding), the scheduler runs an **objective check**: it evaluates the metric (a connector query, e.g. DefectDojo findings older than 48 h, or a Z query, e.g. open proposals) over the agent's assignments.
2. It records the value, updates status (`on_track` / `at_risk` / `off_track`) and emits `objective_update`.
3. If there is a gap, the agent drafts a short explanation and **proposes missions** from its `allowed_playbooks` (or a free-form plan) to close it. They appear in Review as suggestions with an estimated token cost.
4. With `mode: auto_start_read_only`, it may start missions whose plan steps have no `writes` and that fit the objective's `budget_per_period`. Anything with a write is only suggested.
5. Objective history feeds Analytics and the learning loop (repeated misses are signals).

Objectives are owned by a person (`owner_user`), can be paused, and are capped by budget so a proactive agent cannot spend without limit.

## 4. Reliability with open-weight models
GLM and Qwen are good but less reliable at tool use than frontier models. The runner compensates:
- **Strict schemas** for tool arguments; a malformed call yields a `validation_error` event and a corrective message, not a crash.
- **Output repair**: `result.json` is validated against the step's schema; errors go back to the agent up to `max_repair_attempts`, then `needs_attention`.
- **Loop detection**: same tool + same args N times, or no progress for M steps → `needs_attention`.
- **Escalation**: on repeated failure the runner may switch to `escalate_model` once, recorded in events.
- **Small toolsets**: only the operations this step needs are listed; descriptions are short and concrete.
- **Constrained decoding** when the server supports it (`response_format` / JSON schema / guided decoding); verify what inferX exposes.
- `poc/check_tool_calling.py` gates every model before it is mapped to an alias.

## 5. Guardrails (platform-wide, not optional)
| Guardrail | Rule |
|---|---|
| Workspace jail | Reads and writes only under `/workspace` |
| No network from the sandbox | The runner reaches only LiteLLM, z-server and the gateway. The **only other egress** is the egress proxy, which allows the package mirrors listed in `config/z.yaml` → `egress.mirrors` (e.g. an internal Nexus/Artifactory/Harbor) so builds and tests can fetch dependencies. Everything else (`curl` to the internet, `ssh`, `nc`…) is refused by NetworkPolicy / Docker network and the proxy, and a command filter gives clear feedback |
| No credentials | The runner environment has no connector secrets; only the job token and the LiteLLM virtual key |
| Allowed tools only | The gateway lists only operations from the agent profile ∩ playbook step ∩ connector exposure |
| Writes are proposals | Any `write` or `destructive` operation can only be called through `propose_action` |
| Budgets | Tokens, steps, wall time per job and mission; hard stop with `budget_exceeded` |
| Redaction | Secrets and tokens masked in events, artifacts and traces (pattern + known-secret matching) |
| Harness security | OpenHands security analyzer and confirmation on; a confirmation request becomes `needs_attention` (`guardrail_confirm`) |
| Data class | The mission's class only goes up. When the gateway returns a result from a C2+ connector (or the judge classifies staged input as C2+), z-server raises the class and **restricts the job's LiteLLM virtual key to internal models**. The runner cannot set or lower the class |

Every block emits a `denied` event and an audit entry.

## 6. The judge (`z-judge`)
A small, fast model behind its own alias, used by the platform (not by agents directly):
1. **Injection scan** of tool results and of staged workspace inputs before they reach the agent. Flagged content is wrapped and labelled as untrusted, the agent is told, and the result is marked `judge: flagged` in the timeline. High-confidence attacks block the result and raise `needs_attention`.
2. **Pre-action checks** on proposed actions: duplicates (e.g. same Jira summary open), policy (target project allowed, fields complete), data class of the payload vs. destination. The verdict is shown in Review.
3. **Routing**: classify a free-form mission to suggest agents and a playbook.
4. **Data classification** of content before any call to an external provider (see 06).
5. **Eval grading** with rubrics (see 08).

The judge is advisory except where this spec says it blocks. It is evaluated like any other model.

## 7. Interacting with a running agent
- **Guide the agent**: a message from the user is injected at the next step.
- **Pause / resume**: stop after the current step, keep the checkpoint.
- **Take over**: pause, open the workspace in a web terminal or editor (read-only by default), let the user edit, then resume. v1 is a web editor over the workspace rebuilt from the staged inputs and the checkpoint; edits restart the job from the edited checkpoint (ADR 0019).
- **Answer a question**: `ask_human` blocks the job until answered in Review, the CLI or mobile.
- **Stop**: cancel with a reason (becomes a learning signal).

## 8. Images
| Image | Content |
|---|---|
| `z-server` | Python app, all entrypoints |
| `z-runner` | Python runner + OpenHands SDK + git + common build tools (configurable per pack) + document and data libraries |
| `z-browser` | Gateway-side browser worker: Playwright + Chromium, runs recorded flows, egress only to allowlisted target apps |
| `z-ui` | Static build served by an unprivileged nginx |

All images: non-root UID, read-only root filesystem, no shell where not needed for `z-server` and `z-ui`, multi-arch.
