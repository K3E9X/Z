// Interaction flows against a running UI backed by a real z-server. Each flow screenshots its result.
// Usage: node e2e/flows.mjs <baseUrl> <flow> [theme]
//   flows: palette | help | mobile | review-single | review-batch | atlas | signin-error
// These change real state on the server (decisions, a new agent): run them on a dev server only.
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright-core'

const [base = 'http://127.0.0.1:5173', flow = 'palette', theme = 'night'] = process.argv.slice(2)
const out = process.env.OUT ?? 'e2e/out/flows'
mkdirSync(out, { recursive: true })
const mobile = flow === 'mobile'
const browser = await chromium.launch({ executablePath: process.env.CHROME ?? '/opt/pw-browsers/chromium' })
const ctx = await browser.newContext({
  viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 },
  colorScheme: theme === 'day' ? 'light' : 'dark',
  ...(mobile ? { isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : {}),
})
await ctx.addInitScript((t) => {
  localStorage.setItem('z.pref.theme', t)
  localStorage.setItem('z.pref.followSystem', 'false')
}, theme)
const page = await ctx.newPage()
const problems = []
page.on('console', (m) => {
  if (m.type() === 'error' && !/Failed to load resource: the server responded with a status of 40[45]/.test(m.text())) problems.push(m.text())
})
page.on('pageerror', (e) => problems.push(String(e)))
const shot = async (name, full = false) => {
  await page.screenshot({ path: `${out}/${name}.${theme}.png`, fullPage: full })
  console.log('shot', `${out}/${name}.${theme}.png`)
}
const go = async (p) => {
  await page.goto(base + p, { waitUntil: 'networkidle' }).catch(() => undefined)
  await page.waitForTimeout(600)
}

switch (flow) {
  case 'palette': {
    await go('/')
    await page.keyboard.press('Control+k')
    await page.waitForTimeout(300)
    await shot('palette-open')
    await page.keyboard.type('crit')
    await page.waitForTimeout(300)
    await shot('palette-search')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(800)
    console.log('palette navigated to', new URL(page.url()).pathname)
    break
  }
  case 'help': {
    await go('/')
    await page.keyboard.press('Shift+?')
    await page.waitForTimeout(300)
    await shot('help')
    await page.keyboard.press('Escape')
    await page.keyboard.press('g')
    await page.keyboard.press('r')
    await page.waitForTimeout(600)
    console.log('g r went to', new URL(page.url()).pathname)
    break
  }
  case 'mobile': {
    for (const p of ['/', '/review', '/missions/' + (process.env.MISSION ?? '')]) {
      await go(p)
      await shot('mobile' + (p.replace(/[/]+/g, '_').replace(/_$/, '') || '_home'), true)
    }
    break
  }
  case 'review-single': {
    // Approve the selected write with the keyboard, then reject the next one with a reason.
    await go('/review')
    const title = await page.locator('main h1, main h2').first().textContent()
    await page.keyboard.press('Control+Enter')
    await page.waitForTimeout(1500)
    await shot('review-after-approve')
    console.log('approved', title)
    await page.keyboard.press('x')
    await page.waitForTimeout(300)
    await page.getByRole('dialog').getByRole('textbox').first().fill('Duplicate of an existing SECOPS ticket.')
    await shot('review-reject-dialog')
    await page.getByRole('dialog').getByRole('button', { name: /^Reject/ }).click()
    await page.waitForTimeout(1500)
    await shot('review-after-reject')
    break
  }
  case 'review-batch': {
    await go('/review')
    const boxes = page.getByRole('checkbox', { name: /Select/ })
    const n = await boxes.count()
    for (let i = 0; i < n; i++) await boxes.nth(i).check()
    await page.waitForTimeout(400)
    await shot('review-batch-selected')
    await page.getByRole('button', { name: /^Approve \d+/ }).first().click()
    await page.waitForTimeout(600)
    await shot('review-batch-confirm')
    const confirm = page.getByRole('dialog').getByRole('button', { name: /^Approve/ })
    if (await confirm.count()) await confirm.first().click()
    await page.waitForTimeout(2000)
    await shot('review-batch-done')
    break
  }
  case 'atlas': {
    // Duplicate the Ticket Manager as "Atlas", then mention @atlas in the composer.
    await go('/agents/new?from=' + (process.env.AGENT ?? ''))
    await page.getByLabel('Name your team uses').fill('Atlas')
    await shot('atlas-builder')
    await page.getByRole('button', { name: /^Create Atlas/ }).click()
    await page.waitForTimeout(1500)
    await shot('atlas-created')
    await go('/missions/new')
    await page.getByRole('combobox', { name: 'What should Z do?' }).click()
    await page.keyboard.type('Clean the SECOPS backlog with @atl')
    await page.waitForTimeout(400)
    await shot('atlas-mention')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(300)
    console.log('composer text:', await page.getByRole('combobox', { name: 'What should Z do?' }).inputValue())
    break
  }
  default:
    console.error('unknown flow', flow)
}
if (problems.length) console.log('console problems:', problems.join(' | ').slice(0, 800))
await browser.close()
