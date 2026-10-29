# 07 — Security

Z gives language models access to real tools. Its security posture is the product, not an add-on. The invariants in `CLAUDE.md` are the summary; this is the detail.

## 1. Assets and trust boundaries
| Asset | Where |
|---|---|
| Connector credentials (read and write) | Secrets backend → gateway (read) and executor (write) only |
| Internal data pulled by agents | Runner memory, events, artifacts, Postgres |
| Approval decisions and audit trail | Postgres, exported |
| Model gateway master key | z-server only |
| Agent definitions, skills, memory | Postgres + Git (packs) |

Boundaries: user ↔ z-server (OIDC), z-server ↔ runner (job token), runner ↔ LiteLLM (virtual key), runner ↔ gateway (job token), gateway/executor ↔ tools (service accounts), LiteLLM ↔ providers.

## 2. Threats and controls
| Threat | Controls |
|---|---|
| **Prompt injection** in tickets, findings, docs, web pages, repos, uploaded files | Judge scan on every tool result and on staged workspace inputs; workspace content labelled untrusted; untrusted-content wrapping; system prompt states that tool content is data; agents cannot write without approval; allowlisted operations only |
| **Data exfiltration** by a manipulated agent | No network from the sandbox except allowlisted package mirrors through the egress proxy; gateway is the only tool path; the mission's LiteLLM key only allows internal models once the class is C2+; outputs to external tools only through approved actions; judge checks payload class vs. destination |
| **Credential theft** | Agents hold none; per-job tokens, short-lived; virtual LLM keys per mission; redaction everywhere |
| **Excessive agency** | `propose_action` for every write; two approvers for destructive; four-eyes option; budgets and step limits; no auto-approve in v1 |
| **Malicious or buggy pack / skill** | Packs cannot change guardrails or extend tools beyond the agent profile; skills are reviewed and versioned; `scripts/` in skills run only inside the sandbox |
| **Learning loop poisoning** | Proposals need evals with zero regressions and human approval; memory facts need approval; C3 content never enters memory; every change versioned with rollback |
| **Sandbox escape** | Non-root, read-only rootfs, no capabilities, seccomp RuntimeDefault, no service account token, optional gVisor RuntimeClass, one pod per job with its own emptyDir workspace, no shared volumes between jobs |
| **Lateral movement from a runner** | Default-deny NetworkPolicy; egress only to LiteLLM, z-server internal API and gateway; no Kubernetes API access |
| **Misuse of delegated (user) credentials** | Only usable in missions started by that user; scopes minimal; visible and revocable in Settings → Connections; every call shows "runs as" |
| **Spoofed approvals from chat** | Chat users mapped through SSO; chat approvals limited to non-destructive single-approver actions; Z session re-check; full audit |
| **Runaway proactive agents** | Objectives have an owner, a per-period budget, `suggest` by default; read-only auto-start only; writes always proposed |
| **Account takeover / stale access** | GitLab OIDC with 2FA enforced on the groups; PKCE, state, nonce; short sessions; step-up re-auth for sensitive actions; group re-evaluation every hour; leavers lose access within the hour |
| **Privilege escalation** | Deny-by-default RBAC with a declared permission on every route; role changes need step-up and a second admin; all bindings audited with before/after |
| **Abuse by an insider** | RBAC, approvals bound to teams, signed audit log, exports, `user`-scope budgets |
| **Malicious connector rules in a pack** | Platform classifier is a floor (rules can only raise a class); pack exposure is `pending_review` until an admin confirms each operation; globs only for read |
| **Supply chain** (cf. the March 2026 Trivy → LiteLLM compromise) | Actions pinned by SHA, scanners installed from pinned, verified binaries; lockfiles with hashes; 7-day release age gate (14 for LiteLLM); LiteLLM image pinned by digest, compromised versions blocked; SBOM per image; cosign signatures verified before deploy; minimal CI permissions; internal package mirrors |

## 3. Kubernetes and k3s hardening (chart defaults)
- Namespaces `z` (platform) and `z-runners` (jobs). Pod Security Admission `restricted` on both.
- `z-runners`: default-deny ingress and egress; allow egress to pods labelled `z.io/runner-reachable: "true"` in namespace `z` (LiteLLM, api, gateway, egress proxy; set on whichever pods run them, including the single `all` pod), and DNS. Nothing else. The egress proxy allows only `egress.mirrors`.
- Runner pods: `automountServiceAccountToken: false`, `runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `capabilities: drop [ALL]`, `seccompProfile: RuntimeDefault`, `emptyDir` for `/workspace` and `/tmp` with size limits, CPU/memory limits, `activeDeadlineSeconds`, `ttlSecondsAfterFinished`.
- Optional `runtimeClassName: gvisor` (k3s: configure containerd with `runsc`; Kubernetes: per distribution).
- The scheduler's ServiceAccount can only create, get, list and delete Jobs, get and list Pods, read their logs, and create, get, patch and delete the per-job Secrets in `z-runners` (the Secret holding a job token is owned by its Job and goes with it).
- Secrets via Kubernetes Secrets (encryption at rest enabled on the cluster) or External Secrets / Vault.
- Ingress through the cluster's controller (Traefik on k3s, ingress-nginx or Gateway API elsewhere) with TLS; OIDC in-app, or oauth2-proxy in front if preferred.

## 4. Docker Compose hardening
- Runners join a dedicated `runners` network with no route out except LiteLLM and z-server.
- The `runners` network is created with an explicit `name: z_runners` so the executor can attach to it.
- The Docker executor talks to the Docker API through a **socket proxy** that only allows container create/start/stop/inspect/logs/remove; the raw socket is never mounted into z-server.
- Same container security options as Kubernetes (`read_only`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, user, tmpfs).
- `process` executor (runner as a subprocess) exists for development only and is refused when `Z_ENV=production`.

## 5. Audit
Full specification in `12-identity-access-and-admin.md` §4 (events, append-only storage, signed checkpoints, export, retention). Summary:
- Every API mutation, approval, tool call, denied action, learning change and admin change is an `AuditEvent` with actor, runs-as identity, target and a hash chain (`hash = sha256(prev_hash || canonical_json(event))`).
- Export: `GET /audit/export` (JSON lines) and optional syslog forwarding for your existing log platform. Z does not do log analysis itself.
- Mission replay rebuilds the timeline from events and artifacts.

## 6. Data handling
- Retention per class (default: events 180 days, artifacts 90 days, C3 artifacts 7 days), configurable.
- Memory never stores C3. Memory facts show their source and approver.
- Personal data in tool outputs is redacted from traces sent to optional external observability.

## 7. Security testing in CI
- Unit tests for every guardrail and every policy branch (routing classes, approvals, exposure).
- E2E: injection string in a fake ticket must be flagged and must not change the plan; `curl` from the sandbox must fail; an agent calling a write operation directly must get `denied`; a destructive action with one approval must not apply.
- `trivy` image and config scan, `kubescape` or `kube-score` on rendered manifests, `gitleaks` on the repo.
