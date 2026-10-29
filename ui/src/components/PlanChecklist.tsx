import type { PlanStep } from '@/api/types'
import { stepState } from '@/lib/timeline'

export function stepTitle(s: PlanStep): string {
  if (s.title) return s.title
  if (s.prompt) {
    const first = s.prompt.split('\n')[0] ?? s.prompt
    return first.length > 90 ? `${first.slice(0, 89)}…` : first
  }
  return s.id ?? 'Step'
}

function Mark({ state }: { state: ReturnType<typeof stepState> }) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" className="mt-[2px] flex-none">
      {state === 'done' && (
        <>
          <circle cx="8" cy="8" r="7" fill="var(--ok-bg)" />
          <path d="M5 8.2l2 2 4-4.2" fill="none" stroke="var(--ok)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {state === 'current' && (
        <>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="var(--run)" strokeWidth="1.6" />
          <circle cx="8" cy="8" r="2.4" fill="var(--run)" />
        </>
      )}
      {state === 'waiting' && (
        <>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="var(--wait)" strokeWidth="1.6" />
          <circle cx="8" cy="8" r="2.4" fill="var(--wait)" />
        </>
      )}
      {state === 'failed' && (
        <>
          <circle cx="8" cy="8" r="7" fill="var(--err-bg)" />
          <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="var(--err)" strokeWidth="1.6" strokeLinecap="round" />
        </>
      )}
      {state === 'pending' && <circle cx="8" cy="8" r="6.2" fill="none" stroke="var(--line-2)" strokeWidth="1.6" />}
    </svg>
  )
}

const STATE_LABEL = { done: 'done', current: 'in progress', waiting: 'waiting', failed: 'failed', pending: 'not started' }

/** The plan as a checklist (Mission control preview, Agent at work). */
export function PlanChecklist({
  steps,
  agentLabel,
  className = '',
  showAgents = false,
}: {
  steps: PlanStep[]
  agentLabel?: (slug: string | undefined) => string
  className?: string
  showAgents?: boolean
}) {
  return (
    <ol className={`m-0 flex list-none flex-col gap-2.5 p-0 ${className}`} aria-label="Plan">
      {steps.map((s, i) => {
        const state = stepState(s)
        const j = s.jobs
        return (
          <li key={s.id ?? i} className={`flex gap-2.5 ${state === 'current' || state === 'waiting' ? 'text-text' : 'text-text-3'}`}>
            <Mark state={state} />
            <span className="flex min-w-0 flex-col">
              <span className={state === 'current' ? 'font-medium' : ''}>
                {stepTitle(s)}
                <span className="sr-only"> ({STATE_LABEL[state]})</span>
              </span>
              {(showAgents || (j?.total ?? 0) > 1) && (
                <span className="text-sm text-text-3">
                  {showAgents && s.agent && agentLabel ? agentLabel(s.agent) : null}
                  {showAgents && s.agent && (j?.total ?? 0) > 1 ? ' · ' : null}
                  {(j?.total ?? 0) > 1 ? `${j?.succeeded ?? 0} of ${j?.total ?? 0}` : null}
                  {(j?.failed ?? 0) > 0 ? <span className="text-err"> · {j?.failed} failed</span> : null}
                </span>
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
