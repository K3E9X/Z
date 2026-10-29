import type { Tone } from '@/lib/status'
import { toneText } from '@/lib/status'

/**
 * Status is always color AND shape or text (docs/09 §2): running = spinning ring, waiting = filled dot
 * with a bang, succeeded = check, failed = cross, idle = hollow dot, budget = gauge.
 */
export function StatusMark({
  tone,
  label,
  size = 14,
  showLabel = false,
  className = '',
}: {
  tone: Tone
  label: string
  size?: number
  showLabel?: boolean
  className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} data-tone={tone}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
        className={`flex-none ${tone === 'run' ? 'animate-zspin' : ''}`}
      >
        {tone === 'run' && (
          <>
            <circle cx="8" cy="8" r="6" fill="none" stroke="var(--line-2)" strokeWidth="2" />
            <path
              d="M8 2a6 6 0 0 1 6 6"
              fill="none"
              stroke="var(--run)"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </>
        )}
        {tone === 'wait' && (
          <>
            <circle cx="8" cy="8" r="6" fill="var(--wait)" />
            <path d="M8 4.8v3.6M8 10.8v.01" stroke="var(--bg)" strokeWidth="1.8" strokeLinecap="round" />
          </>
        )}
        {tone === 'ok' && (
          <>
            <circle cx="8" cy="8" r="7" fill="var(--ok-bg)" />
            <path
              d="M5 8.2l2 2 4-4.2"
              fill="none"
              stroke="var(--ok)"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </>
        )}
        {tone === 'err' && (
          <>
            <circle cx="8" cy="8" r="7" fill="var(--err-bg)" />
            <path
              d="M5.5 5.5l5 5M10.5 5.5l-5 5"
              fill="none"
              stroke="var(--err)"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </>
        )}
        {tone === 'idle' && (
          <circle cx="8" cy="8" r="6" fill="none" stroke="var(--text-3)" strokeWidth="1.6" />
        )}
        {tone === 'budget' && (
          <>
            <path
              d="M2.5 11a5.5 5.5 0 1 1 11 0"
              fill="none"
              stroke="var(--line-2)"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
            <path
              d="M2.5 11a5.5 5.5 0 0 1 11 0"
              fill="none"
              stroke="var(--err)"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
            <path d="M8 11l3-3.5" stroke="var(--err)" strokeWidth="1.6" strokeLinecap="round" />
          </>
        )}
      </svg>
      {showLabel ? <span className={toneText[tone]}>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  )
}

/** The 6 px dot used inline (header indicator, KPI values, health cells). Pair it with text. */
export function Dot({
  tone,
  pulse = false,
  className = '',
}: {
  tone: Tone
  pulse?: boolean
  className?: string
}) {
  const bg: Record<Tone, string> = {
    run: 'bg-run',
    wait: 'bg-wait',
    ok: 'bg-ok',
    err: 'bg-err',
    idle: 'bg-text-3',
    budget: 'bg-err',
  }
  const hollow = tone === 'idle'
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-1.5 w-1.5 flex-none rounded-full ${hollow ? 'border border-text-3 bg-transparent' : bg[tone]} ${
        pulse ? 'animate-zpulse' : ''
      } ${className}`}
    />
  )
}

/** Dot + label, colored (e.g. "● Healthy"). */
export function StatusText({
  tone,
  label,
  className = '',
}: {
  tone: Tone
  label: string
  className?: string
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <Dot tone={tone} />
      <span className={tone === 'idle' ? 'text-text-2' : toneText[tone]}>{label}</span>
    </span>
  )
}
