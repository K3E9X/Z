# ADR 0026 — Chat tool connectors: markers for idempotent posts, errors read from 2xx bodies

Status: accepted

## Context
docs/05 §7 lists Mattermost (P1) and Slack as chat tool connectors (read channel history and threads; post and
reply through the executor). Two things the spec does not settle:
- The executor must not post twice after a crash between the tool call and its bookkeeping (docs/05 §2 asks for
  idempotent writes). Neither API has an idempotency key that survives minutes.
- Slack answers errors with HTTP 200 and `{"ok": false, "error": ...}`. The generic HTTP path treats any 2xx as
  success, so a refused post would be recorded as applied, a bad token would look healthy, and an agent would
  read an error as data.

## Decision
1. **Markers set by Z, never by the agent.** The executor adds a marker to every post: Mattermost
   `props.z_action = <action id>`, Slack message metadata `{event_type: z_action, event_payload: {action}}`. The
   operations' body schemas have no `props` or `metadata` field, so a proposal carrying one is refused (422).
   Before posting, and when recovering an action found `applying`, the executor reads the channel (or the
   thread) since the proposal and treats a message with the marker as already applied.
2. **`error_body` in the connector contract.** An optional `{path, equals, message}`: a 2xx answer whose body
   field `path` equals `equals` is an error whose reason is the `message` field. It applies to agent reads,
   connection tests, health probes and executor writes alike. Slack ships with
   `error_body: {path: ok, equals: false, message: error}`.
3. **Credentials.** Read and write stay separate accounts or apps. The write account also reads history (Slack:
   `channels:history`, `groups:history`) because the recovery lookup runs in the executor with the write
   credential; the read credential never leaves the gateway.

## Consequences
- Retries and crash recovery never duplicate a chat post; a crash before the post re-posts once.
- Other APIs that report errors in 2xx bodies (some legacy REST tools) need only the manifest field, no code.
- Health probes declared as `health.operation` now run (they were ignored before this change, for DefectDojo too).
