# ADR 0015 — Personal OAuth connections and chat/mail channels

Status: accepted

## Context
docs/05 §6 and §10 describe delegated (user) access to tools such as Microsoft 365 and using Z from Teams,
Slack, Mattermost and mail. The spec leaves token storage, refresh, the identity used in each flow and the
authentication of each inbound platform to the build.

## Decision
1. **Delegated grants** use authorization code + PKCE, one flow per purpose, each with its own app
   registration (`oauth.read_app` / `oauth.write_app`; the connector validation refuses the same client id or
   secret for both, and requires secret references). The state is single use, expires after 10 minutes, is
   bound to the person who started it (a callback in someone else's browser is refused), and the PKCE
   verifier is sealed. Tokens are sealed in `credentials.ref` (AES-GCM, associated data = credential id),
   never listed on the connector (only in `/me/connections`), refreshed two minutes before expiry; a grant
   that cannot be refreshed is marked `expired` and the person is asked to connect again. Revoke deletes them.
2. **Who acts**: a step with `credentials: user` reads with the mission starter's read grant (the trigger owner
   for triggered missions); a user-scope action is applied with that person's write grant, through the same
   review. Nobody else's grant is ever used. A context variable carries the acting user for one call only.
3. **App-only OAuth** (client credentials) serves `service` flows; tokens are cached in memory until shortly
   before they expire; without configured scopes the scope is `<API origin>/.default` (Microsoft identity
   platform convention). Endpoints come from the connector (`authorize_url`, `token_url`) or `tenant_id`.
4. **Channels** authenticate each platform's way: Slack signing secret over `v0:{timestamp}:{body}` (5 min),
   Mattermost slash/outgoing-webhook token, Bot Framework JWT (OpenID metadata + JWKS, issuer
   `https://api.botframework.com`, audience = app id, `serviceUrl` claim = activity `serviceUrl`), and the Z
   HMAC for a mail relay. These routes never accept a Z session or token (`x-permission: hmac`).
5. **People**: chat users map to Z users by e-mail (Teams: looked up through the Bot Connector, `sso_email` or
   `sso_upn`) or by username (Slack/Mattermost slash commands carry no e-mail). Unknown or inactive people,
   teams outside `allowed_teams` and people without `missions:start` get a reply and nothing else.
6. **Content** is judge-scanned: blocked chat messages become a task for a human; flagged ones start with a
   note; mail always becomes a task (with attachments), never an automatic start. A message names `@agents`
   (planned like a session message) or goes to the channel's default agent.
7. **Replies**: the slash command answer (200, as Slack and Mattermost require), a Teams reply activity with the
   bot token, or Slack `chat.postMessage` when a bot token is configured. `response_url` replies only go to the
   Mattermost server or `hooks.slack.com` (or `reply_hosts`). Approval cards are not implemented: approvals
   stay in the Z app (destructive and two-approver actions always do).

## Consequences
- Contract: slash-command form bodies and the 200 `ChannelReply` (incl. the Slack `url_verification`
  challenge) were added to the channel operations.
- Approval cards in Teams/Slack (P1) can be added on top of the same identity mapping and step-up check.
