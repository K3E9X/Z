# ADR 0010 — GitLab as the identity provider (OIDC), roles from GitLab groups

Status: accepted

## Context
Z is used by a whole team. Everyone already has a GitLab account with 2FA, and team structure already lives in GitLab groups. We do not want a second user directory or local passwords.

## Decision
Z signs users in only through GitLab as an OpenID Connect provider (authorization code + PKCE for the web, device authorization grant or loopback PKCE for the CLI). Roles and teams come from GitLab group **paths** read from the userinfo `groups` claim at every login and re-evaluated hourly, mapped through a configurable group mapping. Manual role grants exist for exceptions, are audited and can expire. A single break-glass local admin exists, disabled by default.

## Consequences
Joiners, movers and leavers are handled in GitLab. Z depends on GitLab being available for new logins (existing sessions keep working until they expire). If the organisation later moves to another IdP (Keycloak, Entra ID), only the provider adapter and the claim mapping change; the RBAC model stays.
