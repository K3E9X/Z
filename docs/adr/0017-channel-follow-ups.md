# ADR 0017 — Chat follow-ups: approval cards link to Z, content capped by data class

Status: accepted

## Context
docs/05 §10 promises progress updates, approval cards (Teams Adaptive Cards, Slack/Mattermost buttons) and a
final summary in the thread a mission was started from, and says that approving from a chat card requires a
fresh Z session check, and that destructive and two-approver actions are approved only in the Z app. The spec
does not say how the thread is remembered, how posts survive restarts, or what content may leave Z for a chat
platform (Slack is a SaaS outside the internal cluster).

## Decision
1. **Thread record.** A mission started from a chat gets a `channel_threads` row with the sealed reply target
   (Teams service URL + conversation + activity, Slack channel + thread, or a slash-command response URL)
   and a cursor on the mission's events. Nothing is posted inside the request that starts the mission.
2. **Outbox by cursor.** A worker (in `z-server all`) walks open threads (`FOR UPDATE SKIP LOCKED`, so several
   replicas share the work), posts for `action_proposed`, `question` and mission `needs_attention` / terminal
   status events, and advances the cursor. A failed post stops that thread's batch and is retried; after 5
   failures the thread is marked failed. Delivery is at least once (a crash between a post and the commit can
   repeat one post). A thread closes once its mission has ended and every event was handled.
3. **Approving stays in Z.** Cards and messages carry a link to the action's review in Z (`/review?action=…`)
   or to the mission. No chat button approves anything: the link goes through the Z session and step-up like
   any approval, which satisfies "fresh Z session check" without trusting chat-platform callbacks. Destructive
   actions say they need two approvers in the Z app.
4. **Content cap.** Summaries, questions and reasons are posted only when the mission's data class is at most
   the channel's `config.content_up_to` (default `C1`); above it the post says the details are in Z. This keeps
   C2+ content off chat platforms unless an admin raises the cap for an internal one (e.g. Mattermost on the
   internal network).
5. **Formats.** Teams: an Adaptive Card 1.4 (`TextBlock`, `FactSet`, `Action.OpenUrl`) as a reply activity.
   Slack: `chat.postMessage` in the thread with mrkdwn text, `<`, `>` and `&` escaped (no interactive
   buttons, so the app needs no interactivity endpoint). Slash commands and Mattermost: the response URL
   (ephemeral), limited to the chat server's host as for the first reply.

## Consequences
- People who start work in chat see what needs them there, while every decision is still made, audited and
  step-upped in Z.
- Slack slash-command response URLs expire (30 minutes, a few uses): long missions started that way lose
  their follow-ups (the thread fails after 5 attempts); Slack mentions with a bot token do not have this limit.
