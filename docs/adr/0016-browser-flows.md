# ADR 0016 — Browser connectors: reviewed flows replayed by z-browser

Status: accepted

## Context
docs/05 §1 lets a team reach a tool that only has a web UI through a `browser` connector: named operations
backed by recorded flows, replayed gateway-side by the `z-browser` worker (Playwright + Chromium). The spec
leaves the flow format, how agent input and credentials enter a flow, how the browser's network access is
limited, and how a flow is kept to what an admin reviewed.

## Decision
1. **Flow format.** A connector has `flows: {name: {start_url?, allowed_hosts?, timeout_s?, steps}}`; each
   `declared` operation names one (`flow`). Steps: `goto, click, fill, select, check, uncheck, press, wait_for,
   wait_for_url, extract_text, extract_table, extract_attribute, assert_text, assert_visible, screenshot`, each
   with an optional `timeout_ms` and `optional`. Flows are written in the connector file or the builder (JSON);
   interactive recording is a later feature (`recordBrowserFlow` answers 503 until then).
2. **Placeholders are fenced.** `{{args.x}}` may appear only in `goto`/`wait_for_url` URLs (percent-encoded) and
   in `fill`/`select`/`assert_text` values, never in a selector: what a flow clicks is fixed by the reviewed
   file, not by an agent. `{{auth.username}}`/`{{auth.password}}` may appear only in `fill` values. Every argument
   a flow uses must be declared in the operation's input schema. The pack linter, the connector API and the
   pydantic model enforce the same rules (`z_common.connectors.browser`).
3. **Review is bound to the steps.** An operation's `source_ref` is `flow:<name>@<sha256 of the flow>[:12]`.
   Changing a flow's steps changes the reference: the operation loses its exposure until an admin reviews it
   again, and the gateway refuses to run a flow whose digest no longer matches.
4. **Classes** come from the operation id like any declared operation (verbs; rules can only raise). A read
   flow still runs with the read account; read and write accounts stay different (invariant 7).
5. **Credentials.** A `basic` credential is a form login: the gateway resolves it and the worker types it into
   the `{{auth.*}}` fields. Bearer and API-key credentials become request headers added only to requests to the
   connector's own host (never to a CDN or another allowed host). Credentials never reach the agent; the
   worker scrubs them from its messages and the gateway redacts results as for every tool.
6. **Network.** One Chromium per worker, one fresh context per run (no cookies or storage shared between runs
   or people), service workers blocked, downloads refused. Every request goes through a route guard: http(s)
   to the connector host and the flow's `allowed_hosts` only. Because the browser follows redirects without
   calling the route again, the guard fetches each request itself without following redirects and checks the
   next hop before the browser may follow it. Deploy-level egress (Helm NetworkPolicy `browser.egressTo`) is the
   second fence; runners cannot reach the worker.
7. **Results.** The agent receives `{url, title, data, failed_steps?, blocked_hosts?, screenshots}`; screenshots
   (password fields masked) are stored as `screenshot` artifacts of the connector's data class and referenced
   by id. Results are judged and raise the mission's data class like any tool result. Writes run through
   propose → approval → executor with the write account; a browser write has no idempotency lookup, so a crash
   between the click and the bookkeeping leaves the action for a human instead of replaying it.
8. **Worker API.** `POST /v1/run` with a bearer `Z_BROWSER_TOKEN` (>= 16 characters, refused otherwise), at
   most `Z_BROWSER_CONCURRENCY` runs at a time, each bounded by the flow timeout (5–300 s).

## Consequences
- A web-only tool is usable by agents within minutes, with the same review, audit, judge and approval rules
  as an API connector.
- Flows are brittle by nature (selectors change): `optional` steps absorb cookie banners and similar noise,
  `assert_*` steps make a changed page fail loudly, and failure screenshots help the admin repair the flow.
- Recording in a sandboxed browser, flow reuse across operations (e.g. one shared login) and a visual flow
  editor are left for later.
