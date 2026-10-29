import { chromium } from 'playwright-core'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
await page.goto('http://127.0.0.1:5173/review', { waitUntil: 'networkidle' })
await page.getByRole('button', { name: 'Yes, mark it failed' }).first().click()
await page.waitForTimeout(1500)
await page.screenshot({ path: 'e2e/out/review-after-answer.png' })
await browser.close()
