# ADR 0034 — E-mail digests, replies to Z mails, synthetic tool-call probe

Status: accepted

## Context
docs/05 §10 promises e-mail digests (daily decisions, weekly report) and "reply to a Z mail answers a question";
config/z.yaml has `notifications.digest` (two cron strings) and `inference.probes.tool_call_probe_s`; docs/06 §5
describes a synthetic tool-call probe per alias that fills `tool_call_parse_error_rate`. The spec leaves out who
gets a digest and what it holds, how a digest is sent once with several replicas, how a reply is tied to its
question and authenticated, and what the probe sends and counts.

## Decision
1. **Digest recipients**: active people with an address who turned personal e-mail on (Settings → Notifications,
   ADR 0023 §6). No new preference: the contract has none, and the digest only covers what is theirs to decide or
   their own missions. Mails go through the notification outbox (new internal kind `digest`, addressed to one
   person, never to admin targets) and the admins' SMTP server, with the same retries. Without SMTP nothing is
   queued. Empty digests are not sent.
2. **Daily decisions**: pending writes the person may approve (`actions:approve` on the mission's team, not the
   starter of a four-eyes mission, not already approved by them) and open questions they may answer (the answer
   rules below), on missions that have not ended (eval missions excluded), up to 20 lines, each with its link to
   Z. Summaries, questions and titles only up to C1 (the personal cap); above it the line says the details are in
   Z with the data class. **Weekly report**: the person's missions created in the 7 days before the fire time, by
   status, the writes proposed on them by status, the approvals they gave and their missions' tokens — counts only.
3. **Once per period**: cron strings in UTC with the repo's cron parser (`intake/cron.py`). A worker step in
   `z-server all` takes a transaction-scoped advisory lock (`pg_try_advisory_xact_lock`; other replicas skip),
   reads the last fire time sent per digest from `settings` (`digest_state`), enqueues the mails of the latest fire
   time in (last sent, now] and stores it, in one transaction: a crash sends nothing or everything of that period.
   A digest seen for the first time starts from now (no mail for a period that ended before); after downtime only
   the latest missed period is sent.
4. **Reply token**: question mails (personal and admin e-mail targets, when an e-mail channel is enabled) carry
   `[Z-Q:<question id>.<mac>]` in the subject, mac = the first 96 bits of HMAC-SHA256 of the question id with a key
   derived (HKDF) from the platform secret. A subject tag survives every mail client's "Re:" and needs no special
   mailbox (plus-addressing depends on the mail server); replies go to the SMTP `from` address, which the
   operator routes to the mail relay. The mail says to answer on the first line.
5. **Answering by mail** uses the same service function as `POST /questions/{id}/answer` (`questions.answer`,
   extracted from routes_review without changing its behaviour): visibility, `questions:answer` on the mission's
   team unless the sender started it, one answer per question, events, job resumed, `question.answered` audit
   (actor `channel:<id>`, `runs_as` the person, `channel: email`). The sender is the channel's usual mapping (mail
   address → active Z user). The answer is the first line that is neither blank, quoted (`>`) nor a quote header
   ("On … wrote:", "-----Original Message-----", "From: …", "--"); an option is matched case-insensitively and
   recorded as written in the options. The line goes through the judge's pattern scan (blocked → refused); nothing
   else of the mail is read (invariant 6). A mail with a tag never becomes a task; a mail without one still does.
6. **Refusals** (unknown or forged token, question gone, empty or blocked answer, no right, already answered, not
   an option) change nothing but a `channel.denied` audit event. The sender gets a confirmation or the reason by
   mail (outbox kind `mail_reply`, no content above C1: options are named only for missions up to C1). Unknown
   senders get nothing back (no backscatter). The same reply delivered twice is acknowledged without a second
   answer.
7. **Tool-call probe**: every `tool_call_probe_s` (default 600, at least 60) one replica (advisory lock and a
   `last_run_at` in `settings`, claimed before the calls so no transaction stays open during them) sends each
   alias the platform key allows (internal only: nothing leaves the cluster), except `z-embed`, the single-call
   check of `poc/check_tool_calling.py` through LiteLLM: one `read_file` tool, `tool_choice: auto` (a forced or
   named tool choice would bypass the server's tool-call parser, which is what the probe watches), expecting a call
   of `read_file` whose arguments parse as JSON and hold `path: src/app.py`. The last 20 outcomes per alias are
   kept in `settings` (`tool_call_probes`), so the rate survives restarts. A gateway error (unreachable, non-200)
   is kept as the alias' last detail but stays out of the rate: it says nothing about parsing. `GET
   /inference/health` fills `tool_call_ok` (last probe), `tool_call_parse_error_rate` (failures over the kept
   probes) and `last_probe_at`. Probe tokens are recorded as platform usage (`z:probe`). External aliases are not
   probed. `inference.probes.models_poll_s` stays unread: `/v1/models` is queried on demand (cached 15 s).

## Consequences
- People see what waits for them without opening Z, and can answer a question from their mailbox, with the same
  rights, audit and events as in the app; approvals still happen only in Z.
- A spoofed `From` is the residual risk of mail answers: the attacker also needs the question's token, which only
  the recipients of that question mail have, and the answer is audited with the channel. The mail relay should
  only forward mail that passes the domain's SPF/DKIM/DMARC checks.
- docs/06 §5 ("the probe is not built yet") and docs/05 §10 can now point at this ADR.
