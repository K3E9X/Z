# ADR 0028 — Google Workspace connectors: delegated only, Google's OAuth conventions, idempotent mails and events

Status: accepted

## Context
docs/05 §7 lists Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Chat) next to Microsoft 365. The OAuth
code (ADR 0015) followed the Microsoft identity platform, and the spec does not say how Google differs:
- Google has no `offline_access` scope (an unknown scope fails the consent): a refresh token comes with
  `access_type=offline`, and only on the first consent unless `prompt=consent`. Refreshing never returns a new
  refresh token. Google has no client credentials grant (its OpenID configuration lists authorization_code,
  refresh_token, device_code and jwt-bearer); app-only access means service accounts with domain-wide delegation.
- Gmail, Calendar and Drive are served from `https://www.googleapis.com`, Chat only from
  `https://chat.googleapis.com`; a Z connector has one base URL.
- Gmail sends a base64url RFC 2822 message (`raw`): a reviewer cannot read it, and an agent could add headers
  (Bcc, Reply-To) that the reviewer never sees.
- docs/05 §2 asks for writes that do not duplicate after a crash. Gmail has no idempotency key; Calendar ids
  chosen by the client are not guaranteed to collide-detect; Chat has `requestId`, idempotent per user.
- Operation ids are `<connector>.<operation>` with `[a-z0-9_]` segments, while connector names are slugs that may
  contain `-`: a hyphenated pack connector seeded fine but none of its operations could be approved.

Paths, parameters and scopes were checked against Google's discovery documents (gmail v1, calendar v3, drive v3,
chat v1) and `accounts.google.com/.well-known/openid-configuration`.

## Decision
1. **`oauth.provider` in the connector contract** (`microsoft` | `google`). `google` defaults the endpoints to
   Google's, sends no `offline_access`, asks `access_type=offline` with `prompt=select_account consent`, never
   `include_granted_scopes` (read and write grants must not merge), and sends no scope on the code exchange or the
   refresh. A Google connector with a `client_credentials` flow is refused (lint and API). Without `provider`:
   `microsoft` when `tenant_id` is set, as before. On both platforms Z adds `openid email`, records each grant's
   account from the ID token of the token response, and refuses a read or write grant whose account differs from
   the other one: a mission must not read one mailbox and write as another (before, `identity` stayed empty).
2. **Two connectors in the `workplace` pack**, delegated only (`credentials: user` steps):
   - `google` (`www.googleapis.com`): Gmail messages and threads, Calendar events, Drive file metadata and
     `drive.files.get_text` (export of Docs as text/plain, Sheets as text/csv); writes `gmail.messages.send`,
     `calendar.events.insert`; destructive (not exposed) `gmail.messages.trash`, `calendar.events.delete`.
   - `gchat` (`chat.googleapis.com`): spaces, messages; write `messages.create`; destructive `messages.delete`.
   Read scopes on the read client (`gmail.readonly`, `calendar.readonly`, `drive.readonly`,
   `chat.spaces.readonly`, `chat.messages.readonly`), write scopes on the write client (`gmail.send`,
   `calendar.events`, `chat.messages.create`) plus `gmail.metadata` for the crash check (headers and labels only).
   Every Gmail path is `users/me`: the person's own mailbox, never someone else's.
3. **Z builds the mail.** `gmail.messages.send` takes `{to[], cc[], subject, body, thread_id, in_reply_to}` (one
   plain address per entry, a one-line subject, no Bcc, no RFC 2047 encoded word anywhere: the mail library
   decodes `=?…?=` into extra recipients or unseen text); the executor builds the RFC 2822 message, checks that the
   recipients parsed back from it are exactly the reviewed ones, and the reviewer sees a `mail` preview of those
   fields. Every OpenAPI write with a body refuses top-level keys besides its parameters and `body`: the HTTP
   layer merged leftovers into the request, so a second `message` beside the reviewed one reached Microsoft Graph. Events show an `event` preview that says whether
   invitations go out (`sendUpdates`); Chat posts a `chat_message` preview with platform `google_chat`.
4. **Idempotency.**
   - Gmail: an `X-Z-Action: <action id>` header; the check reads the latest 25 SENT mails (metadata only) and stops
     at mails older than the proposal. Only the account itself puts mail in SENT.
   - Calendar: the private extended property `z_action`, searched with `privateExtendedProperty`; only an event
     this account created (`creator.self`, not `organizer.self`, which is true for every event of a shared
     calendar) with the reviewed summary counts. On a shared calendar the check finds nothing and the action ends
     "outcome unknown" after a crash, never a second event.
   - Chat: `requestId = <action id>`; the operation counts as idempotent, so recovery posts again and Chat returns
     the first message.
5. **No `-` in connector names**, now also enforced by the pack linter (the connector API already refused it).

## Consequences
- An organisation on Google Workspace uses the `workplace` pack without Microsoft 365 (`requires_connectors` is
  empty; the assistant and researcher agents list both suites' read tools).
- Shared mailboxes, shared drives as a service and Chat apps acting as a bot (service accounts with domain-wide
  delegation, the jwt-bearer grant) are not built: they need a signed-JWT client and a delegation review. Docs,
  Sheets and Slides edits are not built either (read through export only).
- A mail sent more than 25 sent mails before a crash is not found again: the action ends "outcome unknown" for a
  human to check, never a blind second send.
- An independent security review of this change found the encoded-word and top-level-key bypasses, the
  `organizer.self` ownership hole, `.`/`..` as a calendar id, a Chat preview claiming a reply Chat would not make
  (no `messageReplyOption`) and a thread of another space; all fixed with tests. Microsoft 365 events carry Graph's
  `transactionId` but do not count as idempotent: Graph does not document its answer to a repeated id.
