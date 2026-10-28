# z-runner

Runs **one** agent job and exits (docs/04 §1). Same image under Docker and Kubernetes.

```bash
Z_SERVER_URL=http://z-api:8000 Z_JOB_ID=job-123 Z_JOB_TOKEN=<single-use bootstrap token> \
  z-runner --job job-123 [--workspace /workspace] [--state-dir DIR] [--log-level INFO]
```

| Exit code | Meaning |
|---|---|
| 0 | `succeeded` |
| 1 | `failed` (agent error, model error, unsafe workspace, platform error; see `JobComplete.error.class`) |
| 2 | usage error (missing `Z_SERVER_URL`, job id or `Z_JOB_TOKEN`; `--job` ≠ `Z_JOB_ID`) |
| 3 | `needs_attention` (`invalid_output`, `loop_detected`, `guardrail_confirm`) |
| 4 | `budget_exceeded` (tokens, steps, or the mission's LiteLLM key budget) |
| 5 | `timed_out` (`JobSpec.deadline`) |
| 6 | `cancelled` |
| 10 | bootstrap failed (spec not fetched); nothing was reported |
| 11 | the completion could not be recorded (z-server unreachable); the last checkpoint is kept |
| 12 | terminated by SIGTERM after a step checkpoint, **without** completing: resume from the checkpoint |

## Flow
1. `GET /internal/jobs/{id}/spec` with the bootstrap token (removed from the environment once read). Every later
   call, internal API and tool gateway, uses `spec.session_token`.
2. Workspace (`none | files | git_clone`): when `workspace.archive_url` is set the archive is downloaded from the
   runner's own internal API (`GET .../workspace`, the token is never sent to another origin), extracted safely
   (no absolute paths, `..`, links leaving the workspace, device files or FIFOs; size and member limits; Python's
   `data` filter as a second line), then checked against `.../workspace/manifest` (size + sha256; `blocked` files
   removed, `flagged` files announced to the agent as untrusted). Skill resources go to `.z/skills/<name>/`.
3. The agent runs step by step through `AgentBackend` (below). After every step: budgets, loop detection,
   escalation, output check, checkpoint, event flush, control.
4. Completion: artifacts (`result.json`, `report.md`, `changes.patch` for `git_clone`), then `POST .../complete`.

Control (`JobControl` from event and checkpoint posts, `GET .../control?wait_s=` while blocked):
- **cancel**: stop before the next step, complete `cancelled`.
- **pause**: the contract has no `paused` completion, so the runner *parks*: after the step's checkpoint it emits
  `status` running→paused and long-polls `control` until `run` (emits paused→running and continues), `cancel`
  or the deadline (`timed_out`). No model call happens while parked.
- **messages** ("Guide the agent"): injected before the next step, once per message id (ids are checkpointed).
- **answers**: unblock `ask_human`; an answer to a question asked before a restart is injected as a message.
- **actions**: status changes of proposed actions (other than `pending`) are told to the agent.
- **llm_api_key**: the LLM key is rotated before the next model call (and added to the redactor).

## AgentBackend and the OpenHands SDK (1.56)
`AgentBackend` is exactly docs/04 §1 (`start`, `step`, `checkpoint`, `restore`, `inject_message`, `stop`);
`restore()` is called before `start()`. The optional `SupportsModelSwitch.set_llm()` handles escalation and key
rotation. `OpenHandsBackend` (`z_runner/backend/openhands/`), verified against the installed SDK sources:

- **LLM**: `LLM(model="litellm_proxy/<alias>", base_url=spec.llm.base_url, api_key=spec.llm.api_key)`. LiteLLM's
  `litellm_proxy` provider posts `{base_url}/chat/completions` with `model=<alias>` unchanged, which is what the Z
  LiteLLM gateway routes (no provider URL anywhere, invariant 1). Proven against the fake LLM
  (`test_demo_summarize_succeeds_with_valid_output`). Costs are 0 per token (internal), at most 2 SDK attempts
  (LiteLLM retries upstream), LiteLLM uses its bundled cost map (no egress at import).
- **Agent**: `Agent(system_prompt=<Z prompt>, tools=[Tool(name="z:<job>:<tool>")], include_default_tools=
  ["FinishTool", "ThinkTool"])`, no `agent_context`: no implicit skills, memory, secrets or date in the prompt.
  The system prompt (role, instructions, persona, platform rules incl. `spec.untrusted_notice`, skills from
  `SKILL.md`, then a fixed `# Known facts` block with the memory snippets) is stable for prefix caching; the task,
  input, workspace notes, writes and output schema are the first user message.
- **Tools**: each Z tool is a `ZSdkTool` (`ToolDefinition`) instance registered in the SDK registry; the model
  sees the tool's own JSON Schema (plus the SDK's optional `summary` / `security_risk`). Arguments are validated
  strictly with jsonschema; the SDK turns the error into a tool error the model sees, and the runner emits
  `validation_error` (`where: tool_args`).
- **Conversation**: `LocalConversation(persistence_dir=..., conversation_id=uuid5(job id), max_iteration_per_run=1,
  stuck_detection=True)`; `step()` = one `run()` = one SDK agent step (one model call + its tool calls). The
  SDK's "max iterations" stop is expected and filtered. `send_message()` injects task, guidance and repairs.
- **Harness security**: `EnsembleSecurityAnalyzer(LLMSecurityAnalyzer, PatternSecurityAnalyzer,
  PolicyRailSecurityAnalyzer)` with `ConfirmRisky(threshold=HIGH, confirm_unknown=False)`. A confirmation request
  emits `denied` (`guardrail_confirm`) and ends the job `needs_attention/guardrail_confirm`; a resumed job rejects
  (never runs) an action that was waiting for confirmation.
- **Untrusted workspace**: `_ZConversation` disables the SDK's ambient loading of plugins (hooks, MCP servers,
  skills) from `<workspace>/.agents|.openhands/plugins` and of file-based agents; project skills, `AGENTS.md`
  and memory files are off (`load_project_skills`/`load_memory` default false). Tested against the stock class.
- **Checkpoint / restore**: tar.gz of the conversation persistence directory (base_state.json + events, every
  text file redacted); restore extracts it before the conversation is created with the same id, so the SDK
  resumes the conversation (it keeps the finished tool calls and results, nothing is replayed).

## Tools
| Tool (model name) | Neutral kind | When |
|---|---|---|
| `read`, `edit` | read, edit | listed in `tools.local`; jailed to the workspace (symlinks resolved). Without `edit`, a step with an output still gets `edit` restricted to `result.json` |
| `terminal` | terminal | listed, and workspace `files` or `git_clone`; fresh bash per call, scrubbed environment, process group killed after each call, network commands refused with `denied` (`sandbox_network`) |
| `code` | code | listed; Python in the workspace (`-I`), network imports refused |
| `documents` | documents | listed; md, csv, json, mermaid, svg (static only), docx (python-docx), pdf (reportlab), xlsx (openpyxl or a built-in writer), pptx (python-pptx, not in the image: reported as unavailable) |
| `<connector>__<resource>__<verb>` | gateway | `tools.gateway`; MCP `tools/call` on `spec.gateway_url` with the session token; results wrapped as untrusted |
| `ask_human`, `search_memory` | builtins | always (implicit) |
| `propose_action` | propose_action | only when `writes` is non-empty; operation must be one of `writes` (else `denied`, rule `step_writes`); payload validated against `WriteTarget.input_schema` |
| `spawn_subagent` | spawn_subagent | only when listed in `tools.builtins`; waits for the child, honours cancel and the deadline |

Calling a write operation as a tool is refused with `denied` (`write_requires_proposal`), any other operation id
outside the allowlist with `denied` (`not_in_allowlist`).

## Guardrails
Workspace jail; network-block feedback (`curl`, `wget`, `nc`, `ssh`, `scp`, `telnet`, `/dev/tcp`, package
indexes/registries other than the configured mirror, git remotes, ...; real enforcement is the network policy);
allowed tools only; budgets (tokens from usage, `limits.max_steps`, `deadline`, the LiteLLM key budget); redaction
(known secrets: bootstrap and session tokens, LLM keys incl. rotated ones; patterns: GitLab, `sk-`, GitHub, Slack,
AWS keys, JWTs, `Authorization:`/`PRIVATE-TOKEN` headers, credentials in URLs, PEM blocks, `password=`...) on
events, artifacts, checkpoints, logs and on everything shown to the model; harness confirmation; loop detection
(same tool + same args 3 times in a row, no progress for 8 steps, or the SDK stuck detector); one escalation to
`escalate_model` after 3 failing steps; output repair up to `max_repair_attempts`. The process is non-dumpable
(`prctl(PR_SET_DUMPABLE, 0)`), so processes the agent starts cannot read its memory or environment.

## Events
`message`, `thought`, `tool_call`, `tool_result`, `terminal`, `file_edit`, `usage` (one per model call),
`validation_error`, `denied`, `artifact`, `question`, `subagent`, `checkpoint`, `status` (pause/resume). Every
payload is validated against the z-common models before it is buffered; batches are flushed after every step and
every 2 s in the background. `action_proposed`, `question_answered` and `judge` are z-server's.

## Tests
`uv run pytest runner/tests` — the real `OpenHandsBackend` against `tests/fakes/fake_llm` and a fake z-server
(`runner/tests/fake_internal.py`: the `/internal/jobs/...` API with z-common models, every event validated against
`contracts/events.schema.json`, every response against `openapi.yaml`) with a fake MCP gateway.

## Image
`deploy/images/z-runner/Dockerfile`: non-root (10001), read-only root filesystem with `/workspace` and `/tmp` as
writable mounts. As PID 1, `z-runner` forks the job, forwards signals and reaps orphans.

```bash
docker build --secret id=extra_ca,src=ca.pem -f deploy/images/z-runner/Dockerfile -t z-runner .
docker run --rm --read-only --tmpfs /tmp --tmpfs /workspace:uid=10001,gid=10001 --cap-drop ALL \
  --security-opt no-new-privileges:true z-runner --help
```
