# ADR 0012 — Details fixed while building missions, scheduler and review

Status: accepted

## Context
Building B1 (missions, scheduler, runner protocol, review) required decisions the specification did not make.

## Decision
1. **Approver scope** (docs/12 §3 "connectors attached to their team"): the data model has no connector-to-team
   link yet, so `actions:approve` is checked on the **mission's team** (the starter's team), or org-wide. When
   connectors get team attachments, the check becomes the intersection.
2. **Step-up freshness**: a sign-in is a fresh authentication; within `auth.step_up_max_age_s` after sign-in or
   step-up, an approver may approve destructive actions without another prompt. Older sessions get
   `step-up-required`.
3. **Destructive approvals** are given one by one in the Z app: batch approve refuses them, a test account never
   gives the second approval, the same user never approves twice.
4. **Edit** creates a new pending action (`edited_from`), the old one is `superseded`; approvals never carry over.
   The patch of a merge request cannot be swapped in an edit.
5. **Judge on staged inputs**: the deterministic stage (patterns, normalization, sampling) runs on every staged
   file. `flagged` files are delivered and labelled untrusted; `blocked` files are withheld, the job becomes
   `needs_attention: judge_blocked` with a `pause` control, and a human resumes it (`POST /missions/{id}/resume`).
   The model stage (`z-judge`, B6) refines verdicts and never lowers a block.
6. **Workspace layout** (mode `files`): uploaded inputs at `/workspace/<name>`, artifacts of the steps a step
   depends on at `/workspace/steps/<step_id>/<name>`; tar entries owned by uid 10001, mode 0600. `git_clone`
   answers 503 until the gateway clone helper (B3) exists.
7. **Server-owned event types** (`status`, `plan`, `checkpoint`, `action_*`, `question*`, `data_class`, `judge`,
   `learning_signal`, `objective_update`) posted by a runner are dropped: a runner cannot forge them.
8. **Retry** of a job in a mission that failed or timed out reopens the mission with a new model key (the old one
   was deleted when it ended) and resumes from the job's last checkpoint. Cancelled, succeeded and
   budget-exceeded missions are not reopened.
9. **Runner cleanup**: the watchdog stops and removes the runner of every ended job (reaper), so containers do not
   accumulate when runners exit without the executor noticing.
10. **Runner image**: `Z_RUNNER_IMAGE` must be pinned by digest when `Z_ENV=production` (supply chain, docs/13).
11. **Approval exclusions** (`exclude` in ApproveRequest) apply to `payload.items[].id`; actions without items
    refuse them.
12. **Mission waiting reason**: `question` when a question is open, `approval` when actions are pending, none
    when a job needs attention for another reason (invalid output, judge block).

13. **Docker socket proxy**: Z ships its own `z-socket-proxy` (standard library, one file) instead of a generic
    proxy. Generic proxies filter by API family but not by body, so a compromised z-server could still create a
    privileged container mounting the host. Ours allows only create/start/stop/wait/inspect/logs/remove of
    containers labelled `io.z.job`, and refuses any create that departs from the runner hardening (allowed image,
    user 10001, runners network, read-only rootfs, all caps dropped, no-new-privileges only, tmpfs on /workspace
    and /tmp with nosuid,nodev, CPU/memory/pids limits, no mounts, devices, host namespaces or extra runtime).

14. **Connector names** use lowercase letters and digits only: the contract's `Slug` allows `-` and
    `OperationId` allows `_`, and a connector name is both a slug and the first segment of its operation ids.
15. **Gateway verdicts** travel in the MCP result `_meta` (`io.z/judge`, `io.z/artifact`, `io.z/truncated`);
    the runner copies them into its `tool_result` event. The gateway itself emits `judge`, `denied` and
    `data_class` events and one audit record per call (`gateway.call`, runs-as = the read account).
16. **Executor idempotency**: Jira issues carry a `z-<action id>` label and comments a `z` entity property;
    an action found `applying` after a crash is looked up first, re-applied only when the operation is
    idempotent, otherwise failed as "outcome unknown" for a human to check. Never a blind retry of a POST.

## Consequences
The review inbox, CLI and web follow these rules; B3 implements the clone helper and the executor that applies
approved actions; B6 adds the model stage of the judge on top of the same verdict shape.
