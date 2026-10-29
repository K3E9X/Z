const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

/** Plain English for the common cron shapes ("0 8 * * 1" → "Every Monday, 8:00"); the raw expression otherwise. */
export function describeCron(expr: string | undefined | null): string {
  if (!expr) return 'On demand'
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr
  const [m, h, dom, mon, dow] = parts as [string, string, string, string, string]
  const time = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${Number(h)}:${m.padStart(2, '0')}` : null
  if (time && dom === '*' && mon === '*') {
    if (dow === '*') return `Every day, ${time}`
    if (dow === '1-5') return `Weekdays, ${time}`
    if (/^\d$/.test(dow)) return `Every ${DAYS[Number(dow) % 7] ?? dow}, ${time}`
  }
  if (time && /^\d+$/.test(dom) && mon === '*' && dow === '*') {
    const d = Number(dom)
    const suffix = d === 1 || d === 21 || d === 31 ? 'st' : d === 2 || d === 22 ? 'nd' : d === 3 || d === 23 ? 'rd' : 'th'
    return `${d}${suffix} of every month, ${time}`
  }
  if (m === '0' && /^\*\/\d+$/.test(h) && dom === '*' && mon === '*' && dow === '*') return `Every ${h.slice(2)} hours`
  if (m === '0' && h === '*' && dom === '*' && mon === '*' && dow === '*') return 'Every hour'
  return expr
}
