// Read-only end-to-end smoke suite against a running UI backed by a REAL z-server (no mocks).
// Usage: pnpm e2e [baseUrl]   (default http://127.0.0.1:5173; the built app behind nginx works too)
// Checks every screen renders without a script error or CSP violation, then the global shortcuts.
// Mutating flows (approve, reject, batch, duplicate an agent) live in e2e/flows.mjs.
import { chromium } from 'playwright-core'

const base = process.argv[2] ?? process.env.Z_UI_URL ?? 'http://127.0.0.1:5173'
const SCREENS = [
  ['/', /Good (morning|afternoon|evening)/],
  ['/missions/new', /What should Z do\?/],
  ['/review', /Review/],
  ['/tasks', /Tasks/],
  ['/playbooks', /Playbooks/],
  ['/history', /History/],
  ['/agents', /Agents/],
  ['/agents/new', /New agent/],
  ['/learning', /Learning/],
  ['/evals', /Evals/],
  ['/analytics', /Analytics/],
  ['/connectors', /Connectors/],
  ['/connectors/new', /connector/i],
  ['/inference', /Inference and usage/],
  ['/settings', /Settings/],
  ['/admin', /Admin/],
  ['/no-such-page', /not found|doesn.t exist|no page/i],
]

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
const problems = []
page.on('console', (m) => {
  const t = m.text()
  // 404/405 are endpoints this server does not serve yet: the UI shows a "not available" state for them.
  if (m.type() === 'error' && !/status of 40[45]/.test(t)) problems.push(t)
})
page.on('pageerror', (e) => problems.push(String(e)))

let failures = 0
const check = (ok, label, extra = '') => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${extra ? ` · ${extra}` : ''}`)
  if (!ok) failures++
}

for (const [path, heading] of SCREENS) {
  problems.length = 0
  await page.goto(base + path, { waitUntil: 'load' })
  const found = await page
    .getByRole('heading', { name: heading })
    .first()
    .waitFor({ timeout: 8000 })
    .then(() => true)
    .catch(() => false)
  await page.waitForTimeout(300)
  check(found && problems.length === 0, path, problems.join(' | ').slice(0, 300))
}

// A mission and its replay, when the server has one.
const missions = await page.evaluate(async () => (await fetch('/api/v1/missions?limit=1', { credentials: 'same-origin' })).json())
const m = missions.items?.[0]
if (m) {
  for (const p of [`/missions/${m.id}`, `/missions/${m.id}/replay`]) {
    problems.length = 0
    await page.goto(base + p, { waitUntil: 'load' })
    const ok = await page.getByRole('heading', { name: m.title }).first().waitFor({ timeout: 8000 }).then(() => true).catch(() => false)
    check(ok && problems.length === 0, p, problems.join(' | ').slice(0, 300))
  }
}

// Shortcuts (docs/09 §6).
await page.goto(base + '/', { waitUntil: 'load' })
await page.getByRole('heading', { name: /Good/ }).first().waitFor()
await page.keyboard.press('Control+k')
const palette = page.getByRole('dialog', { name: 'Command palette' })
check(await palette.isVisible(), 'Ctrl K opens the palette')
await page.keyboard.type('settings')
await page.waitForTimeout(200)
const options = await palette.getByRole('option').allTextContents()
check(options.some((o) => /Settings/.test(o)) && options.every((o) => /settings|New mission/i.test(o)), 'palette filters by words', options.join(', ').slice(0, 200))
await page.keyboard.press('Escape')
await page.keyboard.press('Shift+?')
check(await page.getByRole('dialog', { name: /Keyboard shortcuts/ }).isVisible(), '? opens the shortcuts')
await page.keyboard.press('Escape')
await page.keyboard.press('g')
await page.keyboard.press('r')
await page.waitForURL(/\/review$/, { timeout: 3000 }).catch(() => undefined)
check(new URL(page.url()).pathname === '/review', 'g r goes to Review')
await page.keyboard.press('g')
await page.keyboard.press('n')
await page.waitForURL(/\/missions\/new/, { timeout: 3000 }).catch(() => undefined)
check(new URL(page.url()).pathname === '/missions/new', 'g n starts a new mission')

await browser.close()
console.log(failures ? `${failures} failed` : 'all passed')
process.exit(failures ? 1 : 0)
