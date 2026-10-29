/** Formatting for the dense, tabular UI. Pure functions, tested. */

export function compactNumber(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const abs = Math.abs(n)
  const fmt = (v: number, suffix: string) => {
    const s = v.toFixed(v >= 100 ? 0 : digits)
    return `${s.replace(/\.0+$/, '')}${suffix}`
  }
  if (abs >= 1e9) return fmt(n / 1e9, 'B')
  if (abs >= 1e6) return fmt(n / 1e6, 'M')
  if (abs >= 1e3) return fmt(n / 1e3, 'k')
  return String(Math.round(n))
}

export function grouped(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return Math.round(n).toLocaleString('en-US')
}

export function percent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return '—'
  return `${(ratio * 100).toFixed(digits)}%`
}

export function ms(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n >= 10_000) return `${(n / 1000).toFixed(0)} s`
  if (n >= 1000) return `${(n / 1000).toFixed(1)} s`
  return `${Math.round(n)} ms`
}

/** Elapsed time as m:ss or h:mm:ss (the mockups' "18:04"). */
export function elapsed(fromIso: string | null | undefined, toIso?: string | null, now = Date.now()): string {
  if (!fromIso) return '—'
  const start = Date.parse(fromIso)
  const end = toIso ? Date.parse(toIso) : now
  return clockDuration(Math.max(0, (end - start) / 1000))
}

export function clockDuration(totalSeconds: number): string {
  const s = Math.floor(totalSeconds % 60)
  const m = Math.floor((totalSeconds / 60) % 60)
  const h = Math.floor(totalSeconds / 3600)
  const pad = (v: number) => String(v).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

export function humanDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  if (seconds < 86400) {
    const h = seconds / 3600
    return `${h < 10 ? h.toFixed(1).replace(/\.0$/, '') : Math.round(h)} h`
  }
  return `${Math.round(seconds / 86400)} d`
}

/** "now", "4 min ago", "2 h ago", "yesterday", "3 days ago", or a date. */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const diff = (now - t) / 1000
  if (diff < 0) return future(-diff)
  if (diff < 45) return 'now'
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  if (diff < 172800) return 'yesterday'
  if (diff < 30 * 86400) return `${Math.round(diff / 86400)} days ago`
  return shortDate(iso)
}

function future(diff: number): string {
  if (diff < 60) return 'in a moment'
  if (diff < 3600) return `in ${Math.round(diff / 60)} min`
  if (diff < 86400) return `in ${Math.round(diff / 3600)} h`
  return `in ${Math.round(diff / 86400)} days`
}

export function shortAge(seconds: number): string {
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`
  return `${Math.round(seconds / 86400)} d`
}

export function clock(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}${withSeconds ? `:${pad(d.getSeconds())}` : ''}`
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })} ${clock(iso, true)}`
}

export function isToday(iso: string | null | undefined, now = new Date()): boolean {
  if (!iso) return false
  const d = new Date(iso)
  return (
    d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  )
}

export function greeting(now = new Date()): string {
  const h = now.getHours()
  if (h < 5) return 'Good evening'
  if (h < 12) return 'Good morning'
  if (h < 18) return 'Good afternoon'
  return 'Good evening'
}

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten']

/** "Five agents are working." Words up to ten, digits after. */
export function countWord(n: number, capital = true): string {
  const w = n <= 10 ? (WORDS[n] ?? String(n)) : String(n)
  return capital ? w : w.toLowerCase()
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function initials(name: string): string {
  const parts = name
    .trim()
    .split(/[\s._-]+/)
    .filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return (parts[0] ?? '?').slice(0, 2).toUpperCase()
  return ((parts[0]?.[0] ?? '') + (parts[parts.length - 1]?.[0] ?? '')).toUpperCase()
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(1)} GB`
}

/** Short hash for display: sha256:9f2c…41ab */
export function shortHash(hash: string | null | undefined): string {
  if (!hash) return '—'
  const h = hash.replace(/^sha256:/, '')
  return h.length > 12 ? `sha256:${h.slice(0, 4)}…${h.slice(-4)}` : `sha256:${h}`
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
