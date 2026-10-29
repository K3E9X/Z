import { chromium } from 'playwright-core'
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await b.newPage()
const counts = {}
p.on('request', (r) => { const u = new URL(r.url()).pathname; counts[u] = (counts[u] ?? 0) + 1 })
await p.goto('http://127.0.0.1:5173' + process.argv[2], { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(4000)
console.log(Object.entries(counts).filter(([k]) => !k.startsWith('/src') && !k.startsWith('/node_modules') && !k.startsWith('/@')).sort((a,b)=>b[1]-a[1]).slice(0,10))
await b.close()
