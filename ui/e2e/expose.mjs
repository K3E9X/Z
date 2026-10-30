// Confirms the exposure of one connector through the UI (Connectors → Review exposure).
import { chromium } from 'playwright-core'
const [name, ...ops] = process.argv.slice(2)
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
await page.goto('http://127.0.0.1:5173/connectors', { waitUntil: 'networkidle' })
await page.getByRole('row', { name: new RegExp(name) }).click()
await page.getByRole('button', { name: 'Review exposure' }).click()
const dialog = page.getByRole('dialog')
for (const op of ops) {
  const box = dialog.locator('label', { hasText: op }).locator('input[type=checkbox]')
  if (!(await box.isChecked())) await box.check()
}
await page.screenshot({ path: 'e2e/out/exposure-dialog.png' })
await dialog.getByRole('button', { name: 'Confirm exposure' }).click()
await page.waitForTimeout(1200)
await page.screenshot({ path: 'e2e/out/connectors-after-exposure.png' })
await browser.close()
