"""Source of contracts/openapi.yaml. Edit THIS file, then run:

    uv run python contracts/tools/build_openapi.py contracts/openapi.yaml

A test checks that openapi.yaml is up to date with this script.
"""

import sys

import yaml

C = "common.schema.json#/$defs/"


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def ext(path):
    return {"$ref": path}


def arr(s):
    return {"type": "array", "items": s}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
N = {"type": "number"}
TS = ext(C + "Timestamp")
ID = ext(C + "Id")


def nul(s):
    return {"oneOf": [s, {"type": "null"}]}


def obj(props, req=(), extra=False, desc=None):
    o = {"type": "object", "properties": props}
    if req:
        o["required"] = list(req)
    if not extra:
        o["additionalProperties"] = False
    if desc:
        o["description"] = desc
    return o


def page(item):
    return obj({"items": arr(item), "next_cursor": nul(S)}, ["items", "next_cursor"])


schemas = {
    # ---------- identity ----------
    "EffectiveRole": obj(
        {
            "role": ext(C + "RoleName"),
            "scope": ext(C + "Scope"),
            "source": ext(C + "BindingSource"),
            "via": nul(S),
            "expires_at": nul(TS),
        },
        ["role", "scope", "source"],
        desc="via = GitLab group path for source gitlab.",
    ),
    "TeamRef": obj({"id": ID, "name": S}, ["id", "name"]),
    "User": obj(
        {
            "id": ID,
            "issuer": S,
            "sub": S,
            "username": S,
            "name": S,
            "email": S,
            "avatar_url": nul(S),
            "teams": arr(ref("TeamRef")),
            "status": ext(C + "UserStatus"),
            "last_login_at": nul(TS),
            "mfa_enforced": B,
            "local_kind": nul({"type": "string", "enum": ["bootstrap", "test", "breakglass", "dev"]}),
            "effective_roles": arr(ref("EffectiveRole")),
            "created_at": TS,
        },
        ["id", "username", "name", "status", "teams", "effective_roles"],
    ),
    "Me": obj(
        {
            "user": ref("User"),
            "permissions": arr(
                obj({"permission": ext(C + "Permission"), "scope": ext(C + "Scope")}, ["permission", "scope"])
            ),
            "session": obj(
                {"id": ID, "kind": ext(C + "SessionKind"), "expires_at": TS, "step_up_at": nul(TS)},
                ["id", "kind", "expires_at"],
            ),
            "banners": arr(
                obj(
                    {
                        "kind": {"type": "string", "enum": ["local_account", "breakglass", "maintenance", "budget"]},
                        "text": S,
                    },
                    ["kind", "text"],
                )
            ),
            "csrf_token": nul(S),
            "workspace": obj({"name": S, "environment": S}, ["name"]),
        },
        ["user", "permissions", "session", "banners", "workspace"],
    ),
    "Team": obj(
        {
            "id": ID,
            "name": S,
            "gitlab_groups": arr(S),
            "members_count": I,
            "monthly_token_budget": nul(I),
            "created_at": TS,
        },
        ["id", "name"],
    ),
    "TeamWrite": obj({"name": S, "monthly_token_budget": nul(I)}, ["name"]),
    "Role": obj(
        {
            "id": ID,
            "name": ext(C + "RoleName"),
            "builtin": B,
            "description": S,
            "permissions": arr(ext(C + "Permission")),
        },
        ["id", "name", "builtin", "permissions"],
    ),
    "RoleWrite": obj(
        {"name": ext(C + "RoleName"), "description": S, "permissions": arr(ext(C + "Permission"))},
        ["name", "permissions"],
    ),
    "PermissionInfo": obj(
        {"permission": ext(C + "Permission"), "description": S, "roles": arr(ext(C + "RoleName"))},
        ["permission", "description", "roles"],
    ),
    "RoleBinding": obj(
        {
            "id": ID,
            "subject": ext(C + "Subject"),
            "role": ext(C + "RoleName"),
            "scope": ext(C + "Scope"),
            "source": ext(C + "BindingSource"),
            "expires_at": nul(TS),
            "created_by": nul(S),
            "created_at": TS,
        },
        ["id", "subject", "role", "scope", "source", "created_at"],
    ),
    "RoleBindingWrite": obj(
        {
            "subject": ext(C + "Subject"),
            "role": ext(C + "RoleName"),
            "scope": ext(C + "Scope"),
            "expires_at": nul(TS),
            "reason": S,
        },
        ["subject", "role", "scope"],
    ),
    "GroupMapping": obj(
        {
            "id": ID,
            "group_path": S,
            "role": nul(ext(C + "RoleName")),
            "scope": nul({"type": "string", "enum": ["org", "team"]}),
            "team": nul(S),
            "source": ext(C + "MappingSource"),
        },
        ["id", "group_path", "source"],
    ),
    "GroupMappingConfig": obj(
        {
            "allowed_groups": arr(S),
            "no_match": {"type": "string", "enum": ["viewer", "deny"]},
            "group_mapping": arr(
                obj(
                    {"group": S, "role": ext(C + "RoleName"), "scope": {"type": "string", "enum": ["org", "team"]}},
                    ["group", "role", "scope"],
                )
            ),
            "team_mapping": arr(obj({"group": S, "team": S}, ["group", "team"])),
        },
        ["allowed_groups", "no_match", "group_mapping", "team_mapping"],
    ),
    "AccessChange": obj(
        {
            "user": S,
            "role": nul(S),
            "scope": nul(S),
            "team": nul(S),
            "change": {"type": "string", "enum": ["gain", "loss"]},
        },
        ["user", "change"],
    ),
    "GroupMappingDryRun": obj(
        {"changes": arr(ref("AccessChange")), "users_evaluated": I, "denied_users": arr(S)},
        ["changes", "users_evaluated"],
    ),
    "WhoCan": obj(
        {
            "permission": ext(C + "Permission"),
            "users": arr(obj({"user": S, "scope": ext(C + "Scope"), "via": S}, ["user", "scope", "via"])),
        },
        ["permission", "users"],
    ),
    "AuthSession": obj(
        {
            "id": ID,
            "user": S,
            "kind": ext(C + "SessionKind"),
            "device": nul(S),
            "ip": nul(S),
            "user_agent": nul(S),
            "created_at": TS,
            "last_seen_at": TS,
            "expires_at": TS,
            "step_up_at": nul(TS),
            "revoked_at": nul(TS),
            "current": B,
        },
        ["id", "user", "kind", "created_at", "last_seen_at", "expires_at"],
    ),
    "ApiToken": obj(
        {
            "id": ID,
            "user": S,
            "name": S,
            "scopes": arr(ext(C + "Permission")),
            "expires_at": TS,
            "last_used_at": nul(TS),
            "revoked_at": nul(TS),
            "created_at": TS,
            "token": nul({"type": "string", "description": "Only returned once, at creation."}),
        },
        ["id", "user", "name", "scopes", "expires_at", "created_at"],
    ),
    "ApiTokenCreate": obj(
        {
            "name": S,
            "scopes": arr(ext(C + "Permission")),
            "expires_in_days": {"type": "integer", "minimum": 1, "maximum": 90},
        },
        ["name", "scopes", "expires_in_days"],
    ),
    "AuthSettings": obj(
        {
            "mode": {"type": "string", "enum": ["oidc", "dev"]},
            "issuer_url": S,
            "client_id": S,
            "client_secret": {
                "type": "string",
                "writeOnly": True,
                "description": "Write-only, never returned; stored sealed.",
            },
            "cli_client_id": S,
            "scopes": arr(S),
            "groups_claim": S,
            "session": obj({"idle_minutes": I, "absolute_hours": I, "cli_token_days": I}),
            "step_up_max_age_s": I,
            "jwks": obj({"ok": B, "keys": I, "fetched_at": nul(TS), "error": nul(S)}, ["ok"]),
            "local_accounts": obj(
                {
                    "bootstrap": obj(
                        {
                            "enabled": B,
                            "state": {"type": "string", "enum": ["active", "disabled", "expired"]},
                            "expires_at": nul(TS),
                        },
                        ["enabled", "state"],
                    ),
                    "test_accounts": arr(
                        obj({"name": S, "role": S, "team": nul(S), "expires_at": nul(TS)}, ["name", "role"])
                    ),
                    "breakglass": obj({"enabled": B}, ["enabled"]),
                }
            ),
        },
        ["mode", "issuer_url", "client_id", "scopes", "groups_claim"],
    ),
    "AuthSettingsTest": obj({"ok": B, "discovery": B, "jwks": B, "detail": S}, ["ok"]),
    "CliAuthConfig": obj(
        {
            "mode": {"type": "string", "enum": ["oidc", "dev"]},
            "issuer_url": S,
            "client_id": S,
            "scopes": arr(S),
            "device_flow": B,
            "loopback_redirect": S,
        },
        ["mode", "issuer_url", "client_id", "scopes", "device_flow", "loopback_redirect"],
    ),
    "CliExchangeRequest": obj(
        {"gitlab_access_token": S, "id_token": S, "device_name": S, "dev": B},
        ["device_name"],
        desc="Send the GitLab access token (and ID token when available) obtained by the device flow or loopback PKCE; the GitLab token is not kept. dev=true only in auth.mode dev.",
    ),
    "CliToken": obj({"token": S, "expires_at": TS, "user": ref("User")}, ["token", "expires_at", "user"]),
    "LocalLoginRequest": obj({"username": S, "password": S, "totp": S}, ["username", "password"]),
    "StepUpResponse": obj({"redirect_url": nul(S), "step_up_at": nul(TS)}),
    "LoginRedirect": obj({"redirect_url": S}, ["redirect_url"]),
    # ---------- agents, skills, playbooks ----------
    "AgentServerFields": obj(
        {
            "id": ID,
            "owner_team": nul(S),
            "visibility": {"type": "string", "enum": ["team", "org"]},
            "status": {"type": "string", "enum": ["working", "available", "disabled"]},
            "pack": nul(S),
            "current_missions": arr(S),
            "evals_summary": nul(obj({"passed": I, "total": I, "model": S, "run_id": S}, ["passed", "total"])),
            "created_at": TS,
            "updated_at": TS,
        },
        ["id", "status", "created_at", "updated_at"],
        extra=True,
    ),
    "Agent": {
        "allOf": [ext("agent.schema.json#/$defs/AgentProfile"), ref("AgentServerFields")],
        "unevaluatedProperties": False,
        "required": [
            "name",
            "display_name",
            "handle",
            "version",
            "role",
            "model",
            "skills",
            "tools",
            "autonomy",
            "limits",
            "id",
            "status",
            "created_at",
            "updated_at",
        ],
        "description": "Agent profile (agent.schema.json) plus server fields.",
    },
    "AgentCreate": {
        "description": "A full profile, or {template, display_name, handle} to start from a template.",
        "oneOf": [
            ext("agent.schema.json"),
            obj(
                {
                    "template": ext(C + "Slug"),
                    "display_name": S,
                    "handle": ext(C + "Handle"),
                    "avatar": ext(C + "Avatar"),
                    "owner_team": S,
                },
                ["template", "display_name", "handle"],
            ),
        ],
    },
    "AgentPatch": {
        "allOf": [ext("agent.schema.json#/$defs/AgentProfile")],
        "properties": {"change_note": S, "owner_team": S},
        "unevaluatedProperties": False,
        "description": "Any subset of the profile. name never changes; a rename changes display_name/handle and keeps history. A new version is created for every change except rename-only.",
    },
    "AgentDuplicate": obj(
        {"display_name": S, "handle": ext(C + "Handle"), "avatar": ext(C + "Avatar")}, ["display_name", "handle"]
    ),
    "AgentVersion": obj(
        {
            "version": ext(C + "SemVer"),
            "created_at": TS,
            "created_by": nul(S),
            "change_note": nul(S),
            "profile": ext("agent.schema.json"),
        },
        ["version", "created_at", "profile"],
    ),
    "AgentDraftRequest": obj({"description": S, "team": S}, ["description"]),
    "AgentDraft": obj(
        {
            "profile": ext("agent.schema.json"),
            "questions": arr(S),
            "objectives": arr(ext("objective.schema.json")),
            "new_skills": arr(obj({"name": S, "purpose": S}, ["name", "purpose"])),
        },
        ["profile"],
    ),
    "Skill": obj(
        {
            "id": ID,
            "name": ext(C + "Slug"),
            "description": S,
            "version": ext(C + "SemVer"),
            "origin": {"type": "string", "enum": ["pack", "learned", "user"]},
            "pack": nul(S),
            "path": nul(S),
            "checksum": S,
            "evals": nul(S),
            "owner": nul(S),
            "agents": arr(S),
            "content": nul({"type": "string", "description": "SKILL.md text (detail view only)."}),
            "last_change": nul(
                obj(
                    {
                        "kind": {"type": "string", "enum": ["created", "edited", "learned", "rolled_back"]},
                        "at": TS,
                        "by": nul(S),
                        "proposal_id": nul(S),
                    },
                    ["kind", "at"],
                )
            ),
            "pending_proposal_id": nul(S),
        },
        ["id", "name", "description", "version", "origin", "checksum", "agents"],
    ),
    "SkillVersion": obj(
        {
            "version": ext(C + "SemVer"),
            "checksum": S,
            "created_at": TS,
            "created_by": nul(S),
            "origin": S,
            "proposal_id": nul(S),
        },
        ["version", "checksum", "created_at", "origin"],
    ),
    "SkillRollback": obj({"to_version": ext(C + "SemVer"), "reason": S}, ["to_version"]),
    "PlaybookServerFields": obj(
        {
            "id": ID,
            "pack": nul(S),
            "owner_team": nul(S),
            "usage_count": I,
            "last_run_at": nul(TS),
            "created_at": TS,
            "updated_at": TS,
        },
        ["id", "usage_count", "created_at", "updated_at"],
        extra=True,
    ),
    "Playbook": {
        "allOf": [ext("playbook.schema.json#/$defs/PlaybookProps"), ref("PlaybookServerFields")],
        "unevaluatedProperties": False,
        "required": [
            "name",
            "version",
            "description",
            "team",
            "steps",
            "id",
            "usage_count",
            "created_at",
            "updated_at",
        ],
        "description": "Playbook manifest plus server fields.",
    },
    "PlaybookRun": obj(
        {
            "inputs": {"type": "object"},
            "title": S,
            "data_class": ext(C + "DataClass"),
            "plan_first": B,
            "budget": ext(C + "MissionBudget"),
            "session_id": nul(S),
        },
        ["inputs"],
    ),
    # ---------- missions ----------
    "JobCounts": obj(
        dict.fromkeys(["total", "queued", "running", "needs_attention", "succeeded", "failed", "cancelled"], I)
    ),
    "Mission": obj(
        {
            "id": ID,
            "title": S,
            "prompt": nul(S),
            "playbook_id": nul(S),
            "playbook": nul(S),
            "playbook_version": nul(S),
            "plan": nul(ext("mission-plan.schema.json")),
            "inputs": {"type": "object"},
            "connectors": arr(ext(C + "Slug")),
            "data_class": ext(C + "DataClass"),
            "autonomy": ext(C + "Autonomy"),
            "plan_first": B,
            "status": ext(C + "MissionStatus"),
            "waiting_reason": nul(ext(C + "WaitingReason")),
            "created_by": S,
            "team": nul(S),
            "session_id": nul(S),
            "trigger_id": nul(S),
            "budget": ext(C + "MissionBudget"),
            "tokens_in": I,
            "tokens_out": I,
            "jobs": ref("JobCounts"),
            "pending_actions": I,
            "pending_questions": I,
            "created_at": TS,
            "started_at": nul(TS),
            "ended_at": nul(TS),
            "error": nul(S),
        },
        [
            "id",
            "title",
            "inputs",
            "connectors",
            "data_class",
            "autonomy",
            "plan_first",
            "status",
            "created_by",
            "budget",
            "tokens_in",
            "tokens_out",
            "created_at",
        ],
    ),
    "MissionCreate": obj(
        {
            "prompt": S,
            "playbook": {"type": "string", "description": "Playbook name or id."},
            "inputs": {"type": "object"},
            "title": S,
            "agents": arr({"type": "string", "description": "Handles mentioned with @ (without @)."}),
            "connectors": arr(ext(C + "Slug")),
            "plan": ext("mission-plan.schema.json"),
            "data_class": ext(C + "DataClass"),
            "autonomy": ext(C + "Autonomy"),
            "plan_first": B,
            "budget": ext(C + "MissionBudget"),
            "session_id": nul(S),
            "start": {"type": "boolean", "default": True},
        },
        desc="Either prompt (free-form, planned by the Orchestrator or with the given plan) or playbook + inputs.",
    ),
    "DraftPlanRequest": obj({"prompt": S, "agents": arr(S), "connectors": arr(ext(C + "Slug"))}, ["prompt"]),
    "DraftPlan": obj(
        {
            "plan": ext("mission-plan.schema.json"),
            "suggested_playbook": nul(S),
            "parsed": obj({"agents": arr(S), "connectors": arr(S), "writes": arr(S), "sources": arr(S)}),
            "estimated_tokens": nul(I),
        },
        ["plan"],
    ),
    "PlanPatch": obj({"plan": ext("mission-plan.schema.json")}, ["plan"]),
    "MissionControl": obj({"reason": S}),
    "GuideMessage": obj(
        {"text": {"type": "string", "minLength": 1, "maxLength": 8000}, "job_id": nul(S)},
        ["text"],
        desc="Injected at the next step of the job (or every running job of the mission).",
    ),
    "TakeOver": obj(
        {"job_id": S, "mode": {"type": "string", "enum": ["read_only", "read_write"], "default": "read_only"}},
        ["job_id"],
    ),
    "TakeOverSession": obj(
        {"job_id": S, "terminal_url": S, "expires_at": TS, "mode": S}, ["job_id", "terminal_url", "expires_at", "mode"]
    ),
    "SaveAsPlaybook": obj({"name": ext(C + "Slug"), "description": S, "domain": S}, ["name"]),
    "Job": obj(
        {
            "id": ID,
            "mission_id": ID,
            "step_id": S,
            "agent": ext(C + "Slug"),
            "agent_version": ext(C + "SemVer"),
            "parent_job_id": nul(S),
            "item_key": nul(S),
            "input": {"type": "object"},
            "status": ext(C + "JobStatus"),
            "needs_attention_reason": nul(ext(C + "NeedsAttentionReason")),
            "attempt": I,
            "resume_from_step": nul(I),
            "steps_done": I,
            "model": ext(C + "ModelAlias"),
            "tokens_in": I,
            "tokens_out": I,
            "created_at": TS,
            "started_at": nul(TS),
            "ended_at": nul(TS),
            "error": nul(obj({"class": S, "message": S}, ["class", "message"])),
            "output": nul({"type": "object"}),
        },
        [
            "id",
            "mission_id",
            "step_id",
            "agent",
            "agent_version",
            "input",
            "status",
            "attempt",
            "steps_done",
            "model",
            "tokens_in",
            "tokens_out",
            "created_at",
        ],
    ),
    "Artifact": obj(
        {
            "id": ID,
            "job_id": nul(S),
            "mission_id": ID,
            "kind": ext(C + "ArtifactKind"),
            "name": S,
            "content_type": S,
            "size": I,
            "sha256": S,
            "data_class": ext(C + "DataClass"),
            "created_at": TS,
        },
        ["id", "mission_id", "kind", "name", "content_type", "size", "sha256", "data_class", "created_at"],
    ),
    "Event": ext("events.schema.json"),
    "Replay": obj(
        {"mission": ref("Mission"), "events": arr(ext("events.schema.json")), "artifacts": arr(ref("Artifact"))},
        ["mission", "events", "artifacts"],
    ),
    # ---------- review ----------
    "Approval": obj({"by": S, "at": TS, "step_up": B, "comment": nul(S)}, ["by", "at", "step_up"]),
    "JudgeCheck": obj({"name": S, "ok": B, "detail": S}, ["name", "ok"]),
    "ProposedAction": obj(
        {
            "id": ID,
            "job_id": ID,
            "mission_id": ID,
            "agent": ext(C + "Slug"),
            "connector": ext(C + "Slug"),
            "operation": ext(C + "OperationId"),
            "class": {"type": "string", "enum": ["write", "destructive"]},
            "summary": S,
            "payload": {"type": "object"},
            "preview": ext("action.schema.json#/$defs/Preview"),
            "reversible": B,
            "judge_verdict": obj(
                {"verdict": {"type": "string", "enum": ["ok", "warn", "block"]}, "checks": arr(ref("JudgeCheck"))},
                ["verdict", "checks"],
            ),
            "runs_as": ext(C + "RunsAs"),
            "status": ext(C + "ActionStatus"),
            "approvals": arr(ref("Approval")),
            "required_approvals": {"type": "integer", "minimum": 1},
            "four_eyes": B,
            "result": nul({"type": "object"}),
            "idempotency_key": S,
            "superseded_by": nul(S),
            "edited_from": nul(S),
            "created_at": TS,
            "updated_at": TS,
        },
        [
            "id",
            "job_id",
            "mission_id",
            "agent",
            "connector",
            "operation",
            "class",
            "summary",
            "payload",
            "preview",
            "reversible",
            "runs_as",
            "status",
            "approvals",
            "required_approvals",
            "idempotency_key",
            "created_at",
        ],
    ),
    "Question": obj(
        {
            "id": ID,
            "job_id": ID,
            "mission_id": ID,
            "agent": ext(C + "Slug"),
            "text": S,
            "options": arr(S),
            "answer": nul(S),
            "answered_by": nul(S),
            "answered_at": nul(TS),
            "created_at": TS,
        },
        ["id", "job_id", "mission_id", "agent", "text", "options", "created_at"],
    ),
    "MissionSuggestion": obj(
        {
            "id": ID,
            "objective_id": nul(S),
            "agent": ext(C + "Slug"),
            "title": S,
            "explanation": S,
            "playbook": nul(S),
            "inputs": {"type": "object"},
            "estimated_tokens": nul(I),
            "has_writes": B,
            "created_at": TS,
        },
        ["id", "agent", "title", "explanation", "inputs", "has_writes", "created_at"],
    ),
    "ReviewItem": obj(
        {
            "kind": {"type": "string", "enum": ["action", "question", "suggestion"]},
            "action": ref("ProposedAction"),
            "question": ref("Question"),
            "suggestion": ref("MissionSuggestion"),
            "mission_title": S,
            "age_s": I,
        },
        ["kind", "mission_title", "age_s"],
    ),
    "ReviewList": obj(
        {
            "items": arr(ref("ReviewItem")),
            "counts": obj(
                {"all": I, "writes": I, "questions": I, "suggestions": I}, ["all", "writes", "questions", "suggestions"]
            ),
            "next_cursor": nul(S),
        },
        ["items", "counts", "next_cursor"],
    ),
    "ApproveRequest": obj(
        {
            "comment": S,
            "exclude": arr(
                {"type": "string", "description": "Item keys of a batch preview to exclude (e.g. a duplicate)."}
            ),
        }
    ),
    "RejectRequest": obj({"reason": {"type": "string", "minLength": 1}}, ["reason"]),
    "EditRequest": obj(
        {"payload": {"type": "object"}, "comment": S},
        ["payload"],
        desc="Supersedes the action with an edited copy (new id, status pending); the diff becomes an action_edited learning signal.",
    ),
    "BatchApprove": obj({"action_ids": arr(ID), "comment": S}, ["action_ids"]),
    "BatchResult": obj(
        {
            "results": arr(
                obj({"action_id": S, "ok": B, "status": S, "error": nul(ref("Problem"))}, ["action_id", "ok"])
            )
        },
        ["results"],
    ),
    "AnswerRequest": obj({"answer": {"type": "string", "minLength": 1}}, ["answer"]),
    # ---------- objectives ----------
    "Objective": {
        "allOf": [ext("objective.schema.json")],
        "properties": {
            "id": ID,
            "status": ext(C + "ObjectiveStatus"),
            "last_value": nul(N),
            "last_checked_at": nul(TS),
            "trend": arr(N),
            "owner_user": S,
        },
        "required": ["id", "status"],
    },
    "ObjectivePatch": obj(
        {
            "title": S,
            "metric": ext("objective.schema.json#/$defs/Metric"),
            "cadence": {},
            "allowed_playbooks": arr(S),
            "mode": ext(C + "ObjectiveMode"),
            "budget_per_period": {"type": "object"},
            "paused": B,
        }
    ),
    "ObjectiveCheck": obj(
        {
            "ts": TS,
            "value": N,
            "target": N,
            "status": ext(C + "ObjectiveStatus"),
            "explanation": nul(S),
            "suggestions": arr(S),
            "started_missions": arr(S),
        },
        ["ts", "value", "target", "status"],
    ),
    "Assignment": obj(
        {
            "id": ID,
            "agent": ext(C + "Slug"),
            "object_kind": ext(C + "AssignmentKind"),
            "object_ref": S,
            "role": {"type": "string", "enum": ["owner", "watcher"]},
            "since": TS,
        },
        ["id", "agent", "object_kind", "object_ref", "role", "since"],
    ),
    "AssignmentCreate": obj(
        {
            "object_kind": ext(C + "AssignmentKind"),
            "object_ref": S,
            "role": {"type": "string", "enum": ["owner", "watcher"]},
        },
        ["object_kind", "object_ref"],
    ),
    # ---------- tasks, triggers, sessions ----------
    "Task": obj(
        {
            "id": ID,
            "source": ext(C + "TaskSource"),
            "external_ref": nul(S),
            "title": S,
            "body_ref": nul(S),
            "suggested_agent": nul(S),
            "suggested_playbook": nul(S),
            "status": ext(C + "TaskStatus"),
            "mission_id": nul(S),
            "created_at": TS,
        },
        ["id", "source", "title", "status", "created_at"],
    ),
    "TaskPatch": obj(
        {
            "action": {"type": "string", "enum": ["start", "dismiss", "schedule"]},
            "playbook": S,
            "agent": S,
            "inputs": {"type": "object"},
            "reason": S,
        },
        ["action"],
    ),
    "Trigger": obj(
        {
            "id": ID,
            "kind": ext(C + "TriggerKind"),
            "name": S,
            "config": {"type": "object"},
            "filter": {"type": "object"},
            "playbook": nul(S),
            "inputs": {"type": "object"},
            "owner_user": S,
            "auto_start": B,
            "enabled": B,
            "last_fired_at": nul(TS),
            "webhook_url": nul(S),
            "secret": nul({"type": "string", "description": "HMAC secret, returned once at creation."}),
            "created_at": TS,
        },
        ["id", "kind", "config", "filter", "inputs", "owner_user", "auto_start", "enabled", "created_at"],
    ),
    "TriggerCreate": obj(
        {
            "kind": ext(C + "TriggerKind"),
            "name": S,
            "config": {"type": "object"},
            "filter": {"type": "object"},
            "playbook": S,
            "inputs": {"type": "object"},
            "auto_start": B,
            "enabled": B,
        },
        ["kind"],
    ),
    "WebhookAccepted": obj({"task_id": nul(S), "mission_id": nul(S)}),
    "Session": obj(
        {
            "id": ID,
            "user": S,
            "surface": ext(C + "Surface"),
            "title": S,
            "missions": arr(S),
            "created_at": TS,
            "updated_at": TS,
        },
        ["id", "user", "surface", "title", "missions", "created_at", "updated_at"],
    ),
    "SessionCreate": obj({"surface": ext(C + "Surface"), "title": S}, ["surface"]),
    "SessionMessage": obj(
        {
            "role": {"type": "string", "enum": ["user", "assistant", "system"]},
            "text": S,
            "ts": TS,
            "mission_id": nul(S),
        },
        ["role", "text", "ts"],
    ),
    "SessionDetail": {
        "allOf": [ref("Session")],
        "properties": {"messages": arr(ref("SessionMessage"))},
        "required": ["messages"],
    },
    "SessionMessageIn": obj(
        {
            "text": {"type": "string", "minLength": 1},
            "autonomy": ext(C + "Autonomy"),
            "plan_first": B,
            "model": ext(C + "ModelAlias"),
        },
        ["text"],
    ),
    "SessionReply": obj({"messages": arr(ref("SessionMessage")), "mission": nul(ref("Mission"))}, ["messages"]),
    # ---------- connectors ----------
    "ConnectorHealthInfo": obj(
        {
            "status": ext(C + "ConnectorHealth"),
            "latency_p50_ms": nul(I),
            "latency_p95_ms": nul(I),
            "error_rate": nul(N),
            "last_success_at": nul(TS),
            "checked_at": nul(TS),
            "detail": nul(S),
        },
        ["status"],
    ),
    "CredentialInfo": obj(
        {
            "id": ID,
            "purpose": ext(C + "CredentialPurpose"),
            "scope": ext(C + "CredentialScope"),
            "backend": S,
            "ref": S,
            "identity": nul(S),
            "scopes": arr(S),
            "rotated_at": nul(TS),
            "expires_at": nul(TS),
        },
        ["id", "purpose", "scope", "backend", "ref"],
        desc="A credential reference. The secret value is never returned.",
    ),
    "Connector": obj(
        {
            "id": ID,
            "name": ext(C + "Slug"),
            "kind": ext(C + "ConnectorKind"),
            "display_name": S,
            "base_url": nul(S),
            "data_class": ext(C + "DataClass"),
            "review_status": ext(C + "ReviewStatus"),
            "enabled": B,
            "health": ref("ConnectorHealthInfo"),
            "operations_total": I,
            "operations_exposed": I,
            "used_by": arr(S),
            "credentials": arr(ref("CredentialInfo")),
            "rate_limit": nul({"type": "object"}),
            "pack": nul(S),
            "manifest": ext("connector.schema.json"),
            "created_at": TS,
            "updated_at": TS,
        },
        [
            "id",
            "name",
            "kind",
            "display_name",
            "data_class",
            "review_status",
            "enabled",
            "health",
            "operations_total",
            "operations_exposed",
            "used_by",
            "manifest",
            "created_at",
        ],
    ),
    "ConnectorPatch": obj({"manifest": ext("connector.schema.json"), "enabled": B}),
    "ConnectorImport": obj(
        {
            "source": {"type": "string", "enum": ["openapi", "graphql", "mcp", "docs"]},
            "name": ext(C + "Slug"),
            "display_name": S,
            "base_url": S,
            "spec_url": S,
            "spec": {"type": ["object", "string"]},
            "mcp": ext("connector.schema.json#/$defs/McpServer"),
            "docs": S,
            "data_class": ext(C + "DataClass"),
        },
        ["source", "name"],
    ),
    "ConnectorDraft": obj(
        {"manifest": ext("connector.schema.json"), "operations": arr(ref("Operation")), "warnings": arr(S)},
        ["manifest", "operations"],
    ),
    "ConnectorTestRequest": obj({"operation": S, "params": {"type": "object"}, "credential_id": S}),
    "ConnectorTestResult": obj(
        {"ok": B, "latency_ms": I, "status_code": nul(I), "detail": S, "sample": nul({})},
        ["ok", "latency_ms", "detail"],
    ),
    "Operation": obj(
        {
            "id": ext(C + "OperationId"),
            "connector": ext(C + "Slug"),
            "source_ref": S,
            "description": S,
            "class": ext(C + "OperationClass"),
            "classifier_class": ext(C + "OperationClass"),
            "exposed": B,
            "proposed_exposed": B,
            "rate_limit": nul({"type": "object"}),
            "input_schema": {"type": "object"},
            "reviewed_by": nul(S),
            "reviewed_at": nul(TS),
        },
        ["id", "connector", "source_ref", "class", "classifier_class", "exposed", "proposed_exposed", "input_schema"],
    ),
    "OperationPatch": obj(
        {"class": ext(C + "OperationClass"), "exposed": B, "rate_limit": {"type": "object"}},
        desc="class can only be raised above classifier_class, never lowered.",
    ),
    "ApproveExposure": obj(
        {"operations": arr(obj({"id": ext(C + "OperationId"), "exposed": B}, ["id", "exposed"]))}, ["operations"]
    ),
    "BrowserRecording": obj(
        {"connector": ext(C + "Slug"), "start_url": S, "name": S}, ["connector", "start_url", "name"]
    ),
    "BrowserRecordingSession": obj({"id": ID, "viewer_url": S, "expires_at": TS}, ["id", "viewer_url", "expires_at"]),
    "PersonalConnection": obj(
        {
            "id": ID,
            "connector": ext(C + "Slug"),
            "status": {"type": "string", "enum": ["active", "expired", "revoked", "disabled"]},
            "read_scopes": arr(S),
            "write_scopes": arr(S),
            "last_used_at": nul(TS),
            "created_at": TS,
        },
        ["id", "connector", "status", "read_scopes", "write_scopes", "created_at"],
    ),
    "OAuthStart": obj({"purpose": ext(C + "CredentialPurpose")}, ["purpose"]),
    "Channel": obj(
        {
            "id": ID,
            "kind": ext(C + "ChannelKind"),
            "name": S,
            "config": {"type": "object"},
            "default_agent": ext(C + "Slug"),
            "allowed_teams": arr(S),
            "identity_mapping": {"type": "string", "enum": ["sso_email", "sso_upn"]},
            "enabled": B,
            "created_at": TS,
        },
        ["id", "kind", "config", "default_agent", "allowed_teams", "enabled", "created_at"],
    ),
    "ChannelCreate": obj(
        {
            "kind": ext(C + "ChannelKind"),
            "name": S,
            "config": {"type": "object"},
            "default_agent": ext(C + "Slug"),
            "allowed_teams": arr(S),
            "enabled": B,
        },
        ["kind", "default_agent"],
    ),
    # ---------- inference, usage ----------
    "InferenceModel": obj(
        {
            "alias": ext(C + "ModelAlias"),
            "provider": S,
            "kind": {"type": "string", "enum": ["internal", "external"]},
            "status": {"type": "string", "enum": ["healthy", "degraded", "down", "unknown"]},
            "listed": B,
            "replicas_ready": nul(I),
            "replicas_desired": nul(I),
            "requests_running": nul(N),
            "requests_waiting": nul(N),
            "ttft_p95_ms": nul(N),
            "tokens_per_s": nul(N),
            "kv_cache_usage": nul(N),
            "context": nul(I),
            "tool_call_ok": nul(B),
            "tool_call_parse_error_rate": nul(N),
            "last_probe_at": nul(TS),
        },
        ["alias", "provider", "kind", "status", "listed"],
    ),
    "InferenceNode": obj({"name": S, "gpu_util": nul(N), "gpu_mem_used_ratio": nul(N), "gpu_temp_c": nul(N)}, ["name"]),
    "InferenceHealth": obj(
        {
            "status": {"type": "string", "enum": ["healthy", "degraded", "down", "unknown"]},
            "gateway": obj({"ok": B, "latency_ms": nul(I), "error": nul(S)}, ["ok"]),
            "prometheus_configured": B,
            "models": arr(ref("InferenceModel")),
            "nodes": arr(ref("InferenceNode")),
            "prefix_cache_hit_rate": nul(N),
            "preemptions_24h": nul(N),
            "tokens_today": I,
            "checked_at": TS,
        },
        ["status", "gateway", "prometheus_configured", "models", "nodes", "tokens_today", "checked_at"],
    ),
    "UsageBucket": obj(
        {"ts": TS, "tokens_in": I, "tokens_out": I, "cached_tokens": I, "requests": I},
        ["ts", "tokens_in", "tokens_out", "cached_tokens", "requests"],
    ),
    "UsageSeries": obj(
        {
            "key": S,
            "label": S,
            "buckets": arr(ref("UsageBucket")),
            "tokens_in": I,
            "tokens_out": I,
            "cached_tokens": I,
            "requests": I,
            "cost": nul(N),
            "cap": nul(I),
        },
        ["key", "buckets", "tokens_in", "tokens_out", "cached_tokens", "requests"],
    ),
    "Usage": obj(
        {
            "group_by": {"type": "string", "enum": ["model", "agent", "mission", "team", "user", "playbook"]},
            "bucket": {"type": "string", "enum": ["minute", "hour", "day"]},
            "from": TS,
            "to": TS,
            "series": arr(ref("UsageSeries")),
        },
        ["group_by", "bucket", "from", "to", "series"],
    ),
    "Budget": obj(
        {
            "id": ID,
            "scope": {"type": "string", "pattern": "^(org|(team|user|playbook|mission|agent|objective):.+)$"},
            "limit": {"type": "integer", "minimum": 1},
            "period": ext(C + "BudgetPeriod"),
            "alert_at": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "used": I,
            "hard": B,
        },
        ["id", "scope", "limit", "period", "alert_at", "used", "hard"],
    ),
    "BudgetWrite": obj(
        {
            "scope": S,
            "limit": {"type": "integer", "minimum": 1},
            "period": ext(C + "BudgetPeriod"),
            "alert_at": N,
            "hard": B,
        },
        ["scope", "limit", "period"],
    ),
    "Provider": obj(
        {
            "id": ID,
            "name": S,
            "kind": {"type": "string", "enum": ["internal", "external"]},
            "aliases": arr(ext(C + "ModelAlias")),
            "data_classes": arr(ext(C + "DataClass")),
            "enabled": B,
        },
        ["id", "name", "kind", "aliases", "data_classes", "enabled"],
    ),
    "ProviderWrite": obj(
        {
            "name": S,
            "kind": {"type": "string", "enum": ["internal", "external"]},
            "aliases": arr(ext(C + "ModelAlias")),
            "data_classes": arr(ext(C + "DataClass")),
            "enabled": B,
        },
        ["name", "kind", "aliases", "data_classes", "enabled"],
    ),
    "RoutingRule": obj(
        {
            "id": ID,
            "match": {"type": "object", "description": "e.g. {finding_kind: dependency}"},
            "target": obj({"agent": nul(S), "playbook": nul(S)}),
            "origin": {"type": "string", "enum": ["config", "learned"]},
            "version": I,
        },
        ["id", "match", "target", "origin", "version"],
    ),
    "RoutingPolicy": obj(
        {
            "data_classes": {"type": "object", "additionalProperties": S},
            "default_mission_class": ext(C + "DataClass"),
            "external_fallback_when_degraded": B,
            "never_external": arr(ext(C + "DataClass")),
            "never_in_memory": arr(ext(C + "DataClass")),
            "rules": arr(ref("RoutingRule")),
        },
        ["default_mission_class", "external_fallback_when_degraded", "never_external", "never_in_memory"],
    ),
    # ---------- learning, evals ----------
    "Signal": obj(
        {
            "id": ID,
            "kind": ext(C + "SignalKind"),
            "mission_id": nul(S),
            "job_id": nul(S),
            "agent": nul(S),
            "skill": nul(S),
            "payload": {"type": "object"},
            "ts": TS,
        },
        ["id", "kind", "payload", "ts"],
    ),
    "EvalScores": obj(
        {"checks_passed": I, "checks_total": I, "cases_passed": I, "cases_total": I, "rubric_avg": nul(N), "tokens": I},
        ["checks_passed", "checks_total", "cases_passed", "cases_total", "tokens"],
    ),
    "Proposal": obj(
        {
            "id": ID,
            "kind": ext(C + "ProposalKind"),
            "target": S,
            "title": S,
            "diff": S,
            "rationale": S,
            "evidence": arr(S),
            "eval_before": nul(ref("EvalScores")),
            "eval_after": nul(ref("EvalScores")),
            "regressions": arr(S),
            "cost_delta": nul(N),
            "shadow": nul(obj({"missions": I, "matched": I})),
            "status": ext(C + "ProposalStatus"),
            "created_at": TS,
            "decided_by": nul(S),
            "decided_at": nul(TS),
        },
        ["id", "kind", "target", "title", "diff", "evidence", "regressions", "status", "created_at"],
    ),
    "ProposalDecision": obj({"reason": S}),
    "EvalRunRequest": obj(
        {
            "target": {"type": "string", "pattern": "^(agent|skill):[a-z0-9-]+$"},
            "models": arr(ext(C + "ModelAlias")),
            "version": ext(C + "SemVer"),
            "cases": arr(S),
        },
        ["target"],
    ),
    "EvalCaseResult": obj(
        {
            "id": S,
            "passed": B,
            "checks": arr(obj({"type": S, "ok": B, "detail": S}, ["type", "ok"])),
            "rubric": arr(
                obj(
                    {"item": S, "score": {"type": "integer", "minimum": 0, "maximum": 2}, "justification": S},
                    ["item", "score"],
                )
            ),
            "job_id": nul(S),
        },
        ["id", "passed", "checks", "rubric"],
    ),
    "EvalRun": obj(
        {
            "id": ID,
            "target": S,
            "target_version": S,
            "model": ext(C + "ModelAlias"),
            "status": {"type": "string", "enum": ["queued", "running", "succeeded", "failed"]},
            "scores": nul(ref("EvalScores")),
            "passed": nul(B),
            "cases": arr(ref("EvalCaseResult")),
            "created_at": TS,
            "ended_at": nul(TS),
        },
        ["id", "target", "target_version", "model", "status", "cases", "created_at"],
    ),
    # ---------- audit, admin ----------
    "AuditEvent": obj(
        {
            "seq": I,
            "ts": TS,
            "actor": S,
            "actor_ip": nul(S),
            "user_agent": nul(S),
            "session_id": nul(S),
            "action": S,
            "target_type": S,
            "target_id": nul(S),
            "team": nul(S),
            "result": {"type": "string", "enum": ["success", "denied", "error"]},
            "runs_as": nul(S),
            "before": nul({}),
            "after": nul({}),
            "request_id": nul(S),
            "prev_hash": S,
            "hash": S,
        },
        ["seq", "ts", "actor", "action", "target_type", "result", "prev_hash", "hash"],
    ),
    "AuditCheckpoint": obj(
        {"seq": I, "hash": S, "signature": S, "ts": TS, "key_id": S}, ["seq", "hash", "signature", "ts"]
    ),
    "AuditVerify": obj(
        {"ok": B, "checked": I, "first_bad_seq": nul(I), "checkpoints_verified": I, "detail": S},
        ["ok", "checked", "checkpoints_verified"],
    ),
    "AuditSettings": obj(
        {"retention_days": {"type": "integer", "minimum": 90}, "checkpoint_minutes": I, "forward": {"type": "object"}},
        ["retention_days", "checkpoint_minutes", "forward"],
    ),
    "Guardrail": obj({"value": {}, "locked": B, "description": S}, ["value", "locked"]),
    "Guardrails": {
        "type": "object",
        "additionalProperties": ref("Guardrail"),
        "description": "Keyed by guardrail name (config guardrails). Locked guardrails cannot be changed through the API.",
    },
    "UserPatch": obj({"action": {"type": "string", "enum": ["suspend", "reactivate"]}, "reason": S}, ["action"]),
    "UserDetail": {
        "allOf": [ref("User")],
        "properties": {
            "bindings": arr(ref("RoleBinding")),
            "sessions": arr(ref("AuthSession")),
            "tokens": arr(ref("ApiToken")),
            "connections": arr(ref("PersonalConnection")),
            "recent_activity": arr(ref("AuditEvent")),
        },
        "required": ["bindings", "sessions", "tokens", "connections", "recent_activity"],
    },
    "Analytics": obj(
        {
            "from": TS,
            "to": TS,
            "hours_saved": arr(
                obj(
                    {"playbook": S, "completed": I, "baseline_minutes": I, "hours": N},
                    ["playbook", "completed", "baseline_minutes", "hours"],
                )
            ),
            "approval_rate": nul(N),
            "edit_rate": nul(N),
            "failures_by_connector": arr(obj({"connector": S, "failures": I}, ["connector", "failures"])),
            "tokens_by_team": arr(obj({"team": S, "tokens": I, "cost": nul(N)}, ["team", "tokens"])),
            "missions_by_status": {"type": "object", "additionalProperties": I},
        },
        ["from", "to", "hours_saved", "failures_by_connector", "tokens_by_team", "missions_by_status"],
    ),
    "StreamMessage": obj(
        {
            "type": {
                "type": "string",
                "enum": [
                    "review_counts",
                    "mission_status",
                    "job_status",
                    "inference_health",
                    "connector_health",
                    "budget_alert",
                    "banner",
                    "heartbeat",
                ],
            },
            "data": {"type": "object"},
            "ts": TS,
        },
        ["type", "data", "ts"],
        desc="Messages of GET /stream (SSE event name = type).",
    ),
    # ---------- internal (runner) ----------
    "SkillBundle": obj(
        {
            "name": ext(C + "Slug"),
            "version": ext(C + "SemVer"),
            "content": {"type": "string", "description": "Full SKILL.md"},
            "resources": arr(obj({"path": S, "content_b64": S}, ["path", "content_b64"])),
        },
        ["name", "version", "content"],
    ),
    "MemorySnippet": obj(
        {"id": ID, "scope": {"type": "string", "enum": ["org", "team", "agent"]}, "text": S}, ["id", "scope", "text"]
    ),
    "GatewayTool": obj(
        {
            "name": {
                "type": "string",
                "pattern": "^[a-z0-9_]+(__[a-z0-9_]+)+$",
                "description": "MCP/LLM tool name = operation id with '.' replaced by '__' (OpenAI function names forbid dots).",
            },
            "operation": ext(C + "OperationId"),
            "description": S,
            "input_schema": {"type": "object"},
        },
        ["name", "operation", "description", "input_schema"],
    ),
    "WriteTarget": obj(
        {
            "operation": ext(C + "OperationId"),
            "connector": ext(C + "Slug"),
            "class": {"type": "string", "enum": ["write", "destructive"]},
            "input_schema": {"type": "object"},
            "description": S,
        },
        ["operation", "connector", "class", "input_schema"],
    ),
    "JobSpec": obj(
        {
            "job_id": ID,
            "mission_id": ID,
            "step_id": S,
            "attempt": {"type": "integer", "minimum": 1},
            "resume_from_step": nul(I),
            "agent": ext("agent.schema.json"),
            "skills": arr(ref("SkillBundle")),
            "memory": arr(ref("MemorySnippet")),
            "prompt": {"type": "string", "description": "Rendered step prompt."},
            "input": {"type": "object", "description": "Step input (fan-out item, fan-in list, mission inputs)."},
            "output": nul(
                obj(
                    {"schema": {"type": "object", "description": "Resolved JSON Schema."}, "max_repair_attempts": I},
                    ["schema", "max_repair_attempts"],
                )
            ),
            "writes": arr(ref("WriteTarget")),
            "workspace": obj({"mode": ext(C + "WorkspaceMode"), "archive_url": nul(S)}, ["mode"]),
            "tools": obj(
                {
                    "local": arr(
                        {"type": "string", "enum": ["read", "edit", "terminal", "code", "documents", "browser"]}
                    ),
                    "builtins": arr(
                        {
                            "type": "string",
                            "enum": [
                                "propose_action",
                                "ask_human",
                                "spawn_subagent",
                                "search_memory",
                                "report_objective",
                            ],
                        }
                    ),
                    "gateway": arr(ref("GatewayTool")),
                },
                ["local", "builtins", "gateway"],
            ),
            "limits": ext(C + "Limits"),
            "model": ext(C + "ModelAlias"),
            "escalate_model": nul(ext(C + "ModelAlias")),
            "llm": obj(
                {
                    "base_url": S,
                    "api_key": {
                        "type": "string",
                        "description": "Mission LiteLLM virtual key (model allowlist enforced by LiteLLM).",
                    },
                },
                ["base_url", "api_key"],
            ),
            "gateway_url": S,
            "server_url": S,
            "session_token": {
                "type": "string",
                "description": "Bearer token for every later internal and gateway call of this job; expires with the job deadline.",
            },
            "deadline": TS,
            "checkpoint_b64": nul(S),
            "objective_id": nul(S),
            "untrusted_notice": S,
        },
        [
            "job_id",
            "mission_id",
            "step_id",
            "attempt",
            "agent",
            "skills",
            "memory",
            "prompt",
            "input",
            "writes",
            "workspace",
            "tools",
            "limits",
            "model",
            "llm",
            "gateway_url",
            "server_url",
            "session_token",
            "deadline",
        ],
        desc="GET /internal/jobs/{id}/spec. The bootstrap job token in Z_JOB_TOKEN is single-use; later calls use session_token.",
    ),
    "EventBatch": obj({"events": arr(ext("events.schema.json#/$defs/EventIn"))}, ["events"]),
    "JobControl": obj(
        {
            "state": {"type": "string", "enum": ["run", "pause", "cancel"]},
            "reason": nul(S),
            "messages": arr(obj({"id": S, "text": S, "by": S}, ["id", "text"])),
            "answers": arr(
                obj({"question_id": S, "answer": S, "answered_by": S}, ["question_id", "answer", "answered_by"])
            ),
            "actions": arr(obj({"action_id": S, "status": ext(C + "ActionStatus")}, ["action_id", "status"])),
            "llm_api_key": nul(
                {"type": "string", "description": "Set when the virtual key was rotated (data class raised)."}
            ),
        },
        ["state", "messages", "answers", "actions"],
        desc="Returned by POST events/checkpoint and GET control: what the runner must do next.",
    ),
    "CheckpointIn": obj(
        {"step": {"type": "integer", "minimum": 0}, "blob_b64": S, "tokens_in": I, "tokens_out": I},
        ["step", "blob_b64"],
    ),
    "ActionIn": obj(
        {
            "connector": ext(C + "Slug"),
            "operation": ext(C + "OperationId"),
            "payload": {"type": "object"},
            "summary": S,
        },
        ["connector", "operation", "payload", "summary"],
    ),
    "QuestionIn": obj({"text": {"type": "string", "minLength": 1}, "options": arr(S)}, ["text"]),
    "MemorySearch": obj({"query": S, "top_k": {"type": "integer", "minimum": 1, "maximum": 50}}, ["query"]),
    "SubagentIn": obj(
        {
            "agent": ext(C + "Slug"),
            "input": {"type": "object"},
            "prompt": S,
            "budget_share": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        },
        ["agent", "prompt"],
    ),
    "SubagentStatus": obj(
        {"child_job_id": S, "status": ext(C + "JobStatus"), "output": nul({"type": "object"}), "error": nul(S)},
        ["child_job_id", "status"],
    ),
    "ObjectiveReport": obj(
        {
            "objective_id": S,
            "value": N,
            "explanation": S,
            "suggestions": arr(
                obj({"title": S, "playbook": nul(S), "inputs": {"type": "object"}}, ["title", "inputs"])
            ),
        },
        ["objective_id", "value", "explanation"],
    ),
    "JobComplete": obj(
        {
            "status": {
                "type": "string",
                "enum": ["succeeded", "failed", "needs_attention", "budget_exceeded", "timed_out", "cancelled"],
            },
            "output": nul({"type": "object"}),
            "report_artifact": nul(S),
            "needs_attention_reason": nul(ext(C + "NeedsAttentionReason")),
            "error": nul(obj({"class": S, "message": S}, ["class", "message"])),
            "tokens_in": I,
            "tokens_out": I,
            "steps": I,
        },
        ["status", "tokens_in", "tokens_out", "steps"],
    ),
    "WorkspaceManifest": obj(
        {
            "files": arr(
                obj(
                    {"path": S, "size": I, "sha256": S, "judge": ext(C + "JudgeVerdict")},
                    ["path", "size", "sha256", "judge"],
                )
            ),
            "flagged": arr(S),
            "repo": nul(S),
            "commit": nul(S),
        },
        ["files", "flagged"],
    ),
    "DataClassRaise": obj(
        {
            "to": ext(C + "DataClass"),
            "cause": {"type": "string", "enum": ["tool_result", "input", "judge"]},
            "detail": S,
        },
        ["to", "cause"],
    ),
    "Health": obj(
        {
            "status": {"type": "string", "enum": ["ok", "degraded", "down"]},
            "version": S,
            "checks": {"type": "object", "additionalProperties": obj({"ok": B, "detail": S}, ["ok"])},
        },
        ["status", "version"],
    ),
    "Problem": ext(C + "Problem"),
}

paths = {}


def op(
    method,
    path,
    opid,
    perm,
    owner,
    summary,
    tag,
    req=None,
    resp=None,
    status=200,
    params=(),
    step_up=False,
    mutating=None,
    sse=False,
    query=(),
    content=None,
    resp_content=None,
    desc=None,
):
    o = {"operationId": opid, "tags": [tag], "summary": summary, "x-permission": perm, "x-owner": owner}
    if mutating is None:
        mutating = method in ("post", "put", "patch", "delete")
    if mutating:
        o["x-audit"] = True
    if step_up:
        o["x-step-up"] = True
    if desc:
        o["description"] = desc
    ps = []
    for p in params:
        ps.append({"name": p, "in": "path", "required": True, "schema": {"type": "string"}})
    for q in query:
        name, sch = q if isinstance(q, tuple) else (q, {"type": "string"})
        ps.append({"name": name, "in": "query", "required": False, "schema": sch})
    if ps:
        o["parameters"] = ps
    if req is not None:
        o["requestBody"] = {"required": True, "content": content or {"application/json": {"schema": req}}}
    r = {}
    if sse:
        r["200"] = {
            "description": "Server-sent events: `id` = seq (send Last-Event-ID to resume), `event` = type, `data` = JSON.",
            "content": {"text/event-stream": {"schema": resp or {"type": "string"}}},
        }
    elif resp_content:
        r[str(status)] = {"description": "OK", "content": resp_content}
    elif resp is None:
        r["204" if status == 200 else str(status)] = {"description": "Done"}
    else:
        r[str(status)] = {"description": "OK", "content": {"application/json": {"schema": resp}}}
    r["default"] = {
        "description": "Problem details",
        "content": {"application/problem+json": {"schema": ref("Problem")}},
    }
    o["responses"] = r
    paths.setdefault(path, {})[method] = o


PG = [("cursor", {"type": "string"}), ("limit", {"type": "integer", "minimum": 1, "maximum": 200, "default": 50})]
# ---- auth
op(
    "get",
    "/auth/login",
    "authLogin",
    "public",
    "B1",
    "Start GitLab sign-in (redirect)",
    "auth",
    resp=ref("LoginRedirect"),
    query=[("redirect", {"type": "string"})],
    desc="302 to GitLab authorize with PKCE, state and nonce. Clients that cannot follow redirects get the URL as JSON with Accept: application/json.",
)
op(
    "get",
    "/auth/callback",
    "authCallback",
    "public",
    "B1",
    "OIDC callback",
    "auth",
    query=["code", "state", "error"],
    resp=None,
    status=302,
    mutating=True,
    desc="Validates state, code_verifier, ID token (iss, aud, signature via JWKS, exp, nonce), reads userinfo groups, provisions the user, maps roles and teams, opens a session, sets __Host-z_session and __Host-z_csrf cookies, redirects to the UI.",
)
op("post", "/auth/logout", "authLogout", "authenticated", "B1", "Sign out", "auth")
op(
    "post",
    "/auth/step-up",
    "authStepUp",
    "authenticated",
    "B1",
    "Start step-up re-authentication",
    "auth",
    req=obj(
        {
            "return_to": S,
            "password": {"type": "string", "description": "Local accounts: re-enter the password."},
            "totp": {"type": "string", "description": "Break-glass: current TOTP code."},
        }
    ),
    resp=ref("StepUpResponse"),
    desc="GitLab users: returns the OIDC URL (prompt=login + max_age). Local accounts: password (and TOTP for break-glass) in the body, step-up is immediate.",
)
op(
    "get",
    "/auth/cli/config",
    "authCliConfig",
    "public",
    "B1",
    "OAuth settings for the CLI",
    "auth",
    resp=ref("CliAuthConfig"),
)
op(
    "post",
    "/auth/cli/exchange",
    "authCliExchange",
    "public",
    "B1",
    "Exchange a GitLab token for a Z CLI token",
    "auth",
    req=ref("CliExchangeRequest"),
    resp=ref("CliToken"),
)
op(
    "post",
    "/auth/local/login",
    "authLocalLogin",
    "public",
    "B1",
    "Sign in with a local account (bootstrap, test, break-glass)",
    "auth",
    req=ref("LocalLoginRequest"),
    resp=ref("Me"),
    desc="Off by default; argon2id; rate-limited; locked after 5 failures; always audited.",
)
op(
    "get",
    "/me",
    "getMe",
    "authenticated",
    "B1",
    "Current user, effective permissions and session",
    "auth",
    resp=ref("Me"),
)
op(
    "get",
    "/me/sessions",
    "listMySessions",
    "authenticated",
    "B1",
    "My web sessions and CLI tokens",
    "auth",
    resp=arr(ref("AuthSession")),
)
op(
    "delete",
    "/me/sessions/{id}",
    "revokeMySession",
    "authenticated",
    "B1",
    "Revoke one of my sessions",
    "auth",
    params=["id"],
)
op("get", "/me/tokens", "listMyTokens", "authenticated", "B1", "My API tokens", "auth", resp=arr(ref("ApiToken")))
op(
    "post",
    "/me/tokens",
    "createMyToken",
    "authenticated",
    "B1",
    "Create an API token (shown once)",
    "auth",
    req=ref("ApiTokenCreate"),
    resp=ref("ApiToken"),
    status=201,
)
op("delete", "/me/tokens/{id}", "revokeMyToken", "authenticated", "B1", "Revoke an API token", "auth", params=["id"])
# ---- missions
op(
    "post",
    "/missions",
    "createMission",
    "missions:start",
    "B1",
    "Create (and by default start) a mission",
    "missions",
    req=ref("MissionCreate"),
    resp=ref("Mission"),
    status=201,
)
op(
    "post",
    "/missions/draft-plan",
    "draftPlan",
    "missions:start",
    "B1",
    "Draft a plan for a free-form request",
    "missions",
    req=ref("DraftPlanRequest"),
    resp=ref("DraftPlan"),
    mutating=False,
)
op(
    "get",
    "/missions",
    "listMissions",
    "missions:read",
    "B1",
    "List missions",
    "missions",
    resp=page(ref("Mission")),
    query=PG
    + [
        ("status", {"type": "string"}),
        ("playbook", {"type": "string"}),
        ("agent", {"type": "string"}),
        ("created_by", {"type": "string"}),
        ("q", {"type": "string"}),
    ],
)
op(
    "get",
    "/missions/{id}",
    "getMission",
    "missions:read",
    "B1",
    "Get a mission",
    "missions",
    resp=ref("Mission"),
    params=["id"],
)
op(
    "patch",
    "/missions/{id}/plan",
    "patchMissionPlan",
    "missions:start",
    "B1",
    "Edit the plan before start (plan mode)",
    "missions",
    req=ref("PlanPatch"),
    resp=ref("Mission"),
    params=["id"],
)
for act, perm in [
    ("start", "missions:start"),
    ("pause", "missions:control"),
    ("resume", "missions:control"),
    ("cancel", "missions:control"),
]:
    op(
        "post",
        f"/missions/{{id}}/{act}",
        f"{act}Mission",
        perm,
        "B1",
        f"{act.capitalize()} a mission",
        "missions",
        req=ref("MissionControl"),
        resp=ref("Mission"),
        params=["id"],
    )
op(
    "post",
    "/missions/{id}/messages",
    "guideMission",
    "missions:control",
    "B1",
    "Guide the agent",
    "missions",
    req=ref("GuideMessage"),
    params=["id"],
    status=202,
)
op(
    "post",
    "/missions/{id}/take-over",
    "takeOverMission",
    "missions:control",
    "B1",
    "Pause and open the workspace",
    "missions",
    req=ref("TakeOver"),
    resp=ref("TakeOverSession"),
    params=["id"],
)
op(
    "get",
    "/missions/{id}/events",
    "streamMissionEvents",
    "missions:read",
    "B1",
    "Mission event stream (SSE)",
    "missions",
    params=["id"],
    sse=True,
    resp=ext("events.schema.json"),
    query=[("after_seq", {"type": "integer"})],
)
op(
    "post",
    "/missions/{id}/files",
    "uploadMissionFile",
    "missions:start",
    "B1",
    "Upload an input file before start (workspace files)",
    "missions",
    params=["id"],
    resp=ref("Artifact"),
    status=201,
    content={"multipart/form-data": {"schema": obj({"file": {"type": "string", "format": "binary"}, "name": S}, ["file"])}},
    req={},
    desc="Only while the mission is draft or planned. Files land in the job workspace (mode files) as /workspace/<name>, after the judge scan of staged inputs.",
)
op(
    "post",
    "/missions/{id}/save-as-playbook",
    "saveMissionAsPlaybook",
    "playbooks:write",
    "B1",
    "Save a mission as a playbook",
    "missions",
    req=ref("SaveAsPlaybook"),
    resp=ref("Playbook"),
    params=["id"],
    status=201,
)
op(
    "get",
    "/missions/{id}/replay",
    "getMissionReplay",
    "missions:read",
    "B1",
    "Rebuild a mission timeline",
    "history",
    resp=ref("Replay"),
    params=["id"],
)
# ---- jobs
op(
    "get",
    "/missions/{id}/jobs",
    "listMissionJobs",
    "missions:read",
    "B1",
    "Jobs of a mission",
    "jobs",
    resp=page(ref("Job")),
    params=["id"],
    query=PG + [("status", {"type": "string"}), ("step", {"type": "string"})],
)
op("get", "/jobs/{id}", "getJob", "missions:read", "B1", "Get a job", "jobs", resp=ref("Job"), params=["id"])
op(
    "get",
    "/jobs/{id}/events",
    "streamJobEvents",
    "missions:read",
    "B1",
    "Job event stream (SSE)",
    "jobs",
    params=["id"],
    sse=True,
    resp=ext("events.schema.json"),
)
op(
    "get",
    "/jobs/{id}/artifacts",
    "listJobArtifacts",
    "missions:read",
    "B1",
    "Artifacts of a job",
    "jobs",
    resp=arr(ref("Artifact")),
    params=["id"],
)
op(
    "get",
    "/artifacts/{id}",
    "getArtifact",
    "missions:read",
    "B1",
    "Download an artifact",
    "jobs",
    params=["id"],
    resp_content={"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    desc="Served with Content-Disposition: attachment. Downloads of C2+ artifacts are audited. ?meta=1 returns the Artifact JSON.",
    query=[("meta", {"type": "boolean"})],
)
op(
    "post",
    "/jobs/{id}/retry",
    "retryJob",
    "missions:control",
    "B1",
    "Retry a failed or stuck job",
    "jobs",
    req=ref("MissionControl"),
    resp=ref("Job"),
    params=["id"],
)
# ---- review
op(
    "get",
    "/review",
    "listReview",
    "missions:read",
    "B1",
    "Review inbox: actions, questions, suggestions",
    "review",
    resp=ref("ReviewList"),
    query=PG
    + [
        ("kind", {"type": "string", "enum": ["all", "writes", "questions", "suggestions"]}),
        ("mission", {"type": "string"}),
    ],
)
op(
    "get",
    "/actions/{id}",
    "getAction",
    "missions:read",
    "B1",
    "Get a proposed action",
    "review",
    resp=ref("ProposedAction"),
    params=["id"],
)
op(
    "post",
    "/actions/{id}/approve",
    "approveAction",
    "actions:approve",
    "B1",
    "Approve an action",
    "review",
    req=ref("ApproveRequest"),
    resp=ref("ProposedAction"),
    params=["id"],
    desc="Destructive actions need step-up and two distinct approvers; with four_eyes the mission starter cannot approve.",
)
op(
    "post",
    "/actions/{id}/reject",
    "rejectAction",
    "actions:approve",
    "B1",
    "Reject an action",
    "review",
    req=ref("RejectRequest"),
    resp=ref("ProposedAction"),
    params=["id"],
)
op(
    "post",
    "/actions/{id}/edit",
    "editAction",
    "actions:approve",
    "B1",
    "Edit an action (supersede with a new pending copy)",
    "review",
    req=ref("EditRequest"),
    resp=ref("ProposedAction"),
    params=["id"],
)
op(
    "post",
    "/actions:batch-approve",
    "batchApproveActions",
    "actions:approve",
    "B1",
    "Approve several non-destructive actions",
    "review",
    req=ref("BatchApprove"),
    resp=ref("BatchResult"),
)
op(
    "post",
    "/questions/{id}/answer",
    "answerQuestion",
    "questions:answer",
    "B1",
    "Answer an agent question",
    "review",
    req=ref("AnswerRequest"),
    resp=ref("Question"),
    params=["id"],
)
op(
    "post",
    "/suggestions/{id}/accept",
    "acceptSuggestion",
    "missions:start",
    "B1",
    "Start a suggested mission",
    "review",
    req=obj({}),
    resp=ref("Mission"),
    params=["id"],
)
op(
    "post",
    "/suggestions/{id}/dismiss",
    "dismissSuggestion",
    "missions:start",
    "B1",
    "Dismiss a suggested mission",
    "review",
    req=ref("ProposalDecision"),
    params=["id"],
)
# ---- agents & skills
op(
    "get",
    "/agents",
    "listAgents",
    "agents:read",
    "B1",
    "List agents",
    "agents",
    resp=page(ref("Agent")),
    query=PG + [("status", {"type": "string"}), ("q", {"type": "string"})],
)
op(
    "post",
    "/agents",
    "createAgent",
    "agents:write",
    "B1",
    "Create an agent",
    "agents",
    req=ref("AgentCreate"),
    resp=ref("Agent"),
    status=201,
)
op(
    "get",
    "/agents/{id}",
    "getAgent",
    "agents:read",
    "B1",
    "Get an agent (id, name or handle)",
    "agents",
    resp=ref("Agent"),
    params=["id"],
)
op(
    "patch",
    "/agents/{id}",
    "patchAgent",
    "agents:write",
    "B1",
    "Edit or rename an agent",
    "agents",
    req=ref("AgentPatch"),
    resp=ref("Agent"),
    params=["id"],
)
op(
    "post",
    "/agents/{id}/duplicate",
    "duplicateAgent",
    "agents:write",
    "B1",
    "Duplicate an agent",
    "agents",
    req=ref("AgentDuplicate"),
    resp=ref("Agent"),
    params=["id"],
    status=201,
)
op(
    "get",
    "/agents/{id}/versions",
    "listAgentVersions",
    "agents:read",
    "B1",
    "Agent versions",
    "agents",
    resp=arr(ref("AgentVersion")),
    params=["id"],
)
op(
    "post",
    "/agents/draft",
    "draftAgent",
    "agents:write",
    "B6",
    "Agent builder: draft a profile from a description",
    "agents",
    req=ref("AgentDraftRequest"),
    resp=ref("AgentDraft"),
    mutating=False,
)
op(
    "get",
    "/skills",
    "listSkills",
    "skills:read",
    "B1",
    "List skills",
    "skills",
    resp=page(ref("Skill")),
    query=PG + [("agent", {"type": "string"}), ("origin", {"type": "string"})],
)
op(
    "get",
    "/skills/{id}",
    "getSkill",
    "skills:read",
    "B1",
    "Get a skill with its SKILL.md",
    "skills",
    resp=ref("Skill"),
    params=["id"],
)
op(
    "get",
    "/skills/{id}/versions",
    "listSkillVersions",
    "skills:read",
    "B1",
    "Skill versions",
    "skills",
    resp=arr(ref("SkillVersion")),
    params=["id"],
)
op(
    "post",
    "/skills/{id}/rollback",
    "rollbackSkill",
    "skills:write",
    "B1",
    "Roll a skill back to a previous version",
    "skills",
    req=ref("SkillRollback"),
    resp=ref("Skill"),
    params=["id"],
)
# ---- objectives
op(
    "get",
    "/agents/{id}/objectives",
    "listObjectives",
    "agents:read",
    "B1",
    "Objectives of an agent",
    "objectives",
    resp=arr(ref("Objective")),
    params=["id"],
)
op(
    "post",
    "/agents/{id}/objectives",
    "createObjective",
    "agents:write",
    "B1",
    "Give an agent an objective",
    "objectives",
    req=ext("objective.schema.json"),
    resp=ref("Objective"),
    params=["id"],
    status=201,
)
op(
    "patch",
    "/objectives/{id}",
    "patchObjective",
    "agents:write",
    "B1",
    "Edit or pause an objective",
    "objectives",
    req=ref("ObjectivePatch"),
    resp=ref("Objective"),
    params=["id"],
)
op(
    "get",
    "/objectives/{id}/history",
    "objectiveHistory",
    "agents:read",
    "B1",
    "Objective checks",
    "objectives",
    resp=arr(ref("ObjectiveCheck")),
    params=["id"],
)
op(
    "post",
    "/objectives/{id}/evaluate",
    "evaluateObjective",
    "agents:write",
    "B1",
    "Run an objective check now",
    "objectives",
    req=obj({}),
    resp=ref("ObjectiveCheck"),
    params=["id"],
)
op(
    "get",
    "/agents/{id}/assignments",
    "listAssignments",
    "agents:read",
    "B1",
    "Objects an agent looks after",
    "objectives",
    resp=arr(ref("Assignment")),
    params=["id"],
)
op(
    "post",
    "/agents/{id}/assignments",
    "createAssignment",
    "agents:write",
    "B1",
    "Assign an object to an agent",
    "objectives",
    req=ref("AssignmentCreate"),
    resp=ref("Assignment"),
    params=["id"],
    status=201,
)
# ---- playbooks
op(
    "get",
    "/playbooks",
    "listPlaybooks",
    "playbooks:read",
    "B1",
    "List playbooks",
    "playbooks",
    resp=page(ref("Playbook")),
    query=PG + [("domain", {"type": "string"}), ("q", {"type": "string"})],
)
op(
    "post",
    "/playbooks",
    "createPlaybook",
    "playbooks:write",
    "B1",
    "Create a playbook",
    "playbooks",
    req=ext("playbook.schema.json"),
    resp=ref("Playbook"),
    status=201,
)
op(
    "get",
    "/playbooks/{id}",
    "getPlaybook",
    "playbooks:read",
    "B1",
    "Get a playbook (id or name)",
    "playbooks",
    resp=ref("Playbook"),
    params=["id"],
)
op(
    "patch",
    "/playbooks/{id}",
    "patchPlaybook",
    "playbooks:write",
    "B1",
    "Edit a playbook (new version)",
    "playbooks",
    req=ext("playbook.schema.json"),
    resp=ref("Playbook"),
    params=["id"],
)
op(
    "post",
    "/playbooks/{id}/run",
    "runPlaybook",
    "missions:start",
    "B1",
    "Run a playbook",
    "playbooks",
    req=ref("PlaybookRun"),
    resp=ref("Mission"),
    params=["id"],
    status=201,
)
# ---- tasks & triggers
op(
    "get",
    "/tasks",
    "listTasks",
    "tasks:read",
    "B1",
    "Task board",
    "tasks",
    resp=page(ref("Task")),
    query=PG + [("status", {"type": "string"}), ("source", {"type": "string"})],
)
op(
    "patch",
    "/tasks/{id}",
    "patchTask",
    "tasks:write",
    "B1",
    "Start, schedule or dismiss a task",
    "tasks",
    req=ref("TaskPatch"),
    resp=ref("Task"),
    params=["id"],
)
op("get", "/triggers", "listTriggers", "triggers:read", "B1", "List triggers", "tasks", resp=arr(ref("Trigger")))
op(
    "post",
    "/triggers",
    "createTrigger",
    "triggers:write",
    "B1",
    "Create a trigger",
    "tasks",
    req=ref("TriggerCreate"),
    resp=ref("Trigger"),
    status=201,
)
op(
    "patch",
    "/triggers/{id}",
    "patchTrigger",
    "triggers:write",
    "B1",
    "Edit, enable or disable a trigger",
    "tasks",
    req=ref("TriggerCreate"),
    resp=ref("Trigger"),
    params=["id"],
)
op("delete", "/triggers/{id}", "deleteTrigger", "triggers:write", "B1", "Delete a trigger", "tasks", params=["id"])
op(
    "post",
    "/webhooks/{trigger_id}",
    "receiveWebhook",
    "hmac",
    "B1",
    "Inbound webhook (HMAC)",
    "tasks",
    req={"type": "object"},
    resp=ref("WebhookAccepted"),
    params=["trigger_id"],
    status=202,
    desc="Authenticated by X-Z-Signature: sha256=<hex HMAC over '{X-Z-Timestamp}.{body}'> (5 min skew), X-Hub-Signature-256, or X-Gitlab-Token, depending on the trigger kind.",
)
# ---- sessions
op(
    "get",
    "/sessions",
    "listSessions",
    "sessions:read",
    "B1",
    "My sessions",
    "sessions",
    resp=page(ref("Session")),
    query=PG + [("surface", {"type": "string"})],
)
op(
    "post",
    "/sessions",
    "createSession",
    "sessions:write",
    "B1",
    "Start a session",
    "sessions",
    req=ref("SessionCreate"),
    resp=ref("Session"),
    status=201,
)
op(
    "get",
    "/sessions/{id}",
    "getSession",
    "sessions:read",
    "B1",
    "A session and its transcript",
    "sessions",
    resp=ref("SessionDetail"),
    params=["id"],
)
op(
    "post",
    "/sessions/{id}/messages",
    "postSessionMessage",
    "sessions:write",
    "B1",
    "Send a message in a session",
    "sessions",
    req=ref("SessionMessageIn"),
    resp=ref("SessionReply"),
    params=["id"],
)
# ---- connectors
op(
    "get",
    "/connectors",
    "listConnectors",
    "connectors:read",
    "B3",
    "List connectors",
    "connectors",
    resp=arr(ref("Connector")),
)
op(
    "post",
    "/connectors",
    "createConnector",
    "connectors:manage",
    "B3",
    "Create a connector from a manifest",
    "connectors",
    req=ext("connector.schema.json"),
    resp=ref("Connector"),
    status=201,
    step_up=True,
)
op(
    "get",
    "/connectors/{id}",
    "getConnector",
    "connectors:read",
    "B3",
    "Get a connector",
    "connectors",
    resp=ref("Connector"),
    params=["id"],
)
op(
    "patch",
    "/connectors/{id}",
    "patchConnector",
    "connectors:manage",
    "B3",
    "Edit a connector",
    "connectors",
    req=ref("ConnectorPatch"),
    resp=ref("Connector"),
    params=["id"],
    step_up=True,
)
op(
    "post",
    "/connectors/{id}/approve-exposure",
    "approveExposure",
    "connectors:manage",
    "B3",
    "Confirm which operations agents may see",
    "connectors",
    req=ref("ApproveExposure"),
    resp=ref("Connector"),
    params=["id"],
    step_up=True,
)
op(
    "post",
    "/connectors/{id}/test",
    "testConnector",
    "connectors:manage",
    "B3",
    "Test a connector",
    "connectors",
    req=ref("ConnectorTestRequest"),
    resp=ref("ConnectorTestResult"),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/connectors/import",
    "importConnector",
    "connectors:manage",
    "B3",
    "Draft a connector from a spec, docs or MCP server",
    "connectors",
    req=ref("ConnectorImport"),
    resp=ref("ConnectorDraft"),
    mutating=False,
)
op(
    "post",
    "/connectors/record-browser",
    "recordBrowserFlow",
    "connectors:manage",
    "B3",
    "Record a browser flow",
    "connectors",
    req=ref("BrowserRecording"),
    resp=ref("BrowserRecordingSession"),
    status=201,
)
op(
    "get",
    "/connectors/{id}/operations",
    "listOperations",
    "connectors:read",
    "B3",
    "Operations of a connector",
    "connectors",
    resp=arr(ref("Operation")),
    params=["id"],
)
op(
    "patch",
    "/operations/{id}",
    "patchOperation",
    "connectors:manage",
    "B3",
    "Raise class, expose or hide an operation",
    "connectors",
    req=ref("OperationPatch"),
    resp=ref("Operation"),
    params=["id"],
    step_up=True,
)
op(
    "get",
    "/me/connections",
    "listMyConnections",
    "authenticated",
    "B3",
    "My personal (delegated) connections",
    "connectors",
    resp=arr(ref("PersonalConnection")),
)
op(
    "post",
    "/me/connections/{connector}/oauth/start",
    "startOAuth",
    "authenticated",
    "B3",
    "Start a delegated OAuth connection",
    "connectors",
    req=ref("OAuthStart"),
    resp=ref("LoginRedirect"),
    params=["connector"],
)
op(
    "get",
    "/oauth/callback",
    "oauthCallback",
    "public",
    "B3",
    "OAuth callback for personal connections",
    "connectors",
    query=["code", "state", "error"],
    status=302,
    mutating=True,
)
op(
    "delete",
    "/me/connections/{id}",
    "revokeMyConnection",
    "authenticated",
    "B3",
    "Revoke a personal connection",
    "connectors",
    params=["id"],
)
op("get", "/channels", "listChannels", "connectors:read", "B3", "List channels", "channels", resp=arr(ref("Channel")))
op(
    "post",
    "/channels",
    "createChannel",
    "connectors:manage",
    "B3",
    "Create a channel",
    "channels",
    req=ref("ChannelCreate"),
    resp=ref("Channel"),
    status=201,
    step_up=True,
)
for ch, p in [
    ("teams", "/channels/teams/messages"),
    ("slack", "/channels/slack/events"),
    ("mattermost", "/channels/mattermost/hook"),
    ("email", "/channels/email/inbound"),
]:
    op(
        "post",
        p,
        f"channel{ch.capitalize()}Inbound",
        "hmac",
        "B3",
        f"Inbound {ch} messages",
        "channels",
        req={"type": "object"},
        resp=ref("WebhookAccepted"),
        status=202,
    )
# ---- inference & usage
op(
    "get",
    "/inference/health",
    "inferenceHealth",
    "usage:read",
    "B1",
    "inferX and gateway health",
    "inference",
    resp=ref("InferenceHealth"),
)
op(
    "get",
    "/inference/models",
    "inferenceModels",
    "usage:read",
    "B1",
    "Model aliases with status",
    "inference",
    resp=arr(ref("InferenceModel")),
)
op(
    "get",
    "/inference/nodes",
    "inferenceNodes",
    "usage:read",
    "B1",
    "GPU nodes",
    "inference",
    resp=arr(ref("InferenceNode")),
)
op(
    "get",
    "/usage",
    "getUsage",
    "usage:read",
    "B1",
    "Token usage",
    "inference",
    resp=ref("Usage"),
    query=[
        ("group_by", {"type": "string", "enum": ["model", "agent", "mission", "team", "user", "playbook"]}),
        ("from", {"type": "string", "format": "date-time"}),
        ("to", {"type": "string", "format": "date-time"}),
        ("bucket", {"type": "string", "enum": ["minute", "hour", "day"]}),
        ("format", {"type": "string", "enum": ["json", "csv"]}),
    ],
)
op("get", "/budgets", "listBudgets", "usage:read", "B1", "Budgets", "inference", resp=arr(ref("Budget")))
op(
    "put",
    "/budgets",
    "putBudgets",
    "guardrails:manage",
    "B1",
    "Replace budgets",
    "inference",
    req=arr(ref("BudgetWrite")),
    resp=arr(ref("Budget")),
    step_up=True,
)
op("get", "/providers", "listProviders", "usage:read", "B1", "Model providers", "inference", resp=arr(ref("Provider")))
op(
    "post",
    "/providers",
    "createProvider",
    "providers:manage",
    "B1",
    "Add or update a provider",
    "inference",
    req=ref("ProviderWrite"),
    resp=ref("Provider"),
    status=201,
    step_up=True,
)
op(
    "get",
    "/routing-policy",
    "getRoutingPolicy",
    "usage:read",
    "B6",
    "Routing policy",
    "inference",
    resp=ref("RoutingPolicy"),
)
op(
    "put",
    "/routing-policy",
    "putRoutingPolicy",
    "providers:manage",
    "B6",
    "Replace the routing policy",
    "inference",
    req=ref("RoutingPolicy"),
    resp=ref("RoutingPolicy"),
    step_up=True,
    desc="C2 and C3 can never be routed to external providers; such a policy is refused.",
)
# ---- learning & evals
op(
    "get",
    "/signals",
    "listSignals",
    "learning:read",
    "B6",
    "Learning signals",
    "learning",
    resp=page(ref("Signal")),
    query=PG + [("agent", {"type": "string"}), ("kind", {"type": "string"})],
)
op(
    "get",
    "/proposals",
    "listProposals",
    "learning:read",
    "B6",
    "Learning proposals",
    "learning",
    resp=page(ref("Proposal")),
    query=PG + [("status", {"type": "string"})],
)
op(
    "get",
    "/proposals/{id}",
    "getProposal",
    "learning:read",
    "B6",
    "A proposal with diff and evals",
    "learning",
    resp=ref("Proposal"),
    params=["id"],
)
for act in ["apply", "reject", "try"]:
    op(
        "post",
        f"/proposals/{{id}}/{act}",
        f"{act}Proposal",
        "learning:apply",
        "B6",
        f"{act.capitalize()} a proposal",
        "learning",
        req=ref("ProposalDecision"),
        resp=(ref("Mission") if act == "try" else ref("Proposal")),
        params=["id"],
    )
op(
    "get",
    "/evals",
    "listEvalRuns",
    "learning:read",
    "B6",
    "Eval runs",
    "learning",
    resp=page(ref("EvalRun")),
    query=PG + [("target", {"type": "string"})],
)
op(
    "post",
    "/evals",
    "runEvals",
    "learning:apply",
    "B6",
    "Run an eval suite (one run per model)",
    "learning",
    req=ref("EvalRunRequest"),
    resp=arr(ref("EvalRun")),
    status=202,
)
op(
    "get",
    "/evals/{run_id}",
    "getEvalRun",
    "learning:read",
    "B6",
    "An eval run",
    "learning",
    resp=ref("EvalRun"),
    params=["run_id"],
)
# ---- history
op(
    "get",
    "/audit",
    "listAudit",
    "audit:read_own",
    "B1",
    "Audit log (own events unless audit:read)",
    "history",
    resp=page(ref("AuditEvent")),
    query=PG
    + [
        ("actor", {"type": "string"}),
        ("action", {"type": "string"}),
        ("target_type", {"type": "string"}),
        ("target_id", {"type": "string"}),
        ("result", {"type": "string"}),
        ("ip", {"type": "string"}),
        ("from", {"type": "string", "format": "date-time"}),
        ("to", {"type": "string", "format": "date-time"}),
    ],
)
op(
    "get",
    "/audit/export",
    "exportAudit",
    "audit:export",
    "B1",
    "Export the audit log",
    "history",
    step_up=True,
    mutating=True,
    query=[
        ("format", {"type": "string", "enum": ["json", "csv", "syslog"]}),
        ("from", {"type": "string", "format": "date-time"}),
        ("to", {"type": "string", "format": "date-time"}),
    ],
    resp_content={
        "application/x-ndjson": {"schema": {"type": "string"}},
        "text/csv": {"schema": {"type": "string"}},
        "text/plain": {"schema": {"type": "string"}},
    },
    desc="JSON lines, CSV or RFC 5424 lines. The export itself is audited.",
)
# ---- admin
op(
    "get",
    "/admin/users",
    "adminListUsers",
    "users:manage",
    "B1",
    "Users",
    "admin",
    resp=page(ref("User")),
    query=PG + [("role", {"type": "string"}), ("status", {"type": "string"}), ("q", {"type": "string"})],
)
op(
    "get",
    "/admin/users/{id}",
    "adminGetUser",
    "users:manage",
    "B1",
    "User detail",
    "admin",
    resp=ref("UserDetail"),
    params=["id"],
)
op(
    "patch",
    "/admin/users/{id}",
    "adminPatchUser",
    "users:manage",
    "B1",
    "Suspend or reactivate a user",
    "admin",
    req=ref("UserPatch"),
    resp=ref("User"),
    params=["id"],
    step_up=True,
)
op(
    "post",
    "/admin/users/{id}/revoke-sessions",
    "adminRevokeUserSessions",
    "users:manage",
    "B1",
    "Revoke all sessions and CLI tokens of a user",
    "admin",
    req=obj({"reason": S}),
    params=["id"],
    step_up=True,
)
op(
    "get",
    "/admin/role-bindings",
    "adminListBindings",
    "users:manage",
    "B1",
    "Role bindings",
    "admin",
    resp=arr(ref("RoleBinding")),
    query=[("subject", {"type": "string"}), ("role", {"type": "string"})],
)
op(
    "post",
    "/admin/role-bindings",
    "adminCreateBinding",
    "users:manage",
    "B1",
    "Grant a role (manual binding)",
    "admin",
    req=ref("RoleBindingWrite"),
    resp=ref("RoleBinding"),
    status=201,
    step_up=True,
    desc="An admin cannot approve their own role change: a binding granting a role to oneself is refused.",
)
op(
    "delete",
    "/admin/role-bindings/{id}",
    "adminDeleteBinding",
    "users:manage",
    "B1",
    "Revoke a manual binding",
    "admin",
    params=["id"],
    step_up=True,
)
op("get", "/admin/roles", "adminListRoles", "users:manage", "B1", "Roles", "admin", resp=arr(ref("Role")))
op(
    "post",
    "/admin/roles",
    "adminCreateRole",
    "users:manage",
    "B1",
    "Create a custom role",
    "admin",
    req=ref("RoleWrite"),
    resp=ref("Role"),
    status=201,
    step_up=True,
)
op(
    "patch",
    "/admin/roles/{id}",
    "adminPatchRole",
    "users:manage",
    "B1",
    "Edit a custom role",
    "admin",
    req=ref("RoleWrite"),
    resp=ref("Role"),
    params=["id"],
    step_up=True,
)
op(
    "get",
    "/admin/permissions",
    "adminListPermissions",
    "users:manage",
    "B1",
    "Permission catalog",
    "admin",
    resp=arr(ref("PermissionInfo")),
)
op(
    "get",
    "/admin/who-can",
    "adminWhoCan",
    "users:manage",
    "B1",
    "Who has a permission",
    "admin",
    resp=ref("WhoCan"),
    query=[("permission", {"type": "string"})],
)
op(
    "get",
    "/admin/group-mapping",
    "adminGetGroupMapping",
    "users:manage",
    "B1",
    "GitLab group mapping",
    "admin",
    resp=ref("GroupMappingConfig"),
)
op(
    "put",
    "/admin/group-mapping",
    "adminPutGroupMapping",
    "users:manage",
    "B1",
    "Replace the group mapping",
    "admin",
    req=ref("GroupMappingConfig"),
    resp=ref("GroupMappingConfig"),
    step_up=True,
)
op(
    "post",
    "/admin/group-mapping:dry-run",
    "adminDryRunGroupMapping",
    "users:manage",
    "B1",
    "Who would gain or lose access",
    "admin",
    req=ref("GroupMappingConfig"),
    resp=ref("GroupMappingDryRun"),
    mutating=False,
)
op(
    "get",
    "/admin/auth-settings",
    "adminGetAuthSettings",
    "users:manage",
    "B1",
    "Sign-in settings",
    "admin",
    resp=ref("AuthSettings"),
)
op(
    "put",
    "/admin/auth-settings",
    "adminPutAuthSettings",
    "users:manage",
    "B1",
    "Change sign-in settings",
    "admin",
    req=ref("AuthSettings"),
    resp=ref("AuthSettings"),
    step_up=True,
)
op(
    "post",
    "/admin/auth-settings:test",
    "adminTestAuthSettings",
    "users:manage",
    "B1",
    "Test OIDC discovery and JWKS",
    "admin",
    req=obj({}),
    resp=ref("AuthSettingsTest"),
    mutating=False,
)
op(
    "get",
    "/admin/sessions",
    "adminListSessions",
    "users:manage",
    "B1",
    "All sessions and CLI tokens",
    "admin",
    resp=page(ref("AuthSession")),
    query=PG + [("user", {"type": "string"})],
)
op(
    "delete",
    "/admin/sessions/{id}",
    "adminRevokeSession",
    "users:manage",
    "B1",
    "Revoke a session",
    "admin",
    params=["id"],
    step_up=True,
)
op(
    "get",
    "/admin/tokens",
    "adminListTokens",
    "users:manage",
    "B1",
    "All API tokens",
    "admin",
    resp=page(ref("ApiToken")),
    query=PG + [("user", {"type": "string"})],
)
op(
    "delete",
    "/admin/tokens/{id}",
    "adminRevokeToken",
    "users:manage",
    "B1",
    "Revoke an API token",
    "admin",
    params=["id"],
    step_up=True,
)
op("get", "/teams", "listTeams", "teams:read", "B1", "Teams", "admin", resp=arr(ref("Team")))
op(
    "post",
    "/teams",
    "createTeam",
    "teams:manage",
    "B1",
    "Create a team",
    "admin",
    req=ref("TeamWrite"),
    resp=ref("Team"),
    status=201,
    step_up=True,
)
op("get", "/teams/{id}", "getTeam", "teams:read", "B1", "A team", "admin", resp=ref("Team"), params=["id"])
op(
    "patch",
    "/teams/{id}",
    "patchTeam",
    "teams:manage",
    "B1",
    "Edit a team",
    "admin",
    req=ref("TeamWrite"),
    resp=ref("Team"),
    params=["id"],
    step_up=True,
)
op(
    "get",
    "/settings/guardrails",
    "getGuardrails",
    "guardrails:read",
    "B1",
    "Guardrails",
    "admin",
    resp=ref("Guardrails"),
)
op(
    "put",
    "/settings/guardrails",
    "putGuardrails",
    "guardrails:manage",
    "B1",
    "Change unlocked guardrails",
    "admin",
    req=ref("Guardrails"),
    resp=ref("Guardrails"),
    step_up=True,
    desc="Changing a locked guardrail is refused (409).",
)
op(
    "get",
    "/admin/audit-settings",
    "adminGetAuditSettings",
    "users:manage",
    "B1",
    "Audit retention and forwarding",
    "admin",
    resp=ref("AuditSettings"),
)
op(
    "put",
    "/admin/audit-settings",
    "adminPutAuditSettings",
    "users:manage",
    "B1",
    "Change audit retention and forwarding",
    "admin",
    req=ref("AuditSettings"),
    resp=ref("AuditSettings"),
    step_up=True,
)
op(
    "post",
    "/admin/audit:verify",
    "adminVerifyAudit",
    "audit:export",
    "B1",
    "Verify the audit hash chain and checkpoints",
    "admin",
    req=obj({}),
    resp=ref("AuditVerify"),
    mutating=False,
)
op(
    "get",
    "/analytics",
    "getAnalytics",
    "analytics:read",
    "B1",
    "Analytics",
    "admin",
    resp=ref("Analytics"),
    query=[
        ("from", {"type": "string", "format": "date-time"}),
        ("to", {"type": "string", "format": "date-time"}),
        ("team", {"type": "string"}),
    ],
)
# ---- live
op(
    "get",
    "/stream",
    "globalStream",
    "authenticated",
    "B1",
    "Live stream: review counts, statuses, health (SSE)",
    "live",
    sse=True,
    resp=ref("StreamMessage"),
)
# ---- internal
IT = "Internal (job token)"
op(
    "get",
    "/internal/jobs/{id}/spec",
    "internalJobSpec",
    "job_token",
    "B1",
    "Job spec (single-use bootstrap token)",
    "internal",
    resp=ref("JobSpec"),
    params=["id"],
    mutating=False,
)
op(
    "get",
    "/internal/jobs/{id}/workspace",
    "internalJobWorkspace",
    "job_token",
    "B3",
    "Staged workspace archive (tar.gz), judge-scanned",
    "internal",
    params=["id"],
    resp_content={"application/gzip": {"schema": {"type": "string", "format": "binary"}}},
    mutating=False,
)
op(
    "get",
    "/internal/jobs/{id}/workspace/manifest",
    "internalJobWorkspaceManifest",
    "job_token",
    "B3",
    "Workspace manifest with judge verdicts",
    "internal",
    resp=ref("WorkspaceManifest"),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/internal/jobs/{id}/events",
    "internalPostEvents",
    "job_token",
    "B1",
    "Post events (server redacts and assigns seq)",
    "internal",
    req=ref("EventBatch"),
    resp=ref("JobControl"),
    params=["id"],
    mutating=False,
)
op(
    "get",
    "/internal/jobs/{id}/control",
    "internalJobControl",
    "job_token",
    "B1",
    "What the runner must do next",
    "internal",
    resp=ref("JobControl"),
    params=["id"],
    query=[("wait_s", {"type": "integer", "minimum": 0, "maximum": 30})],
)
op(
    "post",
    "/internal/jobs/{id}/artifacts",
    "internalPostArtifact",
    "job_token",
    "B1",
    "Upload an artifact",
    "internal",
    params=["id"],
    resp=ref("Artifact"),
    status=201,
    mutating=False,
    content={
        "multipart/form-data": {
            "schema": obj(
                {"file": {"type": "string", "format": "binary"}, "kind": ext(C + "ArtifactKind"), "name": S},
                ["file", "kind", "name"],
            )
        }
    },
)
op(
    "post",
    "/internal/jobs/{id}/checkpoint",
    "internalCheckpoint",
    "job_token",
    "B1",
    "Store a checkpoint",
    "internal",
    req=ref("CheckpointIn"),
    resp=ref("JobControl"),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/internal/jobs/{id}/actions",
    "internalProposeAction",
    "job_token",
    "B1",
    "propose_action",
    "internal",
    req=ref("ActionIn"),
    resp=ref("ProposedAction"),
    params=["id"],
    status=201,
    desc="Refused (403 urn:z:problem:forbidden, denied event) unless the operation is in the current step's writes.",
)
op(
    "post",
    "/internal/jobs/{id}/questions",
    "internalAskHuman",
    "job_token",
    "B1",
    "ask_human",
    "internal",
    req=ref("QuestionIn"),
    resp=ref("Question"),
    params=["id"],
    status=201,
)
op(
    "post",
    "/internal/jobs/{id}/memory/search",
    "internalSearchMemory",
    "job_token",
    "B6",
    "search_memory",
    "internal",
    req=ref("MemorySearch"),
    resp=arr(ref("MemorySnippet")),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/internal/jobs/{id}/subagents",
    "internalSpawnSubagent",
    "job_token",
    "B1",
    "spawn_subagent",
    "internal",
    req=ref("SubagentIn"),
    resp=ref("SubagentStatus"),
    params=["id"],
    status=201,
)
op(
    "get",
    "/internal/jobs/{id}/subagents/{child_id}",
    "internalSubagentStatus",
    "job_token",
    "B1",
    "Child job status and output",
    "internal",
    resp=ref("SubagentStatus"),
    params=["id", "child_id"],
)
op(
    "post",
    "/internal/jobs/{id}/objective-report",
    "internalReportObjective",
    "job_token",
    "B1",
    "report_objective",
    "internal",
    req=ref("ObjectiveReport"),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/internal/jobs/{id}/complete",
    "internalCompleteJob",
    "job_token",
    "B1",
    "Finish the job",
    "internal",
    req=ref("JobComplete"),
    resp=ref("Job"),
    params=["id"],
    mutating=False,
)
op(
    "post",
    "/internal/jobs/{id}/data-class",
    "internalRaiseDataClass",
    "service",
    "B3",
    "Raise the mission data class (gateway -> api)",
    "internal",
    req=ref("DataClassRaise"),
    params=["id"],
    mutating=False,
)

doc = {
    "openapi": "3.1.0",
    "info": {
        "title": "Z API",
        "version": "1.0.0",
        "description": "Public and internal API of z-server. Everything under /api/v1 (see servers). JSON, cursor pagination ({items, next_cursor} with ?cursor=&limit=), errors as RFC 9457 problem details. "
        "Every operation declares x-permission (deny by default, contracts/permissions.yaml), x-owner (BUILD workstream), x-audit (writes an audit event in the same transaction) and x-step-up (needs a re-authentication within auth.step_up_max_age_s). "
        "Browser clients use the __Host-z_session cookie and send the __Host-z_csrf cookie value in X-CSRF-Token on state-changing requests; CLI and scripts use Authorization: Bearer <Z CLI or API token>. "
        "Runners use Authorization: Bearer <job token>. Out of /api/v1: GET /healthz, GET /readyz, GET /metrics (Prometheus), /mcp (Z as an MCP server: start_mission, get_mission, list_playbooks, list_review) and /gateway/mcp (per-job tool gateway, MCP streamable HTTP).",
    },
    "servers": [{"url": "/api/v1"}],
    "tags": [
        {"name": t}
        for t in [
            "auth",
            "missions",
            "jobs",
            "review",
            "agents",
            "skills",
            "objectives",
            "playbooks",
            "tasks",
            "sessions",
            "connectors",
            "channels",
            "inference",
            "learning",
            "history",
            "admin",
            "live",
            "internal",
        ]
    ],
    "security": [{"session": []}, {"bearer": []}],
    "paths": paths,
    "components": {
        "securitySchemes": {
            "session": {"type": "apiKey", "in": "cookie", "name": "__Host-z_session"},
            "bearer": {
                "type": "http",
                "scheme": "bearer",
                "description": "Z CLI token, API token, or job token (internal routes).",
            },
        },
        "schemas": schemas,
    },
}
# fix security per op
for p, ms in paths.items():
    for m, o in ms.items():
        if o["x-permission"] in ("public", "hmac"):
            o["security"] = []
        elif o["x-permission"] in ("job_token", "service"):
            o["security"] = [{"bearer": []}]


class D(yaml.SafeDumper):
    pass


def str_presenter(d, data):
    return d.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)


D.add_representer(str, str_presenter)
out = (
    "# Z API contract (OpenAPI 3.1). Frozen at tag contracts-v1; changes go through the main session only.\n"
    + yaml.dump(doc, Dumper=D, sort_keys=False, width=200, allow_unicode=True)
)
open(sys.argv[1], "w").write(out)
print(len(paths), "paths", sum(len(v) for v in paths.values()), "operations", len(schemas), "schemas")
