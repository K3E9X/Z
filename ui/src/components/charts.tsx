/**
 * Charts on the design tokens (series --s1/--s2/--s3, validated for CVD in night and day).
 * Thin marks, 2px surface gaps between stacked segments, 4px rounded data ends, recessive grid,
 * a legend for >= 2 series, hover tooltips, an aria-label summary and a table view (docs/09 §8).
 */
import { useId, useMemo, useState } from 'react'
import { compactNumber, grouped } from '@/lib/format'

export const SERIES = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--text-3)'] as const

export interface StackSeries {
  key: string
  label: string
  values: number[]
}

function niceMax(v: number): number {
  if (v <= 0) return 1
  const exp = 10 ** Math.floor(Math.log10(v))
  const f = v / exp
  const nice = f <= 1 ? 1 : f <= 1.5 ? 1.5 : f <= 2 ? 2 : f <= 3 ? 3 : f <= 5 ? 5 : 10
  return nice * exp
}

/** Stacked columns over time buckets (tokens per hour by model). Series beyond 3 fold into "Other". */
export function StackedBars({
  labels,
  series,
  height = 240,
  title,
  showTable,
  formatTick = compactNumber,
}: {
  labels: string[]
  series: StackSeries[]
  height?: number
  title: string
  showTable?: boolean
  formatTick?: (n: number) => string
}) {
  const folded = useMemo(() => {
    if (series.length <= 3) return series
    const sorted = [...series].sort((a, b) => b.values.reduce((x, y) => x + y, 0) - a.values.reduce((x, y) => x + y, 0))
    const top = sorted.slice(0, 3)
    const rest = sorted.slice(3)
    return [...top, { key: 'other', label: 'Other', values: labels.map((_, i) => rest.reduce((n, s) => n + (s.values[i] ?? 0), 0)) }]
  }, [series, labels])
  const totals = labels.map((_, i) => folded.reduce((n, s) => n + (s.values[i] ?? 0), 0))
  const max = niceMax(Math.max(0, ...totals))
  const [hover, setHover] = useState<number | null>(null)
  const id = useId()
  const n = labels.length
  const W = 800
  const padL = 44
  const padB = 22
  const plotW = W - padL
  const plotH = height - padB
  const band = n > 0 ? plotW / n : plotW
  const barW = Math.min(24, Math.max(3, band * 0.7))
  const peakIdx = totals.indexOf(Math.max(...totals))
  const summary = `${title}: ${folded.map((s) => `${s.label} ${compactNumber(s.values.reduce((a, b) => a + b, 0))}`).join(', ')}${
    peakIdx >= 0 && n ? `; peak ${compactNumber(totals[peakIdx])} at ${labels[peakIdx] ?? ''}` : ''
  }.`
  const ticks = [0, max / 3, (2 * max) / 3, max]
  const labelEvery = Math.max(1, Math.ceil(n / 7))

  if (showTable) {
    return (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-ui">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr className="text-sm text-text-3">
              <th className="py-1.5 pr-4 font-normal">Time</th>
              {folded.map((s) => (
                <th key={s.key} className="py-1.5 pr-4 text-right font-normal">
                  {s.label}
                </th>
              ))}
              <th className="py-1.5 text-right font-normal">Total</th>
            </tr>
          </thead>
          <tbody>
            {labels.map((l, i) => (
              <tr key={`${l}-${i}`} className="border-t border-line font-mono text-[12.5px]">
                <td className="py-1.5 pr-4">{l}</td>
                {folded.map((s) => (
                  <td key={s.key} className="py-1.5 pr-4 text-right">
                    {grouped(s.values[i] ?? 0)}
                  </td>
                ))}
                <td className="py-1.5 text-right">{grouped(totals[i] ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${height}`} className="block h-auto w-full" role="img" aria-labelledby={`${id}-s`} onMouseLeave={() => setHover(null)}>
        <title id={`${id}-s`}>{summary}</title>
        {ticks.map((t) => {
          const y = plotH - (t / max) * plotH
          return (
            <g key={t}>
              <line x1={padL} x2={W} y1={y} y2={y} stroke="var(--line)" strokeWidth="1" />
              <text x={padL - 8} y={y + 4} textAnchor="end" fontSize="11" fill="var(--text-3)" fontFamily="var(--mono)">
                {formatTick(t)}
              </text>
            </g>
          )
        })}
        {labels.map((l, i) => {
          const x = padL + i * band + (band - barW) / 2
          let y = plotH
          const segs = folded.map((s, si) => {
            const v = s.values[i] ?? 0
            const h = (v / max) * plotH
            y -= h
            return { si, y, h, v }
          })
          const top = segs.filter((s) => s.h > 0).at(-1)
          return (
            <g key={`${l}-${i}`} onMouseEnter={() => setHover(i)}>
              <rect x={padL + i * band} y={0} width={band} height={plotH} fill="transparent" />
              {segs.map((s) => {
                if (s.h <= 0) return null
                const isTop = s === top
                const h = Math.max(0, s.h - (isTop ? 0 : 2))
                return isTop ? (
                  <path
                    key={s.si}
                    d={`M${x},${s.y + h} V${s.y + Math.min(4, h)} Q${x},${s.y} ${x + Math.min(4, barW / 2)},${s.y} H${x + barW - Math.min(4, barW / 2)} Q${x + barW},${s.y} ${x + barW},${s.y + Math.min(4, h)} V${s.y + h} Z`}
                    fill={SERIES[s.si] ?? 'var(--text-3)'}
                    opacity={hover === null || hover === i ? 1 : 0.55}
                  />
                ) : (
                  <rect key={s.si} x={x} y={s.y + 2} width={barW} height={h} fill={SERIES[s.si] ?? 'var(--text-3)'} opacity={hover === null || hover === i ? 1 : 0.55} />
                )
              })}
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={height - 4} textAnchor="middle" fontSize="11" fill="var(--text-3)" fontFamily="var(--mono)">
                  {l}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {hover !== null && (
        <div
          role="tooltip"
          className="pointer-events-none absolute top-2 z-10 min-w-[150px] rounded-btn border border-line-2 bg-raised px-3 py-2 text-sm"
          style={{ left: `min(calc(${((padL + hover * band + band) / W) * 100}% + 8px), calc(100% - 170px))` }}
        >
          <div className="mb-1 font-mono text-text-2">{labels[hover]}</div>
          {folded.map((s, si) => (
            <div key={s.key} className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-[2px]" style={{ background: SERIES[si] }} aria-hidden="true" />
              <span className="flex-1 text-text-2">{s.label}</span>
              <span className="font-mono">{compactNumber(s.values[hover] ?? 0)}</span>
            </div>
          ))}
          <div className="mt-1 flex justify-between border-t border-line pt-1 font-mono">
            <span className="text-text-3">total</span>
            {compactNumber(totals[hover] ?? 0)}
          </div>
        </div>
      )}
    </div>
  )
}

export function Legend({ items }: { items: { label: string; value?: string }[] }) {
  return (
    <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-sm">
      {items.map((it, i) => (
        <li key={it.label} className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-[2px]" style={{ background: SERIES[i] ?? 'var(--text-3)' }} aria-hidden="true" />
          <span className="font-mono">{it.label}</span>
          {it.value && <span className="font-mono text-text-2">{it.value}</span>}
        </li>
      ))}
    </ul>
  )
}

/** Horizontal bars, one series (tokens by agent): value at the bar end, sorted by the caller. */
export function HBars({ rows, max }: { rows: { label: string; value: number; sub?: string }[]; max?: number }) {
  const m = max ?? Math.max(1, ...rows.map((r) => r.value))
  return (
    <ul className="m-0 flex list-none flex-col gap-2.5 p-0" aria-label="Bars">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(0,140px)_1fr_52px] items-center gap-3">
          <span className="truncate" title={r.sub}>
            {r.label}
          </span>
          <span className="flex h-2 items-center">
            <span className="h-2 rounded-r-[4px] bg-s1" style={{ width: `${Math.max(1.5, (r.value / m) * 100)}%` }} aria-hidden="true" />
          </span>
          <span className="text-right font-mono text-[12.5px]">{compactNumber(r.value)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Trend line for objectives and KPIs; the last point carries a dot. */
export function Sparkline({ values, width = 96, height = 24, label, target }: { values: number[]; width?: number; height?: number; label: string; target?: number }) {
  if (values.length < 2) return <span className="text-meta text-text-3">—</span>
  const all = target !== undefined ? [...values, target] : values
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const span = hi - lo || 1
  const pts = values.map((v, i) => [2 + (i / (values.length - 1)) * (width - 6), height - 3 - ((v - lo) / span) * (height - 6)] as const)
  const last = pts[pts.length - 1]
  const ty = target !== undefined ? height - 3 - ((target - lo) / span) * (height - 6) : null
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="flex-none overflow-visible">
      {ty !== null && <line x1={0} x2={width} y1={ty} y2={ty} stroke="var(--line-2)" strokeWidth="1" />}
      <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke="var(--s1)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {last && <circle cx={last[0]} cy={last[1]} r="3" fill="var(--s1)" stroke="var(--panel)" strokeWidth="2" />}
    </svg>
  )
}

/** Used / cap bar (TokenMeter): turns wait at 80 %, err past the cap. */
export function TokenMeter({ used, cap, className = '', label }: { used: number; cap: number | null | undefined; className?: string; label?: string }) {
  const ratio = cap ? used / cap : 0
  const tone = ratio >= 1 ? 'bg-err' : ratio >= 0.8 ? 'bg-wait' : 'bg-text-2'
  return (
    <span
      role="meter"
      aria-valuemin={0}
      aria-valuemax={cap ?? undefined}
      aria-valuenow={used}
      aria-label={label ?? `${compactNumber(used)} of ${cap ? compactNumber(cap) : 'no cap'} tokens`}
      className={`flex h-[3px] overflow-hidden rounded-full bg-line-2 ${className}`}
    >
      <span className={tone} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
    </span>
  )
}
