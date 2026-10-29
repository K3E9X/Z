import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useReviewActions } from '@/api/review-actions'
import { useMission, useReview, type ReviewKind } from '@/api/queries'
import type { ReviewItem } from '@/api/types'
import { ActionReview } from '@/components/ActionReview'
import { useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { reviewItemId } from '@/components/DecisionRow'
import { Icon } from '@/components/Icon'
import { QuestionCard } from '@/components/QuestionCard'
import { EmptyState, ErrorState } from '@/components/states'
import { Button, Checkbox, Kbd, Skeleton, Tabs } from '@/components/ui'
import { JsonView } from '@/components/previews'
import { useCan } from '@/lib/auth'
import { compactNumber, relative, shortAge } from '@/lib/format'
import { MOD, useHotkeys } from '@/lib/hotkeys'
import { playbookTitle } from '@/lib/mission'
import { useIsMobile } from '@/lib/useMedia'

function kindLine(i: ReviewItem): string {
  if (i.action) return `${i.action.connector} · ${i.action.class}`
  if (i.question) return 'Question'
  return 'Suggested mission'
}

function ListRow({
  item,
  selected,
  agents,
  onSelect,
  checked,
  onCheck,
}: {
  item: ReviewItem
  selected: boolean
  agents: AgentIndex
  onSelect: () => void
  checked?: boolean
  onCheck?: (v: boolean) => void
}) {
  const title = item.action?.summary ?? item.question?.text ?? item.suggestion?.title ?? ''
  const agent = item.action?.agent ?? item.question?.agent ?? item.suggestion?.agent
  return (
    <li className="relative">
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        className={`flex w-full flex-col gap-0.5 rounded-panel px-3 py-2.5 pr-10 text-left ${selected ? 'bg-raised' : 'hover:bg-panel'}`}
      >
        <span className="flex items-center justify-between gap-2 text-sm text-text-3">
          <span className={item.action?.class === 'destructive' ? 'text-err' : ''}>{kindLine(item)}</span>
          <span className="font-mono">{shortAge(item.age_s)}</span>
        </span>
        <span className="line-clamp-2 font-medium">{title}</span>
        <span className="text-meta text-text-3">{agents.label(agent)}</span>
      </button>
      {onCheck && (
        <input
          type="checkbox"
          className="absolute right-3 bottom-3 h-[14px] w-[14px]"
          checked={!!checked}
          aria-label={`Select “${title}” for batch approval`}
          onChange={(e) => {
            onCheck(e.target.checked)
          }}
        />
      )}
    </li>
  )
}

function QuestionDetail({ item, agents }: { item: ReviewItem; agents: AgentIndex }) {
  const q = item.question
  if (!q) return null
  return (
    <div className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex flex-col gap-1.5 border-b border-line px-8 pt-6 pb-5">
        <div className="text-meta text-text-3">
          Mission “{item.mission_title}” ·{' '}
          <Link
            to="/missions/$id"
            params={{ id: q.mission_id }}
            className="text-text-2 underline underline-offset-2"
          >
            open the mission
          </Link>
        </div>
        <h1 className="m-0 text-2xl font-semibold">{agents.label(q.agent)} has a question</h1>
        <div className="text-meta text-text-3">
          Asked {relative(q.created_at)} · the job is paused until you answer
        </div>
      </div>
      <div className="max-w-[820px] px-8 py-6">
        <QuestionCard
          id={q.id}
          text={q.text}
          options={q.options}
          answer={q.answer}
          answeredBy={q.answered_by}
        />
      </div>
    </div>
  )
}

function SuggestionDetail({
  item,
  agents,
  onDecided,
}: {
  item: ReviewItem
  agents: AgentIndex
  onDecided: () => void
}) {
  const s = item.suggestion
  const actions = useReviewActions()
  const canStart = useCan('missions:start')
  const [busy, setBusy] = useState<string | null>(null)
  if (!s) return null
  const run = (accept: boolean) => {
    setBusy(accept ? 'accept' : 'dismiss')
    actions
      .suggestion(s.id, accept)
      .then(onDecided)
      .catch(() => undefined)
      .finally(() => {
        setBusy(null)
      })
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-1.5 border-b border-line px-8 pt-6 pb-5">
          <div className="text-meta text-text-3">
            Suggested by {agents.label(s.agent)} · {relative(s.created_at)}
            {s.objective_id ? ' · from an objective' : ''}
          </div>
          <h1 className="m-0 text-2xl font-semibold">{s.title}</h1>
        </div>
        <div className="flex max-w-[820px] flex-col gap-4 px-8 py-6">
          <p className="m-0 whitespace-pre-wrap text-text-2">{s.explanation}</p>
          <dl className="m-0 grid grid-cols-[120px_1fr] gap-y-2 text-ui">
            <dt className="text-text-3">Playbook</dt>
            <dd className="m-0">{s.playbook ? playbookTitle(s.playbook) : 'Free-form plan'}</dd>
            <dt className="text-text-3">Estimated cost</dt>
            <dd className="m-0 font-mono">
              {s.estimated_tokens ? `${compactNumber(s.estimated_tokens)} tokens` : '—'}
            </dd>
            <dt className="text-text-3">Writes</dt>
            <dd className="m-0">
              {s.has_writes ? 'Yes, each one will still need your approval' : 'None, read only'}
            </dd>
          </dl>
          {Object.keys(s.inputs).length > 0 && <JsonView data={s.inputs} />}
        </div>
      </div>
      {canStart && (
        <div className="flex justify-end gap-2 border-t border-line px-8 py-4">
          <Button busy={busy === 'dismiss'} onClick={() => run(false)}>
            Dismiss
          </Button>
          <Button variant="primary" busy={busy === 'accept'} onClick={() => run(true)}>
            Start the mission
          </Button>
        </div>
      )}
    </div>
  )
}

function ActionDetail({
  item,
  agents,
  onDecided,
}: {
  item: ReviewItem
  agents: AgentIndex
  onDecided: () => void
}) {
  const mission = useMission(item.action?.mission_id ?? '')
  if (!item.action) return null
  return (
    <ActionReview
      key={item.action.id}
      action={item.action}
      mission={mission.data ?? null}
      agents={agents}
      onDecided={onDecided}
    />
  )
}

export function Review() {
  const search = useSearch({ from: '/review' })
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  const agents = useAgentIndex()
  const kind = (
    ['all', 'writes', 'questions', 'suggestions'].includes(search.kind ?? '') ? search.kind : 'all'
  ) as ReviewKind
  const review = useReview(kind)
  const all = useReview('all')
  const actions = useReviewActions()
  const canApprove = useCan('actions:approve')
  const [checked, setChecked] = useState<Set<string>>(() => new Set())
  const [batchBusy, setBatchBusy] = useState(false)
  const items = useMemo(() => review.data?.items ?? [], [review.data])
  const counts = all.data?.counts ?? review.data?.counts
  const selectedId =
    search.item && items.some((i) => reviewItemId(i) === search.item)
      ? search.item
      : isMobile
        ? null
        : items[0]
          ? reviewItemId(items[0])
          : null
  const selected = items.find((i) => reviewItemId(i) === selectedId) ?? null
  const batchable = items.filter(
    (i) =>
      i.action &&
      i.action.status === 'pending' &&
      i.action.class !== 'destructive' &&
      i.action.required_approvals <= 1,
  )
  const chosen = batchable.filter((i) => checked.has(reviewItemId(i)))

  const select = (id: string | null) => {
    void navigate({
      to: '/review',
      search: { kind: kind === 'all' ? undefined : kind, item: id ?? undefined },
      replace: true,
    })
  }
  const move = (d: 1 | -1) => {
    const idx = items.findIndex((i) => reviewItemId(i) === selectedId)
    const next = items[Math.max(0, Math.min(items.length - 1, idx + d))]
    if (next) select(reviewItemId(next))
  }
  const afterDecision = () => {
    // Optimistic removal already happened; select what is now in the same place.
    const idx = items.findIndex((i) => reviewItemId(i) === selectedId)
    const rest = items.filter((i) => reviewItemId(i) !== selectedId)
    const next = rest[Math.min(idx, rest.length - 1)]
    select(next ? reviewItemId(next) : null)
  }
  useHotkeys({ j: () => move(1), k: () => move(-1), arrowdown: () => move(1), arrowup: () => move(-1) })

  const list = (
    <section
      aria-label="Waiting for review"
      className="flex min-h-0 w-full flex-col border-line md:w-[320px] md:flex-none md:border-r"
    >
      <div className="flex flex-col gap-3.5 px-5 pt-6 pb-3">
        <h1 className="m-0 text-xl font-semibold">Review</h1>
        <Tabs<ReviewKind>
          label="Filter"
          variant="pill"
          value={kind}
          onChange={(k) => {
            void navigate({ to: '/review', search: { kind: k === 'all' ? undefined : k }, replace: true })
          }}
          items={[
            { id: 'all', label: 'All', badge: counts?.all },
            { id: 'writes', label: 'Writes', badge: counts?.writes },
            { id: 'questions', label: 'Questions', badge: counts?.questions },
            ...(counts?.suggestions
              ? [{ id: 'suggestions' as const, label: 'Suggested', badge: counts.suggestions }]
              : []),
          ]}
        />
      </div>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2.5" aria-live="polite">
        {review.error ? (
          <ErrorState error={review.error} className="m-2.5" />
        ) : review.isPending ? (
          <div className="flex flex-col gap-2 p-2.5">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState title="Nothing to review" className="px-3">
            Agents propose every write here before anything changes in your tools.
          </EmptyState>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 pb-3">
            {items.map((it) => {
              const id = reviewItemId(it)
              const canBatch = canApprove && batchable.includes(it)
              return (
                <ListRow
                  key={id}
                  item={it}
                  agents={agents}
                  selected={id === selectedId}
                  onSelect={() => select(id)}
                  checked={checked.has(id)}
                  onCheck={
                    canBatch
                      ? (v) => {
                          setChecked((cur) => {
                            const n = new Set(cur)
                            if (v) n.add(id)
                            else n.delete(id)
                            return n
                          })
                        }
                      : undefined
                  }
                />
              )
            })}
          </ul>
        )}
      </div>
      {chosen.length > 1 ? (
        <div className="flex items-center gap-2 border-t border-line px-5 py-3">
          <Checkbox
            checked={chosen.length === batchable.length}
            onChange={(v) => {
              setChecked(v ? new Set(batchable.map(reviewItemId)) : new Set())
            }}
            label={<span className="text-meta">All</span>}
          />
          <Button
            variant="primary"
            size="sm"
            className="ml-auto"
            busy={batchBusy}
            onClick={() => {
              setBatchBusy(true)
              actions
                .batchApprove(chosen.map((c) => c.action).filter((a) => a !== undefined))
                .then(() => {
                  setChecked(new Set())
                })
                .catch(() => undefined)
                .finally(() => {
                  setBatchBusy(false)
                })
            }}
          >
            Approve {chosen.length} writes
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-5 py-3 text-sm text-text-3">
          <span className="flex items-center gap-1">
            <Kbd>J</Kbd>
            <Kbd>K</Kbd> move
          </span>
          <span className="flex items-center gap-1">
            <Kbd>{MOD}↵</Kbd> approve
          </span>
          <span className="flex items-center gap-1">
            <Kbd>X</Kbd> reject
          </span>
        </div>
      )}
    </section>
  )

  const detail = selected ? (
    selected.kind === 'action' ? (
      <ActionDetail item={selected} agents={agents} onDecided={afterDecision} />
    ) : selected.kind === 'question' ? (
      <QuestionDetail item={selected} agents={agents} />
    ) : (
      <SuggestionDetail item={selected} agents={agents} onDecided={afterDecision} />
    )
  ) : (
    <div className="flex flex-1 items-center justify-center p-10">
      <EmptyState icon="review" title={items.length ? 'Select an item' : 'All caught up'}>
        {items.length
          ? 'Use J and K to move through the list.'
          : 'New writes and questions appear here live.'}
      </EmptyState>
    </div>
  )

  if (isMobile) {
    return (
      <main className="flex min-h-[calc(100vh-48px)] flex-col">
        {selected ? (
          <>
            <button
              type="button"
              className="flex items-center gap-2 px-5 py-3 text-meta text-text-2"
              onClick={() => select(null)}
            >
              <Icon name="chevronRight" size={12} className="rotate-180" /> All items
            </button>
            {detail}
          </>
        ) : (
          list
        )}
      </main>
    )
  }
  return (
    <main className="flex h-screen min-h-0">
      {list}
      <div className="flex min-w-0 flex-1 flex-col">{detail}</div>
    </main>
  )
}
