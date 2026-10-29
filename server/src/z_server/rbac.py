"""Deny-by-default RBAC (docs/12 §2): permissions, roles, scoped grants, GitLab group mapping."""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from z_common.contracts import permissions as load_permissions

ORG = "org"
ALL = "*"
# A broader permission implies a narrower one (checked in Principal.can).
IMPLIES: dict[str, frozenset[str]] = {"audit:read_own": frozenset({"audit:read"})}


@functools.cache
def catalog() -> dict[str, str]:
    """permission -> description."""
    return dict(load_permissions()["permissions"])


def _expand(role: str, roles: dict[str, list[str]], seen: frozenset[str] = frozenset()) -> set[str]:
    if role in seen:
        raise ValueError(f"role inheritance cycle at {role}")
    result: set[str] = set()
    for entry in roles[role]:
        if entry == ALL:
            result |= set(catalog())
        elif entry in roles:
            result |= _expand(entry, roles, seen | {role})
        else:
            result.add(entry)
    return result


@functools.cache
def builtin_roles() -> dict[str, frozenset[str]]:
    roles: dict[str, list[str]] = load_permissions()["roles"]
    return {name: frozenset(_expand(name, roles)) for name in roles}


def role_permissions(role: str, custom: Mapping[str, Iterable[str]] | None = None) -> frozenset[str]:
    if role in builtin_roles():
        return builtin_roles()[role]
    if custom and role in custom:
        return frozenset(p for p in custom[role] if p in catalog())
    return frozenset()


@dataclass(frozen=True)
class Grant:
    permission: str
    scope: str  # org | team:<id>


@dataclass(frozen=True)
class RoleAssignment:
    role: str
    scope: str
    source: str  # gitlab | manual
    via: str | None = None
    expires_at: datetime | None = None


@dataclass
class Principal:
    """Who is calling. `grants` are the effective permissions after token scopes and local-account limits."""

    user_id: str
    username: str
    kind: str  # user | local | dev | api_token
    session_id: str | None = None
    session_kind: str | None = None
    teams: frozenset[str] = frozenset()
    roles: list[RoleAssignment] = field(default_factory=list)
    grants: frozenset[Grant] = frozenset()
    step_up_at: datetime | None = None
    local_kind: str | None = None
    csrf_token: str | None = None
    via_cookie: bool = False

    def can(self, permission: str, team: str | None = None) -> bool:
        """Route-level check when team is None (any scope); resource-level check for a team-owned resource."""
        accepted = {permission} | IMPLIES.get(permission, frozenset())
        for grant in self.grants:
            if grant.permission not in accepted:
                continue
            if team is None or grant.scope == ORG or grant.scope == f"team:{team}":
                return True
        return False

    def can_on(self, permission: str, team: str | None) -> bool:
        """Resource-level check: a resource of `team`, or of no team (org-level), which only an org-wide grant
        reaches. `can(permission, None)` means "at any scope" and must never decide access to a resource."""
        accepted = {permission} | IMPLIES.get(permission, frozenset())
        return any(
            grant.permission in accepted
            and (grant.scope == ORG or (team is not None and grant.scope == f"team:{team}"))
            for grant in self.grants
        )

    def has_org(self, permission: str) -> bool:
        return Grant(permission, ORG) in self.grants

    def teams_with(self, permission: str) -> set[str] | None:
        """None means org-wide; otherwise the teams where the permission applies."""
        if self.has_org(permission):
            return None
        return {
            g.scope.removeprefix("team:")
            for g in self.grants
            if g.permission == permission and g.scope.startswith("team:")
        }


def in_allowed_groups(groups: Iterable[str], allowed: Iterable[str]) -> bool:
    allowed = list(allowed)
    if not allowed:
        return True
    return any(g == a or g.startswith(a.rstrip("/") + "/") for g in groups for a in allowed)


def map_groups(
    groups: Iterable[str], mapping: dict[str, Any], team_ids: dict[str, str]
) -> tuple[list[RoleAssignment], set[str]]:
    """Role assignments and team ids for a user's GitLab group paths (paths only, never names).

    Team-scoped mapped roles apply to each team the user belongs to.
    `team_ids` maps a team name to its id.
    """
    group_set = set(groups)
    teams = {
        team_ids[m["team"]]
        for m in mapping.get("team_mapping", [])
        if m["group"] in group_set and m["team"] in team_ids
    }
    roles: list[RoleAssignment] = []
    for m in mapping.get("group_mapping", []):
        if m["group"] not in group_set:
            continue
        if m["scope"] == "org":
            roles.append(RoleAssignment(m["role"], ORG, "gitlab", m["group"]))
        else:
            roles += [RoleAssignment(m["role"], f"team:{t}", "gitlab", m["group"]) for t in sorted(teams)]
    if not roles and mapping.get("no_match", "viewer") == "viewer":
        roles.append(RoleAssignment("viewer", ORG, "gitlab", None))
    return roles, teams


def grants_for(
    roles: Iterable[RoleAssignment], custom: Mapping[str, Iterable[str]] | None = None, now: datetime | None = None
) -> frozenset[Grant]:
    out: set[Grant] = set()
    for assignment in roles:
        if assignment.expires_at is not None and now is not None and assignment.expires_at <= now:
            continue
        out |= {Grant(p, assignment.scope) for p in role_permissions(assignment.role, custom)}
    return frozenset(out)


def restrict(grants: frozenset[Grant], allowed_permissions: Iterable[str]) -> frozenset[Grant]:
    allowed = set(allowed_permissions)
    return frozenset(g for g in grants if g.permission in allowed)
