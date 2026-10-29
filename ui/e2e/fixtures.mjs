// TEST FIXTURES ONLY. Screens whose endpoints this z-server does not serve yet (Inference, Learning,
// Analytics, Tasks) are rendered with contract-shaped fixtures intercepted in the browser, to check
// their populated layout. Everything else still comes from the real server. Never used by the app.
// Usage: node e2e/fixtures.mjs <baseUrl> [theme]
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright-core'

const [base = 'http://127.0.0.1:5173', theme = 'night'] = process.argv.slice(2)
const out = process.env.OUT ?? 'e2e/out/fixtures'
mkdirSync(out, { recursive: true })
const now = Date.now()
const iso = (msAgo) => new Date(now - msAgo).toISOString()
const H = 3600_000

const buckets = (scale) =>
  Array.from({ length: 24 }, (_, i) => {
    const wave = 0.55 + 0.45 * Math.sin((i / 24) * Math.PI * 2 - 1.2)
    const tin = Math.round(scale * wave * 1000)
    return {
      ts: iso((23 - i) * H),
      tokens_in: tin,
      tokens_out: Math.round(tin * 0.18),
      cached_tokens: Math.round(tin * 0.42),
      requests: Math.round(tin / 2600),
    }
  })
const series = (key, label, scale, cap = null) => {
  const b = buckets(scale)
  const sum = (k) => b.reduce((n, x) => n + x[k], 0)
  return {
    key,
    label,
    buckets: b,
    tokens_in: sum('tokens_in'),
    tokens_out: sum('tokens_out'),
    cached_tokens: sum('cached_tokens'),
    requests: sum('requests'),
    cost: null,
    cap,
  }
}
const usage = (group_by) => ({
  group_by,
  bucket: 'hour',
  from: iso(24 * H),
  to: iso(0),
  series:
    group_by === 'model'
      ? [series('z-heavy', 'z-heavy', 420), series('z-fast', 'z-fast', 260), series('z-judge', 'z-judge', 60)]
      : group_by === 'agent'
        ? [
            series('dev-engineer', 'Dev Engineer', 300),
            series('vuln-analyst', 'Vuln Analyst', 210),
            series('ticket-manager', 'Ticket Manager', 120),
            series('reviewer', 'Reviewer', 70),
          ]
        : [
            series('msn_fx1', 'log4j 2.24.x across the infra group', 280, 2_000_000),
            series('msn_fx2', 'Triage this week’s critical findings', 190, 1_000_000),
            series('msn_fx3', 'Inactive accounts in Logos', 60, 500_000),
          ],
})
const model = (alias, status, extra) => ({
  alias,
  provider: 'inferx',
  kind: 'internal',
  status,
  listed: true,
  replicas_ready: 2,
  replicas_desired: 2,
  requests_running: 6,
  requests_waiting: 1,
  ttft_p95_ms: 640,
  tokens_per_s: 1180,
  kv_cache_usage: 0.62,
  context: 131072,
  tool_call_ok: true,
  tool_call_parse_error_rate: 0.004,
  last_probe_at: iso(60_000),
  ...extra,
})
const FIX = {
  '/inference/health': {
    status: 'healthy',
    gateway: { ok: true, latency_ms: 4, error: null },
    prometheus_configured: true,
    models: [
      model('z-heavy', 'healthy'),
      model('z-fast', 'healthy', { ttft_p95_ms: 210, tokens_per_s: 2340, kv_cache_usage: 0.41 }),
      model('z-judge', 'degraded', { replicas_ready: 1, ttft_p95_ms: 1900, requests_waiting: 9 }),
    ],
    nodes: [
      { name: 'gpu-a01', gpu_util: 0.82, gpu_mem_used_ratio: 0.91, gpu_temp_c: 71 },
      { name: 'gpu-a02', gpu_util: 0.64, gpu_mem_used_ratio: 0.88, gpu_temp_c: 66 },
      { name: 'gpu-b01', gpu_util: 0.31, gpu_mem_used_ratio: 0.52, gpu_temp_c: 58 },
    ],
    prefix_cache_hit_rate: 0.42,
    preemptions_24h: 3,
    tokens_today: 18_400_000,
    checked_at: iso(20_000),
  },
  '/inference/models': null, // filled below
  '/budgets': [
    {
      id: 'bdg_org',
      scope: 'org',
      limit: 30_000_000,
      period: 'month',
      alert_at: 0.8,
      used: 18_400_000,
      hard: false,
    },
    {
      id: 'bdg_team',
      scope: 'team:cyber-ops',
      limit: 12_000_000,
      period: 'month',
      alert_at: 0.8,
      used: 9_900_000,
      hard: true,
    },
  ],
  '/providers': [
    {
      id: 'prv_inferx',
      name: 'inferX',
      kind: 'internal',
      aliases: ['z-heavy', 'z-fast', 'z-judge', 'z-embed'],
      data_classes: ['C0', 'C1', 'C2', 'C3'],
      enabled: true,
    },
    {
      id: 'prv_ext',
      name: 'External provider',
      kind: 'external',
      aliases: ['ext-large'],
      data_classes: ['C0', 'C1'],
      enabled: false,
    },
  ],
  '/routing-policy': {
    default_mission_class: 'C2',
    external_fallback_when_degraded: false,
    never_external: ['C2', 'C3'],
    never_in_memory: ['C3'],
    data_classes: { C0: 'public', C1: 'internal', C2: 'confidential', C3: 'restricted' },
    rules: [],
  },
  '/proposals': {
    next_cursor: null,
    items: [
      {
        id: 'prp_fx1',
        kind: 'skill_edit',
        target: 'ticket-writing',
        title: 'Ask for the owning team before drafting a SECOPS ticket',
        diff: '--- a/SKILL.md\n+++ b/SKILL.md\n@@ -12,2 +12,3 @@\n - Name the component and the fix.\n+- If the owning team is unknown, ask before drafting.\n - Link every source finding.\n',
        rationale: 'Three of the last five rejections were tickets sent to the wrong team.',
        evidence: ['3 rejected writes this week', '2 edits changing the assignee'],
        eval_before: {
          checks_passed: 41,
          checks_total: 48,
          cases_passed: 9,
          cases_total: 12,
          rubric_avg: 1.4,
          tokens: 182_000,
        },
        eval_after: {
          checks_passed: 46,
          checks_total: 48,
          cases_passed: 11,
          cases_total: 12,
          rubric_avg: 1.7,
          tokens: 176_000,
        },
        regressions: [],
        cost_delta: -0.03,
        shadow: { missions: 6, matched: 6 },
        status: 'ready',
        created_at: iso(5 * H),
        decided_by: null,
        decided_at: null,
      },
      {
        id: 'prp_fx2',
        kind: 'routing_rule',
        target: 'finding_kind=dependency',
        title: 'Send dependency findings to Dev Engineer first',
        diff: '{"match":{"finding_kind":"dependency"},"target":{"agent":"dev-engineer"}}',
        evidence: ['8 reassignments'],
        eval_before: null,
        eval_after: null,
        regressions: [],
        cost_delta: null,
        shadow: null,
        status: 'evaluating',
        created_at: iso(9 * H),
        decided_by: null,
        decided_at: null,
      },
    ],
  },
  '/signals': {
    next_cursor: null,
    items: [
      {
        id: 'sig_1',
        kind: 'action_rejected',
        mission_id: 'msn_fx2',
        job_id: null,
        agent: 'ticket-manager',
        skill: 'ticket-writing',
        payload: { reason: 'Wrong team' },
        ts: iso(2 * H),
      },
      {
        id: 'sig_2',
        kind: 'action_edited',
        mission_id: 'msn_fx2',
        job_id: null,
        agent: 'ticket-manager',
        skill: null,
        payload: { field: 'assignee' },
        ts: iso(3 * H),
      },
      {
        id: 'sig_3',
        kind: 'guardrail_hit',
        mission_id: 'msn_fx1',
        job_id: null,
        agent: 'dev-engineer',
        skill: null,
        payload: { what: 'network call outside the allowlist' },
        ts: iso(4 * H),
      },
    ],
  },
  '/analytics': {
    from: iso(30 * 24 * H),
    to: iso(0),
    hours_saved: [
      { playbook: 'weekly-vuln-triage', completed: 4, baseline_minutes: 240, hours: 16 },
      { playbook: 'remediate-dependency', completed: 11, baseline_minutes: 90, hours: 16.5 },
      { playbook: 'mr-security-review', completed: 23, baseline_minutes: 25, hours: 9.6 },
    ],
    approval_rate: 0.82,
    edit_rate: 0.11,
    failures_by_connector: [
      { connector: 'confluence', failures: 7 },
      { connector: 'jira', failures: 2 },
    ],
    tokens_by_team: [
      { team: 'Cyber Ops', tokens: 41_200_000, cost: null },
      { team: 'Architecture', tokens: 9_800_000, cost: null },
    ],
    missions_by_status: { succeeded: 31, failed: 2, cancelled: 1, timed_out: 1 },
  },
  '/tasks': {
    next_cursor: null,
    items: [
      {
        id: 'tsk_1',
        source: 'jira',
        external_ref: 'SECOPS-1412',
        title: 'Review firewall exception for the payments VLAN',
        body_ref: null,
        suggested_agent: 'security-architect',
        suggested_playbook: null,
        status: 'incoming',
        mission_id: null,
        created_at: iso(40 * 60_000),
      },
      {
        id: 'tsk_2',
        source: 'gitlab',
        external_ref: 'infra/api-gateway!482',
        title: 'Security review of merge request !482',
        body_ref: null,
        suggested_agent: 'reviewer',
        suggested_playbook: 'mr-security-review',
        status: 'incoming',
        mission_id: null,
        created_at: iso(2 * H),
      },
      {
        id: 'tsk_3',
        source: 'email',
        external_ref: null,
        title: 'Leaver list from HR for October',
        body_ref: null,
        suggested_agent: null,
        suggested_playbook: 'inactive-account-review',
        status: 'incoming',
        mission_id: null,
        created_at: iso(5 * H),
      },
    ],
  },
  '/triggers': [
    {
      id: 'trg_1',
      kind: 'schedule',
      name: 'Weekly vuln triage',
      config: { cron: '0 8 * * 1' },
      filter: {},
      playbook: 'weekly-vuln-triage',
      inputs: {},
      owner_user: 'dev',
      auto_start: true,
      enabled: true,
      last_fired_at: iso(3 * 24 * H),
      webhook_url: null,
      secret: null,
      created_at: iso(30 * 24 * H),
    },
  ],
}
FIX['/inference/models'] = FIX['/inference/health'].models

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/opt/pw-browsers/chromium' })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 960 },
  colorScheme: theme === 'day' ? 'light' : 'dark',
})
await ctx.addInitScript((t) => {
  localStorage.setItem('z.pref.theme', t)
  localStorage.setItem('z.pref.followSystem', 'false')
}, theme)
await ctx.route(
  /\/api\/v1\/(inference\/health|inference\/models|usage|budgets|providers|routing-policy|proposals|signals|analytics|tasks|triggers)(\?|$)/,
  (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const url = new URL(route.request().url())
    const key = url.pathname.replace('/api/v1', '')
    const body = key === '/usage' ? usage(url.searchParams.get('group_by') ?? 'model') : FIX[key]
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  },
)
const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
for (const p of ['/inference', '/learning', '/analytics', '/tasks', '/']) {
  await page.goto(base + p, { waitUntil: 'load' })
  await page.waitForTimeout(1800)
  const name = `${out}/${p.replace(/\//g, '') || 'home'}.${theme}.png`
  await page.screenshot({ path: name, fullPage: true })
  console.log('shot', name)
}
if (errors.length) console.log('page errors:', errors.join(' | '))
await browser.close()
