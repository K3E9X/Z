// axe-core (WCAG 2.2 A/AA rules) on screens of a running UI backed by a real z-server.
// Usage: node e2e/a11y.mjs <baseUrl> [path ...]   (THEMES=night,day). Exits 1 on any serious/critical violation.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { chromium } from 'playwright-core'

const require = createRequire(import.meta.url)
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8')
const base = process.argv[2] ?? 'http://127.0.0.1:5173'
const paths = process.argv.slice(3)
const themes = (process.env.THEMES ?? 'night,day').split(',')

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/opt/pw-browsers/chromium' })
let failed = 0
for (const theme of themes) {
  // The page's CSP forbids injected scripts (as it should): the audit context bypasses it.
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, bypassCSP: true, colorScheme: theme === 'day' ? 'light' : 'dark' })
  await ctx.addInitScript((t) => {
    localStorage.setItem('z.pref.theme', t)
    localStorage.setItem('z.pref.followSystem', 'false')
  }, theme)
  const page = await ctx.newPage()
  for (const p of paths.length ? paths : ['/']) {
    await page.goto(base + p, { waitUntil: 'load' })
    await page.waitForTimeout(Number(process.env.WAIT ?? 1500))
    await page.evaluate(axeSource)
    const res = await page.evaluate(() =>
      // eslint-disable-next-line no-undef
      axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } }),
    )
    const bad = res.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    const minor = res.violations.length - bad.length
    console.log(`${theme} ${p}: ${bad.length} serious/critical, ${minor} moderate/minor`)
    for (const v of res.violations) {
      console.log(`  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length})`)
      for (const n of v.nodes.slice(0, 3)) console.log(`      ${n.target.join(' ')} ${n.failureSummary?.split('\n')[1]?.trim() ?? ''}`.slice(0, 220))
    }
    failed += bad.length
  }
  await ctx.close()
}
await browser.close()
process.exit(failed ? 1 : 0)
