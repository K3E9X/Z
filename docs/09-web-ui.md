# 09 — Web app: design system and screens

The mockups in `design/mockups/*.html` (open in a browser) and `design/screenshots/*.png` are the reference. Match them closely: layout, hierarchy, copy, density. Tokens are in `design/tokens.css`.

## 1. Direction
**Quiet, expert, dense.** It should feel like a tool built by people who use it all day: hairline borders instead of boxes, few colors, generous type for headings, tabular numbers, monospace for anything machine-made. No gradients, no glow, no emoji, no decorative illustrations. Status is always color **and** icon or text.

## 2. Tokens
- **Themes** (CSS variables on `[data-theme]`): `night` (default), `day`, `slate`, `contrast` (high contrast, WCAG AAA text). Follow-system option.
- **Variables**: `--bg --panel --raised --line --line-2 --text --text-2 --text-3 --inv-bg --inv-text --code`, status `--run --wait --ok --err` with `-bg` tints, chart series `--s1 --s2 --s3`.
- **Typeface** (`[data-font]`, user setting): `plex` (IBM Plex Sans + IBM Plex Mono, default), `inter` (Inter + JetBrains Mono), `editorial` (Newsreader titles + Inter), `geist` (Geist + Instrument Serif). Variables `--sans --mono --display --display-w --display-ls`.
- **Accent**: `#F25C2A`, used only on the Z mark and the CLI prompt caret. Primary buttons are inverted (`--inv-bg`), not accent-colored.
- **Type scale**: body 13.5 px, small 12–12.5 px, section titles 15 px/600, page titles 44 px display face.
- **Shape**: radius 7–8 px controls, 10–14 px panels; 1 px borders; no shadows except focus.
- **Spacing**: 4 px grid; page padding 40 px; list rows 32–44 px.

### Status vocabulary
| State | Color | Mark |
|---|---|---|
| running | `--run` | spinning ring / filled dot |
| waiting (approval, question, needs attention) | `--wait` | dot + label |
| succeeded / healthy | `--ok` | check / dot |
| failed / unreachable | `--err` | cross |
| idle / disabled | `--text-3` | hollow dot |
| budget exceeded | `--err` + label "budget" | gauge |

## 3. Shell
- **Mission control** has the full sidebar: workspace switcher (team · environment), New mission, search (⌘K); nav: Mission control, Review, Tasks, Playbooks, History; Configure: Agents and skills, Learning, Connectors, Inference and usage, Settings; user at the bottom.
- Other pages use a 56 px **icon rail** with the same destinations (aria-labels, current page marked).
- Header: breadcrumb, live gateway indicator (`inferX · tokens today · budget`), notifications.
- The Review count is visible everywhere.

## 4. Screens (mocked)
| # | Screen | Mockup | Must show |
|---|---|---|---|
| 1 | Mission control | `Main` | Greeting, KPI strip (agents running, waiting for you, tokens today, inferX, connectors, success 7 d), Decisions list with inline Approve/Answer, Agents table (state, mission, now, progress, time), live preview of the selected agent, Finished today |
| 2 | Agent at work (mission detail) | `Agent` | Header with status and Pause / Take over / Stop; Plan checklist; sub-agents grid; Context (tools, skills, writes, budget); tabs Activity / Terminal / Files / Browser / Reasoning; timeline with tool calls, diffs, guardrail blocks, questions, proposed actions; "Guide the agent" composer |
| 3 | New mission | `Launch` | "What should Z do?" composer with @agents and #connectors; parsed chips (agent, source, writes to, matching playbook); Draft the plan; editable plan; autonomy, schedule, limits; Save as playbook; Start |
| 4 | Review | `Inbox` | Filters All / Writes / Questions; detail with runs-as identity, calls, reversibility, judge verdict; per-item checklist (exclude a duplicate); note; Reject / Edit / Approve ⌘↵; "Your decision also teaches the agent" |
| 5 | Tasks | `Tasks` | Intake sources (Jira label, GitLab assignee, Logos webhook, schedules); board Incoming / Scheduled / Running / Needs review / Done with suggested agents |
| 6 | Playbooks | `Playbooks` | Domain filters; cards with team, tools, writes, trigger, version, usage; Use; Create |
| 7 | Agents and skills | `Agents` | Roster (working / available) with each agent's chosen name, handle and avatar; agent detail: model, version, evals; tabs Profile / Objectives / Skills / Tools / Memory / Evals / Versions; skills table with origin (pack / learned) and improvement badges; New agent and Rename |
| 8 | Learning | `Learning` | Loop strip with counts; proposal detail (diff, evals before/after, regressions, token cost, Reject / Try on one mission / Apply); other proposals incl. discarded; recent signals |
| 9 | Connectors | `Connectors` | Add connector (MCP, OpenAPI/REST, GraphQL, CLI, database, browser session, inbound webhook); table with health, p95, operations exposed, used by; detail with read/write identities, secret rotation, rate limit, operations with class and exposure toggles, Test connection |
| 10 | Inference and usage | `Inference` | Time range; inferX status banner; KPI strip; tokens per hour by model (stacked bars, legend, table view); by agent; models table (replicas, queue, p95 TTFT, tok/s, context, status); cache and preemption stats; GPU nodes; missions by consumption vs cap; model providers + routing policy; events and budget settings |
| 11 | History and audit | `History` | Filterable audit table; Export audit log / Download JSON; event detail with actor, runs-as, hash; replay scrubber |
| 12 | Command palette | `Palette` | Ask Z, agents, missions, actions (approve, pause all, switch theme) |
| 13 | Settings | `Settings` | Appearance (themes, density, typeface, follow system); models and routing; guardrails (human approval locked on, egress allowlist, judge scan, learning needs approval) |
| 14 | Mobile | `Mobile` | Decisions cards (Approve / Mark failed), running list, Ask Z |
| 15 | CLI | `CLI` | See `10-cli.md` |
| 16 | Sign in | `SignIn` | "Sign in with GitLab" (only method), workspace name, environment, short note on what Z records; error states (not in an allowed group, account suspended); break-glass banner when enabled |
| 17 | Admin | `Admin` | Tabs Users / Teams / Roles and permissions / Group mapping / Sign-in / Sessions and tokens / Audit log / Platform; users table with effective roles and their source (GitLab group vs manual), last sign-in, 2FA, status; user detail panel; dry-run of mapping changes; every save shows a diff and asks for step-up |

## 5. Screens to design during the build (not mocked)
Follow the same system:
- **Connector builder**: stepper (source: spec / docs / browser / MCP) → operations review with proposed classes → credentials → test → save.
- **Trace replay**: full-width timeline scrubber over a finished mission, synced with terminal, browser screenshots and diffs.
- **Evals**: per agent/skill score history by model; case table expected vs. got; run evals.
- **Analytics**: hours saved by playbook (baseline minutes × completed), approval rate, edit rate, failure by connector, tokens and cost by team, trends.
- **Agent builder**: "Describe the agent you need" composer → drafted profile (name, handle, avatar, role, instructions, persona, model, skills, tools, objectives) → edit → the agent is created disabled and tried on a read-only sample mission → enable (or discard). Also from a template or by duplicating an agent.
- **Objectives** (agent tab and a Mission control widget showing objectives at risk or off track): each objective with metric, target, trend sparkline, status, assigned objects, last check, proposed missions; create/edit/pause.
- **Settings → Connections**: the user's personal connections (Outlook, Teams, OneDrive, Google…) with scopes granted, last used, revoke.
- **Channels** (admin): Teams, Slack, Mattermost, email; default agent per channel; allowed teams; test message.

## 6. Behavior
- Live by default over SSE; no refresh buttons; a thin banner if the stream drops, with auto-reconnect (`Last-Event-ID`).
- Optimistic updates for approve/reject with rollback on error.
- Keyboard: `⌘K` palette · `G M` mission control · `G R` review · `G T` tasks · `G P` playbooks · `G A` agents · `J/K` move · `↵` open · `Esc` close · `⌘↵` approve · `X` reject · `/` search · `?` help.
- Every irreversible action shows exactly what will run (diff or payload) and who it runs as.
- Long lists virtualized (logs, 500+ jobs). Large artifacts lazy-loaded.

## 7. Stack and components
React 19, Vite, TypeScript strict, TanStack Router + Query + Virtual, Tailwind 4 (tokens mapped from `design/tokens.css`), Z's own primitives in `ui/src/components` (no shadcn/ui or Radix), cmdk for the palette, the `diff` (jsdiff) library rendered by `DiffViewer` (no Monaco), react-markdown + remark-gfm, generated API client (`openapi-typescript` + `openapi-fetch`), MSW mocks for development.
Shared components: `StatusMark`, `KpiStrip`, `DecisionRow`, `AgentTable`, `PlanChecklist`, `EventTimeline`, `ToolCallItem`, `DiffViewer`, `ActionReview`, `QuestionCard`, `SchemaForm`, `TokenMeter`, `ConnectorHealth`, `StackedBars`, `IconRail`, `Sidebar`, `CommandPalette`.

## 8. Accessibility
WCAG 2.2 AA in all themes (AAA text in `contrast`); visible focus; full keyboard; `aria-live` for new decisions; charts have a table view and an `aria-label` summary; usable from 1280 px, review and approve usable on mobile.
