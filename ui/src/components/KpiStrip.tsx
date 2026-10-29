import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import type { Tone } from '@/lib/status'
import { Dot } from './StatusMark'

export interface Kpi {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: Tone
  to?: string
  /** Shown instead of the value when the endpoint is not served yet. */
  unavailable?: boolean
}

/** A row of hairline-separated cells (Mission control, Inference, Learning loop). */
export function KpiStrip({ items, className = '', minWidth = 150 }: { items: Kpi[]; className?: string; minWidth?: number }) {
  return (
    <div
      role="list"
      className={`m-0 grid gap-px overflow-hidden rounded-panel border border-line bg-line ${className}`}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${minWidth}px, 1fr))` }}
    >
      {items.map((k) => {
        const body = (
          <>
            <span className="text-meta text-text-2">{k.label}</span>
            <span className="flex flex-col gap-1">
              {k.unavailable ? (
                <span className="text-[19px] leading-[1.3] text-text-3" title="Not available on this server yet">
                  —
                </span>
              ) : (
                <span className="flex items-center gap-2 text-[19px] leading-[1.3] font-medium tabular-nums">
                  {k.tone && <Dot tone={k.tone} />}
                  {k.value}
                </span>
              )}
              <span className="text-meta text-text-3">{k.unavailable ? 'Not available on this server yet' : k.sub}</span>
            </span>
          </>
        )
        const cls = 'flex flex-col gap-2 bg-bg px-4 py-4 no-underline'
        return k.to ? (
          <Link key={k.label} role="listitem" to={k.to} className={`${cls} hover:bg-panel`}>
            {body}
          </Link>
        ) : (
          <div key={k.label} role="listitem" className={cls}>
            {body}
          </div>
        )
      })}
    </div>
  )
}
