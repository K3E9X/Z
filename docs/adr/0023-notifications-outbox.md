# ADR 0023 — Notifications go through a transactional outbox to admin-configured targets

Status: accepted

## Context
docs/01 lists notifications (Mattermost, Slack, email, webhooks), docs/06 §5 budget alerts "to notifications",
docs/12 §1 an alert to the notification channels on every break-glass use. The spec does not define the
events, the configuration, the delivery guarantees or what content may leave Z in a notification.

## Decision
1. **Kinds**: `approval_requested`, `question_asked`, `needs_attention`, `mission_succeeded`, `mission_failed`
   (failed, cancelled, budget exceeded, timed out), `budget_alert`, `connector_unhealthy`, `breakglass_used`.
2. **Transactional outbox.** `events.append` (the one place mission events are written), the break-glass
   sign-in and the connector health monitor insert a `notification_outbox` row in the same transaction as the
   change: a rolled-back change sends nothing, a committed one is never lost. Budget alerts are detected by
   the notification worker, once per budget and period when usage crosses `alert_at`.
3. **Targets** (`GET/PUT /admin/notifications`, `guardrails:manage`, step-up, audited): Slack, Mattermost and
   Teams incoming webhooks, a generic webhook, email through SMTP. Each target filters by kinds and optionally
   teams. Webhook URLs carry their own token, so they are secret references, like the webhook signing key and
   the SMTP password; values never reach the database, the API or the audit log. URLs pass the SSRF guard at
   every delivery; redirects are not followed.
4. **Content stays in Z above the target's class.** Summaries, questions and mission titles are sent only for
   missions up to the target's `content_up_to` (default C1); above it the message says details are in Z.
   Messages only link to Z: approving needs the Z session and its rules (step-up, four eyes, two approvers).
5. **Delivery** is at least once, in outbox order per tick: a row records the targets that accepted it, failed
   targets are retried with exponential backoff (30 s to 1 h, 6 attempts), and each target's last success and
   last error are shown read-only in the API and the Admin → Notifications tab. Generic webhooks are signed:
   `X-Z-Signature: sha256=HMAC(key, X-Z-Timestamp + "." + body)`.
6. Personal notification preferences (per user) are out of scope for v1.1; the bell in the app lists what
   waits for the signed-in user.

## Consequences
- One delivery path for every alert the spec mentions; new kinds are one enqueue call plus a message format.
- A target that is down delays its own notifications only; the others are delivered and not repeated.
