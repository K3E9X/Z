# 12 — Identity, access control, administration and audit

Z is a team tool. Everyone signs in with their **GitLab** account (OIDC), gets permissions from their **GitLab groups**, and every action is recorded in a tamper-evident audit log. Admins manage it all from an **Admin** area in the web app (mockups `design/mockups/SignIn.html`, `design/mockups/Admin.html`) and from `z admin` in the CLI.

All of this is **v1.0 (P0)**.

## 1. Sign-in with GitLab (OIDC)

### GitLab side (one-time, by a GitLab admin or group owner)
1. Create an OAuth application: instance-wide (Admin → Applications) or owned by the top-level group.
   - Name `Z`, **confidential**, scopes **`openid profile email`** (add `read_user` only if you want avatar and state checks through the API).
   - Redirect URIs: `https://<z-host>/api/v1/auth/callback` (web) and, for the CLI fallback, the exact loopback URI the CLI uses (fixed port, `http://127.0.0.1:8765/callback`).
   - Trusted (skip the consent screen) if you want.
2. Create the groups that drive roles, for example under `cyber/z/`: `z-admins`, `z-builders`, `z-approvers`, `z-operators`, `z-auditors`. Team groups (e.g. `cyber/soc-csirt`, `cyber/archi`) map to Z teams.
3. Put the client id and secret in Z's secrets (`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`), issuer `https://<gitlab-host>`.

### Z side
- **Web**: authorization code + **PKCE**, `state` and `nonce` checked, ID token validated (issuer, audience, signature from GitLab's JWKS, expiry, nonce).
- **Claims** (GitLab behavior, verify on your version):
  - ID token: `sub`, `preferred_username`, `name`, `email`, `email_verified`, `groups_direct` (paths of direct memberships).
  - Userinfo (`/oauth/userinfo`): adds `groups` (paths, direct **and inherited**) and `https://gitlab.org/claims/groups/{owner,maintainer,developer}` (group *names*, direct only).
  - Z calls **userinfo at every login** and maps roles and teams from `groups` **paths** (never from names).
- Identity key: `issuer + sub`. Email is display only.
- **CLI**: `z login` uses the **OAuth device authorization grant** (`/oauth/authorize_device`, GA in GitLab 17.9); on older GitLab it falls back to authorization code + PKCE with a loopback redirect. The CLI exchanges the GitLab token for a **Z session token** at `POST /api/v1/auth/cli/exchange`; the GitLab token is not kept.
- **MFA**: enforced by GitLab (require 2FA for the groups). Z shows whether the session came from a 2FA-enforced group but does not re-implement MFA.

### Local accounts (first connection, tests, emergencies)
GitLab stays the only identity for normal use. Three kinds of local account exist for the cases where GitLab cannot be used yet or at all. All are off by default, use argon2id password hashes, are rate-limited and locked after 5 failures, show a permanent banner while signed in, and appear in the audit log as `local:<name>` with IP and user agent.

| Account | Purpose | Allowed where | Can do | Ends |
|---|---|---|---|---|
| **Bootstrap admin** (`z-bootstrap`) | First connection after install, before the GitLab app and group mapping exist | Any environment, once | Only the Admin → Sign-in and Group mapping tabs, plus `test login`. Cannot start missions, approve writes, or see connector credentials | Disabled automatically the first time a user mapped to `admin` signs in with GitLab, or after 24 h. Re-enable only from the server: `z-server bootstrap --reset`. The one-time password is never written to logs on Kubernetes: it is written to a memory-backed file of the z-server pod (`Z_BOOTSTRAP_SECRET_FILE`, read once with `kubectl exec … cat`, see docs/11; the break-glass password and TOTP likewise, `Z_BREAKGLASS_SECRET_FILE`); in Compose it is printed once at first start (`docker compose logs`, on the host) |
| **Generic test accounts** (e.g. `tester`, `tester-approver`) | Manual tests, demos, CI smoke tests | Only when `Z_ENV` is not `production`; the server refuses to start in production if any is configured | The role set in config (default `operator`; at most `approver`). Never `admin`, never a second approval on destructive actions, never `user`-scope credentials | Expire after `expires_days` (default 30); listed and revocable in Admin → Sessions and tokens |
| **Break-glass admin** | GitLab is down and an admin must act (revoke, stop missions, change a connector) | Production, with `Z_BREAKGLASS=true` | Full admin, step-up = TOTP | One-time password printed at startup; every use raises a banner for all admins and an alert to the notification channels |

Generic accounts are shared by nature, so they never act on real systems in production and the audit log always records the IP and device. For staging tests on real tools, prefer per-person GitLab test users in a dedicated GitLab group (`cyber/z/z-testers`).

Automated tests do not use passwords at all: they sign in through `tests/fakes/fake_gitlab_oidc` with scripted users and groups, or with `auth.mode: dev` in local development.

### Sessions
- Server-side sessions in Postgres; browser cookie `__Host-z_session` (`Secure`, `HttpOnly`, `SameSite=Lax`), rotated at login and privilege change.
- Lifetimes: idle 2 h, absolute 12 h (configurable). CLI tokens: 30 days, revocable, bound to the device name.
- **Step-up re-authentication** (OIDC `prompt=login` + `max_age=300`) before: approving a destructive action, changing roles or group mappings, changing connectors, credentials, providers, routing policy or guardrails, exporting the full audit log.
- **CSRF**: double-submit token on every state-changing request from the browser; CORS disabled except the configured UI origin.
- Logout ends the Z session (and optionally redirects to GitLab's logout).

### Lifecycle (joiners, movers, leavers)
- **Just-in-time provisioning** at first login, role = what the group mapping gives, or `viewer` if nothing matches (configurable: `deny` to refuse users with no mapped group).
- **Re-evaluation** of groups at every login and every hour for active sessions (userinfo with the stored GitLab refresh token, held encrypted by the server).
- If GitLab says the user is blocked, removed, or no longer in any allowed group: sessions and CLI tokens are revoked within the hour, running missions they started keep running but cannot request new approvals from them, and their personal connections are disabled.
- Personal API tokens (for scripts): scoped (`missions:read`, `missions:write`, `review:approve`…), expiring (max 90 days), shown once, stored hashed, listed and revocable in Admin.

## 2. RBAC

### Model
- **Permission** = `resource:action` (e.g. `missions:start`, `actions:approve`, `connectors:manage`).
- **Role** = named set of permissions. Built-in roles below; admins can create custom roles.
- **RoleBinding** = user or GitLab group → role, **scoped** to `org` or to a **team**.
- Effective permissions = union of bindings from group mappings (source `gitlab`) and manual bindings (source `manual`, set by an admin, audited, optional expiry).
- **Deny by default.** Every API route declares the permission it needs; a test fails if a route has none.
- Resource ownership: missions, agents, playbooks, objectives belong to a team. Team-scoped roles act only on their team's resources; org-scoped roles act everywhere.

### Built-in roles
| Permission | viewer | operator | approver | builder | auditor | admin |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| See missions, agents, playbooks, usage | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Start, pause, guide, cancel missions; answer questions | | ✓ | ✓ | ✓ | | ✓ |
| Approve / reject writes (missions of their team) | | | ✓ | | | ✓ |
| Approve destructive actions (second approver) | | | ✓ | | | ✓ |
| Create / edit agents, skills, playbooks, objectives; apply learning proposals | | | | ✓ | | ✓ |
| Read the full audit log, export it | | | | | ✓ | ✓ |
| Manage connectors, exposure, credentials, providers, routing, guardrails, budgets, channels | | | | | | ✓ |
| Manage users, roles, group mappings, sessions, API tokens | | | | | | ✓ |

Rules on top of the matrix:
- **Separation of duties**: with `four_eyes: true` on a playbook, the mission starter cannot approve its writes. Destructive actions need two distinct approvers. An admin cannot approve their own role change (a second admin must).
- **Approver scope**: `actions:approve` is checked on the **mission's team** (the team the mission was started for): a team-scoped approver approves only actions of their team's missions; an org-scoped approver approves everywhere. Connectors have no team in the data model, so there is no per-connector approver scope (ADR 0012). If connectors get team attachments later, the check becomes the intersection (not built yet).
- Everyone can see their own sessions, tokens and personal connections.

### Default group mapping (`config/z.yaml` → `auth.group_mapping`)
```yaml
auth:
  provider: gitlab
  issuer_url: ${OIDC_ISSUER_URL}          # https://gitlab.example.internal
  client_id: ${OIDC_CLIENT_ID}
  scopes: [openid, profile, email]
  groups_claim: groups                    # from userinfo; paths, inherited included
  allowed_groups: [cyber]                 # users outside these groups cannot sign in
  no_match: viewer                        # viewer | deny
  group_mapping:
    - { group: cyber/z/z-admins,    role: admin,    scope: org }
    - { group: cyber/z/z-auditors,  role: auditor,  scope: org }
    - { group: cyber/z/z-builders,  role: builder,  scope: org }
    - { group: cyber/z/z-approvers, role: approver, scope: team }
    - { group: cyber/z/z-operators, role: operator, scope: team }
  team_mapping:
    - { group: cyber/soc-csirt, team: Cyber Ops }
    - { group: cyber/archi,     team: Architecture }
  session: { idle_minutes: 120, absolute_hours: 12, cli_token_days: 30 }
  step_up_max_age_s: 300
```
Mappings edited in the Admin UI are stored in the database and override the file; both are audited.

## 3. Admin area (web: Configure → Admin; CLI: `z admin`)
| Tab | What admins do |
|---|---|
| **Users** | List (name, GitLab username, teams, effective roles with their source, last sign-in, 2FA-enforced, status); open a user: bindings, sessions, tokens, personal connections (read-only; suspending revokes them), recent activity; add a manual role (with expiry), suspend, revoke all sessions |
| **Teams** | Teams, GitLab groups, members (from GitLab groups + manual), monthly team token budget. Teams own missions, agents and playbooks; connectors are org-wide and have no team (ADR 0012) |
| **Roles and permissions** | Built-in roles (read-only) and custom roles; permission matrix; "who can do X?" lookup |
| **Group mapping** | GitLab group path → role and scope, team mapping, allowed groups, no-match policy; **dry run** showing who gains or loses access before saving |
| **Sign-in** | OIDC settings (issuer, client, scopes, claim), test login, JWKS status, session lifetimes, step-up policy, local accounts (bootstrap state, test accounts, break-glass) |
| **Sessions and tokens** | Active web sessions and CLI tokens (user, device, IP, last seen), revoke; personal API tokens (scopes, expiry), revoke |
| **Audit log** | Same data as History, with admin filters (actor, action, target, result, IP), saved searches (per admin), export, retention, syslog forwarding, chain verification |
| **Platform** | Guardrails (locked ones shown as locked), budgets, providers and routing policy, retention, maintenance mode (no mission or job starts, running jobs finish, every user sees the message), version and health |

Every admin change shows a **diff before save**, requires step-up, and writes an audit event with before/after values.

CLI:
```bash
z admin users list [--role admin] [--json]
z admin users show alice
z admin roles grant alice approver --team "Cyber Ops" --expires 30d
z admin roles revoke alice approver --team "Cyber Ops"
z admin mapping list · z admin mapping test --user alice     # dry run
z admin sessions list · z admin sessions revoke --user alice
z admin tokens list · z admin tokens revoke <id>
z admin audit search --actor alice --since 7d · z admin audit verify
z admin inference-key status · z admin inference-key set · z admin inference-key check   # key read hidden or from stdin (ADR 0027)
```

## 4. Audit log
### What is recorded
| Category | Events |
|---|---|
| Authentication | login success/failure (with reason), logout, step-up, session revoked/expired, CLI token issued/revoked, API token created/used/revoked, local account login (bootstrap, test, break-glass) and lockout, break-glass used |
| Authorization | permission denied (route, permission, user), role/binding/mapping changes with before/after |
| Missions | created, plan edited, started, paused, guided (message hash), taken over, cancelled, completed |
| Actions | proposed, edited, approved (by whom, step-up yes/no), rejected (reason), applied (runs-as identity, target, result), failed |
| Tools | every gateway call (connector, operation, runs-as, status, duration; arguments redacted), denied calls, judge flags |
| Configuration | connectors, exposure, credentials (reference only), providers, routing, guardrails, budgets, channels, agents, skills, playbooks, objectives, learning applies and rollbacks |
| Data | audit exports, artifact downloads of C2+ data |

Each event: `seq, ts, actor (user id, username, or system/agent), actor_ip, user_agent, session_id, action, target_type, target_id, team, result (success/denied/error), runs_as, before?, after?, request_id, prev_hash, hash`.

### Integrity
- Append-only table: the app's database role has `INSERT` and `SELECT` only on `audit_events` (no `UPDATE`/`DELETE`); a separate maintenance role handles retention.
- Hash chain: `hash = sha256(prev_hash || canonical_json(event))`. Every hour a **checkpoint** (last seq + hash) is signed with the server key and written to the log and to the syslog forwarder, so tampering is detectable even by someone with database access. `z admin audit verify` checks the whole chain.
- Clock: UTC, from the server; events ordered by `seq`.

### Access, export, retention
- Users see their own events; `auditor` and `admin` see all. Exports require step-up and are themselves audited.
- Export: JSON lines and CSV from the UI and `GET /audit/export`; continuous forwarding as syslog (RFC 5424, TLS) or JSON over HTTPS to your log platform.
- Retention default 365 days for audit (configurable, minimum 90), independent of mission data retention.

## 5. Security checklist for the web app (ASVS level 2 target)
- Security headers: strict CSP (no inline scripts; nonces), `frame-ancestors 'none'`, HSTS, `X-Content-Type-Options`, `Referrer-Policy: same-origin`, `Permissions-Policy` minimal.
- Input validation from the contracts (Pydantic), output encoding in React (no `dangerouslySetInnerHTML` except sanitised Markdown with a strict allowlist).
- Rate limits: login and token endpoints per IP and per user; API per token.
- SSRF guard on every admin-entered URL (connectors, providers, webhooks): resolve and refuse loopback, link-local, metadata and cluster-internal ranges unless explicitly allowlisted.
- File uploads: size limits, type sniffing, stored outside the web root, served with `Content-Disposition: attachment`.
- Secrets never logged; error responses never include stack traces in production.
- Dependency, SAST, secret, container and DAST scans in CI (see `13-ci-cd.md`).

## 6. Tests (must exist)
- Every route has a declared permission (introspection test).
- Permission matrix: for each built-in role, an allowed and a denied call per permission.
- OIDC: bad issuer, bad audience, expired token, wrong nonce, replayed state → refused; group removed in fake GitLab → access lost at next re-evaluation.
- Local accounts: bootstrap admin cannot start missions or approve, and is disabled after the first GitLab admin login; server refuses to start in production with test accounts configured; a test account cannot get `admin` or approve a destructive action; lockout after 5 failures.
- Separation of duties: starter cannot approve with `four_eyes`; one approver cannot apply a destructive action; admin cannot approve own role change.
- Audit: every mutating route writes an event; chain verification detects a modified row.
- Fake GitLab OIDC provider in `tests/fakes/fake_gitlab_oidc` (discovery, JWKS, authorize, token, userinfo, device flow).
