import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { useProposals, useSignals } from '@/api/queries'
import type { Proposal, Signal } from '@/api/types'
import { useAgentIndex } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { DiffViewer } from '@/components/DiffViewer'
import { EmptyState, NotServed, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Chip, PageTitle, Textarea } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { clock, plural } from '@/lib/format'
import { useNow } from '@/lib/useMedia'

const KIND: Record<string, string> = {
  skill_edit: 'Skill improvement',
  memory_fact: 'Memory',
  routing_rule: 'Routing',
  playbook_edit: 'Playbook',
}
const STATUS: Record<string, { label: string; tone: string }> = {
  observed: { label: 'Observed', tone: 'text-text-3' },
  proposed: { label: 'Proposed', tone: 'text-text-2' },
  evaluating: { label: 'Evaluating', tone: 'text-text-2' },
  ready: { label: 'Waiting', tone: 'text-wait' },
  discarded: { label: 'Discarded', tone: 'text-err' },
  applied: { label: 'Applied', tone: 'text-ok' },
  rejected: { label: 'Rejected', tone: 'text-text-3' },
  rolled_back: { label: 'Rolled back', tone: 'text-text-3' },
}
const SIGNAL: Record<string, string> = {
  action_edited: 'Ticket edited before approval',
  action_rejected: 'Write rejected',
  question_answered: 'Question answered',
  mission_reassigned: 'Mission reassigned',
  retry: 'Retried',
  repair: 'Output repaired',
  failure: 'Job failed',
  guardrail_hit: 'Guardrail hit',
  feedback: 'Feedback',
  connector_error: 'Connector error',
}

function signalDetail(s: Signal): string {
  const p = s.payload
  const bits = [
    typeof p.summary === 'string' ? p.summary : null,
    typeof p.reason === 'string' ? p.reason : null,
    typeof p.ref === 'string' ? p.ref : null,
  ].filter(Boolean)
  return bits.join(' · ')
}

function ProposalDetail({ p }: { p: Proposal }) {
  const canApply = useCan('learning:apply')
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const decide = async (what: 'apply' | 'reject' | 'try') => {
    setBusy(what)
    try {
      if (what === 'apply') {
        await unwrap(api.POST('/proposals/{id}/apply', { params: { path: { id: p.id } }, body: {} }))
        toast.ok('Applied as a new version. Roll back any time from the skill.')
      } else if (what === 'reject') {
        await unwrap(
          api.POST('/proposals/{id}/reject', {
            params: { path: { id: p.id } },
            body: reason ? { reason } : {},
          }),
        )
        toast.ok('Rejected')
        setRejecting(false)
      } else {
        const m = await unwrap(api.POST('/proposals/{id}/try', { params: { path: { id: p.id } }, body: {} }))
        toast.ok('Trying it on one mission, read-only')
        void navigate({ to: '/missions/$id', params: { id: m.id } })
      }
      await qc.invalidateQueries({ queryKey: ['proposals'] })
      await qc.invalidateQueries({ queryKey: ['skills'] })
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  const before = p.eval_before
  const after = p.eval_after
  const adds = (p.diff.match(/^\+(?!\+\+)/gm) ?? []).length
  const dels = (p.diff.match(/^-(?!--)/gm) ?? []).length
  const [target, version] = p.target.split('@')
  return (
    <article className="flex flex-col rounded-card border border-line bg-panel">
      <div className="flex flex-col gap-2 border-b border-line px-6 py-5">
        <div className="flex flex-wrap items-center gap-2.5 text-meta text-text-2">
          <Chip>{KIND[p.kind] ?? p.kind}</Chip>
          {p.evidence.length > 0 && <span>{plural(p.evidence.length, 'signal')}</span>}
          <span className="font-mono">
            {target}
            {version ? ` ${version}` : ''}
          </span>
        </div>
        <h2 className="m-0 text-[17px] font-semibold">{p.title}</h2>
        {p.rationale && <p className="m-0 text-text-2">{p.rationale}</p>}
      </div>
      <div className="flex flex-col gap-2 border-b border-line px-6 py-5">
        <div className="flex justify-between font-mono text-sm text-text-2">
          <span>{p.kind === 'skill_edit' ? `skills/${target}/SKILL.md` : target}</span>
          <span>
            +{adds} −{dels}
          </span>
        </div>
        <DiffViewer diff={p.diff} showFileHeader={false} lineNumbers={false} maxHeight={360} />
      </div>
      <dl className="m-0 grid grid-cols-2 gap-4 border-b border-line px-6 py-5 sm:grid-cols-4">
        {[
          ['Evals before', before ? `${before.cases_passed} / ${before.cases_total}` : '—', ''],
          [
            'Evals after',
            after
              ? `${after.cases_passed} / ${after.cases_total}`
              : p.status === 'evaluating'
                ? 'running…'
                : '—',
            after && before && after.cases_passed > before.cases_passed ? 'text-ok' : '',
          ],
          ['Regressions', String(p.regressions.length), p.regressions.length ? 'text-err' : ''],
          [
            'Token cost',
            p.cost_delta === null || p.cost_delta === undefined
              ? '—'
              : `${p.cost_delta >= 0 ? '+' : ''}${Math.round(p.cost_delta * 100)}%`,
            '',
          ],
        ].map(([k, v, tone]) => (
          <div key={k} className="flex flex-col gap-1">
            <dt className="text-meta text-text-2">{k}</dt>
            <dd className={`m-0 text-[19px] font-medium ${tone}`}>{v}</dd>
          </div>
        ))}
      </dl>
      {p.regressions.length > 0 && (
        <ul className="m-0 list-none border-b border-line px-6 py-3 text-meta text-err">
          {p.regressions.map((r) => (
            <li key={r}>Regression: {r}</li>
          ))}
        </ul>
      )}
      {p.shadow && (
        <div className="border-b border-line px-6 py-3 text-meta text-text-2">
          Shadow replay: {p.shadow.matched ?? 0} of {p.shadow.missions ?? 0} recent missions match what humans
          approved.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 px-6 py-4">
        <span className="min-w-0 flex-1 text-meta text-text-2">
          {p.status === 'ready'
            ? 'Applying creates a new version. You can roll back at any time.'
            : p.status === 'discarded'
              ? 'Discarded by the gates: no regression is allowed.'
              : STATUS[p.status]?.label}
          {p.decided_by ? ` · decided by ${p.decided_by}` : ''}
        </span>
        {canApply && p.status === 'ready' && (
          <>
            <Button onClick={() => setRejecting(true)} busy={busy === 'reject'}>
              Reject
            </Button>
            <Button onClick={() => void decide('try')} busy={busy === 'try'}>
              Try on one mission
            </Button>
            <Button
              variant="primary"
              onClick={() => void decide('apply')}
              busy={busy === 'apply'}
              disabled={p.regressions.length > 0}
            >
              Apply
            </Button>
          </>
        )}
      </div>
      <Dialog
        open={rejecting}
        onClose={() => setRejecting(false)}
        title="Reject this proposal"
        footer={
          <>
            <Button onClick={() => setRejecting(false)}>Cancel</Button>
            <Button variant="danger" busy={busy === 'reject'} onClick={() => void decide('reject')}>
              Reject
            </Button>
          </>
        }
      >
        <Textarea
          aria-label="Reason"
          rows={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why not? The learner uses it."
        />
      </Dialog>
    </article>
  )
}

export function Learning() {
  const search = useSearch({ from: '/learning' })
  const navigate = useNavigate()
  const proposals = useProposals()
  const signals = useSignals()
  const agents = useAgentIndex()
  const list = useMemo(() => proposals.data?.items ?? [], [proposals.data])
  const now = useNow(60_000)
  const week = (signals.data?.items ?? []).filter((s) => Date.parse(s.ts) > now - 7 * 86400_000)
  const ready = list.filter((p) => p.status === 'ready')
  const discarded = list.filter((p) => p.status === 'discarded')
  const selected = list.find((p) => p.id === search.proposal) ?? ready[0] ?? list[0] ?? null
  const strip = [
    [
      '01 · Observe',
      signals.data ? String(week.length) : '—',
      'signals this week',
      'Rejections, edits, retries, guardrail hits',
      isNotServed(signals.error),
    ],
    [
      '02 · Propose',
      proposals.data ? String(list.filter((p) => !['observed'].includes(p.status)).length) : '—',
      'proposals',
      'Skill edits, memory facts, routing rules',
      isNotServed(proposals.error),
    ],
    [
      '03 · Evaluate',
      proposals.data ? String(discarded.length) : '—',
      'discarded',
      'Replayed on evals; any regression is rejected',
      isNotServed(proposals.error),
    ],
    [
      '04 · Validate',
      proposals.data ? String(ready.length) : '—',
      'waiting for you',
      'A human applies, versioned and reversible',
      isNotServed(proposals.error),
    ],
  ] as const
  return (
    <main className="flex w-full max-w-[1320px] flex-col gap-7 px-[72px] pt-11 pb-14 max-lg:px-8 max-md:px-5">
      <PageTitle
        title="Learning"
        lead="Z learns from every decision, edit and failure. It proposes improvements to skills, memory and routing, tests them against the eval suite, and applies nothing without your approval."
        right={
          <div className="flex gap-2">
            <Link
              to="/evals"
              className="flex h-[34px] items-center rounded-btn border border-line-2 px-3.5 no-underline hover:bg-raised"
            >
              Evals
            </Link>
            <Link
              to="/analytics"
              className="flex h-[34px] items-center rounded-btn border border-line-2 px-3.5 no-underline hover:bg-raised"
            >
              Analytics
            </Link>
          </div>
        }
      />
      <ol className="m-0 grid list-none grid-cols-1 gap-px overflow-hidden rounded-card border border-line bg-line p-0 sm:grid-cols-2 lg:grid-cols-4">
        {strip.map(([step, n, what, sub, ns], i) => (
          <li key={step} className="flex flex-col gap-2 bg-bg px-5 py-5">
            <span className="font-mono text-sm text-text-3">{step}</span>
            <span className="flex items-baseline gap-2">
              <span className={`text-[22px] font-medium ${i === 3 && ready.length ? 'text-wait' : ''}`}>
                {n}
              </span>
              <span>{what}</span>
            </span>
            <span className="text-meta text-text-2">{ns ? 'Not available on this server yet' : sub}</span>
          </li>
        ))}
      </ol>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <QueryState query={proposals} what="Learning proposals" endpoint="GET /proposals">
            {() =>
              selected ? (
                <ProposalDetail key={selected.id} p={selected} />
              ) : (
                <EmptyState icon="learning" title="No proposal yet">
                  Proposals appear when a pattern repeats in your edits and rejections, after the eval gates
                  pass.
                </EmptyState>
              )
            }
          </QueryState>
        </div>
        <aside aria-label="Other proposals and signals" className="flex flex-col gap-8">
          <section aria-labelledby="other" className="flex flex-col">
            <h2 id="other" className="m-0 border-b border-line pb-2.5 text-ui font-medium">
              Other proposals
            </h2>
            {isNotServed(proposals.error) ? (
              <NotServed what="Proposals" endpoint="GET /proposals" compact className="py-3" />
            ) : (
              <>
                {list.filter((p) => p.id !== selected?.id).length === 0 && (
                  <p className="m-0 py-3 text-meta text-text-3">Nothing else to review.</p>
                )}
                {list
                  .filter((p) => p.id !== selected?.id)
                  .slice(0, 12)
                  .map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => void navigate({ to: '/learning', search: { proposal: p.id } })}
                      className="flex flex-col gap-1 border-b border-line py-3.5 text-left"
                    >
                      <span className="flex justify-between text-meta text-text-3">
                        <span>
                          {KIND[p.kind] ?? p.kind} · {agents.label(p.target.split('@')[0])}
                        </span>
                        <span className={STATUS[p.status]?.tone}>{STATUS[p.status]?.label ?? p.status}</span>
                      </span>
                      <span className="font-medium">{p.title}</span>
                      {p.rationale && (
                        <span className="line-clamp-2 text-meta text-text-2">{p.rationale}</span>
                      )}
                    </button>
                  ))}
              </>
            )}
          </section>
          <section aria-labelledby="signals" className="flex flex-col">
            <h2 id="signals" className="m-0 border-b border-line pb-2.5 text-ui font-medium">
              Recent signals
            </h2>
            <QueryState query={signals} what="Signals" endpoint="GET /signals" compact>
              {() =>
                (signals.data?.items ?? []).length === 0 ? (
                  <p className="m-0 py-3 text-meta text-text-3">
                    No signal yet. Rejections, edits, retries and guardrail hits show up here.
                  </p>
                ) : (
                  (signals.data?.items ?? []).slice(0, 10).map((s) => (
                    <div key={s.id} className="grid grid-cols-[52px_1fr] gap-3 border-b border-line py-3">
                      <span className="font-mono text-sm text-text-3">{clock(s.ts)}</span>
                      <span>
                        {SIGNAL[s.kind] ?? s.kind}
                        {signalDetail(s) && <span className="text-text-2"> · {signalDetail(s)}</span>}
                        {s.agent && <span className="text-text-3"> · {agents.label(s.agent)}</span>}
                      </span>
                    </div>
                  ))
                )
              }
            </QueryState>
          </section>
        </aside>
      </div>
    </main>
  )
}
