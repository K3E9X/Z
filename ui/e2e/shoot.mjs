// Screenshot screens against a running UI (Vite or the built app) backed by a real z-server.
// Usage: node e2e/shoot.mjs [baseUrl] [path ...]   (theme via THEMES=night,day; out dir e2e/out)
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright-core'

const base = process.argv[2] ?? 'http://127.0.0.1:5173'
const paths = process.argv.slice(3)
const themes = (process.env.THEMES ?? 'night').split(',')
const width = Number(process.env.WIDTH ?? 1440)
const height = Number(process.env.HEIGHT ?? 960)
const full = process.env.FULL !== '0'
const out = process.env.OUT ?? 'e2e/out'
mkdirSync(out, { recursive: true })

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/opt/pw-browsers/chromium-1243/chrome-linux64/chrome' })
for (const theme of themes) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: theme === 'day' ? 'light' : 'dark' })
  await ctx.addInitScript((t) => {
    localStorage.setItem('z.pref.theme', t)
    localStorage.setItem('z.pref.followSystem', 'false')
  }, theme)
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(String(e)))
  for (const p of paths.length ? paths : ['/']) {
    await page.goto(base + p, { waitUntil: 'networkidle' }).catch(() => undefined)
    await page.waitForTimeout(Number(process.env.WAIT ?? 800))
    const name = (p.replace(/[/?=&]+/g, '_').replace(/^_|_$/g, '') || 'home') + `.${theme}.png`
    await page.screenshot({ path: `${out}/${name}`, fullPage: full })
    console.log('shot', name, errors.length ? `errors: ${errors.join(' | ').slice(0, 400)}` : '')
    errors.length = 0
  }
  await ctx.close()
}
await browser.close()
