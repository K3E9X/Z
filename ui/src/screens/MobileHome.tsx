import { Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { canQuickApprove, useReviewActions } from '@/api/review-actions'
import { useMissions, useReview } from '@/api/queries'
import type { ReviewItem } from '@/api/types'
import { useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { reviewItemId, reviewItemTitle } from '@/components/DecisionRow'
import { Icon } from '@/components/Icon'
import { EmptyState } from '@/components/states'
import { Button } from '@/components/ui'
import { useCan, useCurrentMe } from '@/lib/auth'
import { countWord, greeting } from '@/lib/format'
import { missionAgent, nowLine, progress } from '@/lib/mission'

function DecisionCard({ item, agents }: { item: ReviewItem; agents: AgentIndex }) {
  const actions = useReviewActions()
  const canApprove = useCan('actions:approve')
  const canAnswer = useCan('questions:answer')
  const [busy, setBusy] = useState<string | null>(null)
  const id = reviewItemId(item)
  const agent = item.action?.agent ?? item.question?.agent ?? item.suggestion?.agent
  const kind = item.action ? `${item.action.connector} ${item.action.class}` : item.kind
  const run = (key: string, p: Promise<unknown>) => {
    setBusy(key)
    p.catch(() => undefined).finally(() => {
      setBusy(null)
    })
  }
  const q = item.question
  return (
    <article className="flex flex-col gap-3 rounded-card border border-line bg-panel p-4">
      <div className="text-meta text-wait">
        {agents.label(agent)} · {kind}
      </div>
      <div className="text-[15px] font-medium">{item.question ? item.question.text : reviewItemTitle(item)}</div>
      <div className="grid grid-cols-2 gap-2">
        {item.action && (
          <>
            <Link
              to="/review"
              search={{ item: id }}
              className="flex h-11 items-center justify-center rounded-btn border border-line-2 no-underline"
            >
              Review
            </Link>
            {canApprove && canQuickApprove(item.action) ? (
              <Button
                variant="primary"
                size="lg"
                className="h-11"
                busy={busy === 'approve'}
                onClick={() => {
                  if (item.action) run('approve', actions.approve(item.action))
                }}
              >
                Approve
              </Button>
            ) : (
              <span className="flex items-center justify-center text-meta text-text-3">
                {item.action.class === 'destructive' ? 'Two approvers, in the app' : 'Open to approve'}
              </span>
            )}
          </>
        )}
        {q &&
          (canAnswer && q.options.length >= 1 && q.options.length <= 2 ? (
            [...q.options].reverse().map((opt, i) => (
              <Button
                key={opt}
                size="lg"
                className="h-11 whitespace-normal"
                variant={i === q.options.length - 1 ? 'primary' : 'secondary'}
                busy={busy === opt}
                onClick={() => {
                  run(opt, actions.answer(q.id, opt))
                }}
              >
                {opt}
              </Button>
            ))
          ) : (
            <Link to="/review" search={{ item: id }} className="col-span-2 flex h-11 items-center justify-center rounded-btn border border-line-2 no-underline">
              Answer
            </Link>
          ))}
        {item.suggestion && (
          <Link to="/review" search={{ item: id }} className="col-span-2 flex h-11 items-center justify-center rounded-btn border border-line-2 no-underline">
            Review suggestion
          </Link>
        )}
      </div>
    </article>
  )
}

/** Phone layout of Mission control (docs/09 screen 14): decisions, running list, Ask Z. */
export function MobileHome() {
  const me = useCurrentMe()
  const navigate = useNavigate()
  const agents = useAgentIndex()
  const review = useReview('all')
  const active = useMissions({ status: 'queued,running,waiting,paused' }, { live: true })
  const [ask, setAsk] = useState('')
  const decisions = review.data?.items ?? []
  const running = active.data?.items ?? []
  const working = running.filter((m) => m.status === 'running' || m.status === 'waiting').length
  const canStart = useCan('missions:start')

  return (
    <main className="flex min-h-screen flex-col gap-6 px-5 pt-1 pb-28">
      {/* The mobile bar above already carries the mark and the menu. */}
      <div className="flex items-center justify-end">
        <span className="flex items-center gap-2 text-meta text-text-2">
          <span className="h-1.5 w-1.5 rounded-full bg-run" aria-hidden="true" />
          {working} {working === 1 ? 'mission' : 'missions'} running
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <h1 className="m-0 font-display-face text-[38px] leading-[1.05]">{greeting()}.</h1>
        <p className="m-0 text-text-2">
          {decisions.length === 0
            ? `Nothing is waiting for you, ${me.user.name.split(' ')[0] ?? ''}.`
            : `${countWord(decisions.length)} ${decisions.length === 1 ? 'decision is' : 'decisions are'} waiting for you.`}
        </p>
      </div>
      <section aria-labelledby="m-decisions" className="flex flex-col gap-3">
        <h2 id="m-decisions" className="m-0 text-ui font-medium text-text-2">
          Decisions
        </h2>
        <div aria-live="polite" className="flex flex-col gap-3">
          {decisions.length === 0 && <EmptyState title="All clear">Agents will ask here.</EmptyState>}
          {decisions.slice(0, 6).map((d) => (
            <DecisionCard key={reviewItemId(d)} item={d} agents={agents} />
          ))}
        </div>
      </section>
      <section aria-labelledby="m-running" className="flex flex-col">
        <h2 id="m-running" className="m-0 pb-1 text-ui font-medium text-text-2">
          Running
        </h2>
        {running.length === 0 && <div className="py-3 text-meta text-text-3">No mission running.</div>}
        {running.map((m) => {
          const p = progress(m)
          return (
            <Link
              key={m.id}
              to="/missions/$id"
              params={{ id: m.id }}
              className="flex items-start justify-between gap-3 border-b border-line py-3.5 no-underline"
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-[15px] font-medium">{m.title}</span>
                <span className="truncate text-meta text-text-3">
                  {agents.label(missionAgent(m))} · {nowLine(m).text}
                </span>
              </span>
              <span className="font-mono text-sm text-text-2">
                {p.done}/{p.total}
              </span>
            </Link>
          )
        })}
      </section>
      {canStart && (
        <form
          className="fixed right-4 bottom-4 left-4 flex items-center gap-2 rounded-card border border-line-2 bg-panel p-2 pl-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (ask.trim()) void navigate({ to: '/missions/new', search: { prompt: ask.trim() } })
          }}
        >
          <label htmlFor="ask-z" className="sr-only">
            Ask Z
          </label>
          <input
            id="ask-z"
            value={ask}
            onChange={(e) => {
              setAsk(e.target.value)
            }}
            placeholder="Ask Z…"
            className="h-10 min-w-0 flex-1 border-none bg-transparent text-[15px] outline-none"
          />
          <button type="submit" aria-label="Send" className="flex h-11 w-11 items-center justify-center rounded-btn bg-inv-bg text-inv-text">
            <Icon name="arrowUp" size={16} />
          </button>
        </form>
      )}
    </main>
  )
}
