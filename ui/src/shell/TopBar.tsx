import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { isNotServed } from '@/api/problem'
import { useReview } from '@/api/queries'
import { Icon } from '@/components/Icon'
import { Dot } from '@/components/StatusMark'
import { compactNumber, percent, shortAge } from '@/lib/format'
import { useShellData } from './useShellData'

export interface Crumb {
  label: string
  to?: string
}

/** Live gateway indicator: `inferX · tokens today · budget` (docs/09 §3). */
export function GatewayIndicator() {
  const d = useShellData()
  if (!d.health) {
    return (
      <Link to="/inference" className="flex items-center gap-2 font-mono text-xs text-text-3 no-underline">
        <Dot tone="idle" />
        {isNotServed(d.healthError) ? 'inference status not served yet' : 'inference status…'}
      </Link>
    )
  }
  const tone =
    d.health.status === 'healthy'
      ? 'ok'
      : d.health.status === 'down'
        ? 'err'
        : d.health.status === 'unknown'
          ? 'idle'
          : 'wait'
  const parts = ['inferX', `${compactNumber(d.health.tokens_today)} tokens today`]
  if (d.budgetPct !== null) parts.push(`${percent(d.budgetPct)} of budget`)
  return (
    <Link
      to="/inference"
      className="flex items-center gap-2 font-mono text-xs text-text-2 no-underline hover:text-text"
      aria-label={`Inference ${d.health.status}: ${parts.slice(1).join(', ')}`}
    >
      <Dot tone={tone} />
      {parts.join(' · ')}
    </Link>
  )
}

function Notifications() {
  const [open, setOpen] = useState(false)
  const review = useReview('all')
  const ref = useRef<HTMLDivElement>(null)
  const count = review.data?.counts.all ?? 0
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={count ? `Notifications, ${count} waiting for you` : 'Notifications'}
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v)
        }}
        className="relative flex h-8 w-8 items-center justify-center rounded-btn text-text-2 hover:bg-raised hover:text-text"
      >
        <Icon name="bell" size={16} />
        {count > 0 && (
          <span className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-wait" aria-hidden="true" />
        )}
      </button>
      {open && (
        <div className="absolute top-full right-0 z-40 mt-1 w-[360px] rounded-panel border border-line-2 bg-raised p-1.5">
          <div className="px-2.5 pt-1.5 pb-2 text-meta text-text-3">Waiting for you</div>
          {(review.data?.items ?? []).slice(0, 6).map((it) => {
            const id = it.action?.id ?? it.question?.id ?? it.suggestion?.id ?? ''
            const title = it.action?.summary ?? it.question?.text ?? it.suggestion?.title ?? ''
            return (
              <Link
                key={id}
                to="/review"
                search={{ item: id }}
                onClick={() => {
                  setOpen(false)
                }}
                className="flex flex-col rounded-ctl px-2.5 py-2 no-underline hover:bg-panel"
              >
                <span className="truncate">{title}</span>
                <span className="text-sm text-text-3">
                  {it.kind} · {it.mission_title} · {shortAge(it.age_s)}
                </span>
              </Link>
            )
          })}
          {count === 0 && (
            <div className="px-2.5 pb-2 text-meta text-text-3">Nothing is waiting. Agents will ask here.</div>
          )}
          <Link
            to="/review"
            onClick={() => {
              setOpen(false)
            }}
            className="mt-1 block border-t border-line px-2.5 pt-2 pb-1 text-meta text-text-2 no-underline"
          >
            Open Review
          </Link>
        </div>
      )}
    </div>
  )
}

export function TopBar({ crumbs, right }: { crumbs: Crumb[]; right?: ReactNode }) {
  return (
    <header className="sticky top-0 z-20 flex h-[52px] flex-none items-center gap-4 border-b border-line bg-bg px-7">
      <nav aria-label="Breadcrumb" className="min-w-0 flex-1 truncate text-ui text-text-3">
        {crumbs.map((c, i) => (
          <span key={i}>
            {i > 0 && <span className="mx-1.5">/</span>}
            {c.to && i < crumbs.length - 1 ? (
              <Link to={c.to} className="text-text-3 no-underline hover:text-text">
                {c.label}
              </Link>
            ) : (
              <span
                className={i === crumbs.length - 1 ? 'text-text' : ''}
                aria-current={i === crumbs.length - 1 ? 'page' : undefined}
              >
                {c.label}
              </span>
            )}
          </span>
        ))}
      </nav>
      {right ?? (
        <>
          <GatewayIndicator />
          <Notifications />
        </>
      )}
    </header>
  )
}
