# z-ui

The Z web app: mission control, live mission view and replay, review, tasks, playbooks, history,
agents and skills, learning, evals, analytics, connectors, inference and usage, settings, admin
(`docs/09-ui-and-cli.md` §4 and §5). React 19, Vite, TypeScript strict, TanStack Router and Query,
Tailwind v4 mapped to the design tokens, cmdk, react-markdown. No CDN: fonts are self-hosted.

## Develop

Requirements: Node 22 and pnpm 10 (`corepack enable`). Packages must be at least 7 days old
(`minimumReleaseAge` in `pnpm-workspace.yaml`).

```sh
pnpm -C ui install
Z_API_URL=http://127.0.0.1:8000 pnpm -C ui dev    # http://127.0.0.1:5173
```

Vite proxies `/api` and `/healthz` to `Z_API_URL` (default `http://127.0.0.1:8000`), so the app,
its session cookie and the API share one origin, as behind the z-ui image. Point it at any running
z-server: the compose stack (`docker compose -f deploy/compose/docker-compose.yml up -d`, API on
`127.0.0.1:8080`) or `uv run z-server all`. There is no mock mode: screens whose endpoint the server
does not serve yet (404 or 405 from the router, 501) show a "not available on this server yet" state
naming the endpoint, never invented numbers.

## Security model in the client

- The session is the server's cookie. Nothing is stored in `localStorage` but display preferences
  (theme, density, typeface); ESLint forbids storage globals elsewhere.
- Every non-GET request sends the `__Host-z_csrf` cookie value (or the `csrf_token` from `GET /me`)
  as `X-CSRF-Token` (`src/api/client.ts`).
- Errors are RFC 9457 problems (`src/api/problem.ts`): 401 shows the sign-in page, 403 names the
  missing permission, `urn:z:problem:step-up-required` opens the re-authentication dialog and
  retries (`src/shell/stepup.tsx`).
- Tool, ticket, document and agent content is untrusted: rendered as text, Markdown without raw
  HTML (`skipHtml`, safe URL transform, images as alt text), diffs as text. ESLint forbids
  `dangerouslySetInnerHTML`, `innerHTML` and `rehype-raw`.
- The build has no inline script or style element, so the image serves it under
  `default-src 'self'; script-src 'self'; style-src 'self'; ... frame-ancestors 'none'`.

## Live data

`src/api/sse.ts` reads Server-Sent Events with `fetch` (cookies, `Last-Event-ID` resume, server
`retry:`, exponential backoff, stall detection). `src/api/live.ts` tracks every open stream for the
shell's "reconnecting" banner. `src/api/useMissionTimeline.ts` batches events per animation frame
into the pure reducer `src/lib/timeline.ts`; the timeline virtualizes past 150 rows.

## Test

```sh
pnpm -C ui lint          # eslint (type-aware) + security rules
pnpm -C ui typecheck     # tsc --noEmit, strict
pnpm -C ui test          # vitest + Testing Library + MSW (unit and component tests)
pnpm -C ui build         # typecheck, then the production build in ui/dist
```

End to end, against a running UI backed by a real z-server (Chromium is preinstalled at
`/opt/pw-browsers/chromium`; set `CHROME` to use another binary; never `playwright install`):

```sh
pnpm -C ui e2e http://127.0.0.1:5173                 # read-only: every screen + global shortcuts
pnpm -C ui e2e:a11y http://127.0.0.1:5173 / /review  # axe-core WCAG 2.2 AA, THEMES=night,day
THEMES=night,day pnpm -C ui e2e:shots http://127.0.0.1:5173 / /review /inference   # e2e/out/
pnpm -C ui e2e:flows http://127.0.0.1:5173 review-single night   # mutating flows, dev servers only
node ui/e2e/fixtures.mjs http://127.0.0.1:5173 day   # contract-shaped fixtures for unserved endpoints
```

Flows (`e2e/flows.mjs`): `palette`, `help`, `mobile`, `review-single` (approve with Ctrl+Enter,
reject with X and a reason), `review-batch`, `atlas` (duplicate an agent as Atlas, then `@atlas`
completion), `signin`, `noaccess`, `stepup`. They change real state on the server they target.
Fixtures (`e2e/fixtures.mjs`) exist only to check populated layouts of screens the server does not
serve yet; the app itself never ships fake data. Screenshots land in `ui/e2e/out/` (git-ignored).

## Regenerate the API client

`contracts/` is the source of truth. After a contract change:

```sh
pnpm -C ui generate:api   # src/api/schema.gen.ts, events.gen.ts, permissions.gen.ts
pnpm -C ui check:api      # fails when schema.gen.ts is out of date (the image build runs it)
```

Never edit the `*.gen.ts` files. Shapes the generator cannot express are adapted in
`src/api/types.ts` (for example `toContractPlan`).

## Build the image

`deploy/images/z-ui/`: a pinned Node image builds `ui/dist` (frozen lockfile, client checked
against `contracts/`); the runtime is unprivileged nginx (uid 101) that serves the app with a strict
CSP and security headers, falls back to `index.html`, caches hashed assets for a year and proxies
`/api` and `/healthz` to z-server with unbuffered SSE. It only writes to `/tmp`.

```sh
docker build -f deploy/images/z-ui/Dockerfile -t z-ui:local .
# behind a TLS-intercepting proxy or with an internal registry:
docker build -f deploy/images/z-ui/Dockerfile --secret id=extra_ca,src=/path/ca.pem \
  --build-arg NPM_REGISTRY=https://npm.example.internal/ -t z-ui:local .
docker run --read-only --tmpfs /tmp:uid=101,gid=101 --cap-drop ALL --security-opt no-new-privileges \
  -e Z_SERVER_UPSTREAM=z-server:8000 -p 8080:8080 z-ui:local
```

`Z_SERVER_UPSTREAM` (default `z-server:8000`) and `Z_UI_PORT` (default `8080`) are read at start.
Health: `GET /ui-healthz` (nginx itself) and `GET /healthz` (proxied to z-server).

## Layout

```
src/api/         generated client, problem handling, SSE, queries, review actions
src/lib/         timeline reducer, formatting, status, hotkeys, prefs, auth helpers
src/components/  StatusMark, EventTimeline, ActionReview, DiffViewer, SchemaForm, charts, ...
src/shell/       navigation, top bar, command palette, shortcuts help, step-up
src/screens/     one file per screen (admin/ for the admin tabs)
tests/           Vitest suites (MSW for HTTP)       e2e/  Playwright scripts against a real server
```
