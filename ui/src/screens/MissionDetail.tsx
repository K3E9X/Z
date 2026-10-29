import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useMemo, useRef, useState } from 'react'
import { artifactUrl, downloadArtifact } from '@/api/artifacts'
import { api, unwrap } from '@/api/client'
import { isNotServed, problemMessage } from '@/api/problem'
import { qk, useAgents, useMission, useMissionJobs } from '@/api/queries'
import type { Job, Mission } from '@/api/types'
import { useMissionTimeline } from '@/api/useMissionTimeline'
import { useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { DiffViewer } from '@/components/DiffViewer'
import { EventTimeline } from '@/components/EventTimeline'
import { Icon } from '@/components/Icon'
import { Markdown } from '@/components/Markdown'
import { PlanChecklist } from '@/components/PlanChecklist'
import { EmptyState, ErrorState, QueryState } from '@/components/states'
import { StatusText } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, ProgressBar, Skeleton, Tabs, Textarea } from '@/components/ui'
import { useArtifactText } from '@/api/artifacts'
import { useCan } from '@/lib/auth'
import { compactNumber, elapsed, plural, relative } from '@/lib/format'
import { MOD } from '@/lib/hotkeys'
import { missionAgent, missionAgents, planSteps } from '@/lib/mission'
import { jobStatus, missionStatus, needsAttentionLabel, TERMINAL_MISSION } from '@/lib/status'
import type { TimelineState } from '@/lib/timeline'
import { useNow } from '@/lib/useMedia'
import { TopBar } from '@/shell/TopBar'
import { PlanEditor } from './PlanEditor'

type Tab = 'activity' | 'terminal' | 'files' | 'browser' | 'reasoning'

function SubAgents({
  jobs,
  onPick,
  selected,
  agents,
}: {
  jobs: Job[]
  onPick: (id: string) => void
  selected?: string
  agents: AgentIndex
}) {
  const c = { done: 0, running: 0, failed: 0, queued: 0, attention: 0 }
  for (const j of jobs) {
    if (j.status === 'succeeded') c.done++
    else if (j.status === 'running') c.running++
    else if (['failed', 'timed_out', 'budget_exceeded', 'cancelled'].includes(j.status)) c.failed++
    else if (j.status === 'needs_attention' || j.status === 'paused') c.attention++
    else c.queued++
  }
  const color = (s: string) =>
    s === 'succeeded'
      ? 'bg-ok'
      : s === 'running'
        ? 'bg-run'
        : ['failed', 'timed_out', 'budget_exceeded'].includes(s)
          ? 'bg-err'
          : s === 'needs_attention' || s === 'paused'
            ? 'bg-wait'
            : 'bg-raised border border-line-2'
  return (
    <section aria-labelledby="subagents" className="flex flex-col gap-2.5">
      <h2 id="subagents" className="m-0 text-ui font-medium">
        Sub-agents
      </h2>
      <div className="grid grid-cols-6 gap-1.5" role="list">
        {jobs.slice(0, 120).map((j) => {
          const st = jobStatus(j.status)
          return (
            <button
              key={j.id}
              type="button"
              role="listitem"
              aria-label={`${agents.label(j.agent)} ${j.item_key ?? j.step_id}: ${st.label}`}
              title={`${agents.label(j.agent)} · ${j.item_key ?? j.step_id} · ${st.label}${j.needs_attention_reason ? ` · ${needsAttentionLabel(j.needs_attention_reason)}` : ''}`}
              onClick={() => {
                onPick(j.id)
              }}
              className={`aspect-[1.15] rounded-[5px] ${color(j.status)} ${selected === j.id ? 'outline-2 outline-offset-1 outline-text' : ''}`}
            />
          )
        })}
      </div>
      {jobs.length > 120 && <div className="text-sm text-text-3">+ {jobs.length - 120} more</div>}
      <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-meta text-text-2">
        <span>{c.done} done</span>
        <span>{c.running} running</span>
        {c.attention > 0 && <span className="text-wait">{c.attention} need attention</span>}
        {c.failed > 0 && <span className="text-err">{c.failed} failed</span>}
        {c.queued > 0 && <span>{c.queued} queued</span>}
      </div>
    </section>
  )
}

function Context({
  mission,
  timeline,
  agents,
}: {
  mission: Mission
  timeline: TimelineState
  agents: AgentIndex
}) {
  const slugs = missionAgents(mission)
  const skills = [...new Set(slugs.flatMap((s) => agents.get(s)?.skills ?? []))]
  const plan = planSteps(mission)
  const writes = [...new Set(plan.flatMap((s) => s.writes ?? []))]
  const used = mission.tokens_in + mission.tokens_out || timeline.tokens.in + timeline.tokens.out
  const max = mission.budget.max_tokens ?? 0
  const tools = [
    ...mission.connectors.map((c) => `${c} (read)`),
    ...(plan.some((s) => s.workspace === 'git_clone' || s.workspace === 'files')
      ? ['isolated workspace']
      : []),
  ]
  return (
    <section aria-labelledby="context" className="flex flex-col gap-2.5">
      <h2 id="context" className="m-0 text-ui font-medium">
        Context
      </h2>
      <dl className="m-0 grid grid-cols-[56px_1fr] gap-x-3.5 gap-y-2.5 text-ui">
        <dt className="text-text-3">Tools</dt>
        <dd className="m-0">{tools.length ? tools.join(', ') : 'Workspace only'}</dd>
        <dt className="text-text-3">Skills</dt>
        <dd className="m-0">{skills.length ? skills.join(', ') : '—'}</dd>
        <dt className="text-text-3">Writes</dt>
        <dd className="m-0">
          {mission.autonomy === 'read_only'
            ? 'None, read only'
            : writes.length
              ? `${writes.join(', ')}, after approval`
              : 'None in this plan'}
        </dd>
        <dt className="text-text-3">Data</dt>
        <dd className="m-0">
          <span className="font-mono">{timeline.dataClass ?? mission.data_class}</span>
          <span className="text-text-3">
            {' '}
            ·{' '}
            {(timeline.dataClass ?? mission.data_class) >= 'C2'
              ? 'internal models only'
              : 'external allowed by policy'}
          </span>
        </dd>
        <dt className="text-text-3">Budget</dt>
        <dd className="m-0 flex flex-col gap-1.5">
          <span className="font-mono">
            {compactNumber(used)} / {max ? compactNumber(max) : '∞'} tokens
          </span>
          {max > 0 && (
            <ProgressBar
              value={used}
              max={max}
              tone={used / max > 0.9 ? 'err' : used / max > 0.75 ? 'wait' : 'text'}
              label="Token budget used"
            />
          )}
        </dd>
      </dl>
    </section>
  )
}

function TerminalTab({ timeline }: { timeline: TimelineState }) {
  const items = timeline.items.filter((i) => i.kind === 'terminal')
  if (items.length === 0)
    return (
      <EmptyState icon="terminal" title="No command ran yet">
        Commands run in the sandbox appear here with their output.
      </EmptyState>
    )
  return (
    <div className="flex flex-col gap-3 py-4">
      {items.map((it) =>
        it.kind === 'terminal' ? (
          <div key={it.key} className="overflow-hidden rounded-btn border border-line bg-code">
            <div className="flex items-center gap-2 border-b border-line px-3 py-2 font-mono text-[12.5px]">
              <span className="text-text-3">$</span>
              <span className="min-w-0 flex-1 truncate">{it.term.command}</span>
              {it.term.exit_code !== null && it.term.exit_code !== undefined && (
                <span className={it.term.exit_code === 0 ? 'text-ok' : 'text-err'}>
                  exit {it.term.exit_code}
                </span>
              )}
              {it.term.output_ref && <FullOutput id={it.term.output_ref} />}
            </div>
            <pre className="scrollbar-thin m-0 max-h-[320px] overflow-auto px-3 py-2 font-mono text-xs leading-[1.6] text-text-2">
              {it.term.output_tail ?? ''}
            </pre>
          </div>
        ) : null,
      )}
    </div>
  )
}

function FullOutput({ id }: { id: string }) {
  const [open, setOpen] = useState(false)
  const q = useArtifactText(id, open)
  return (
    <>
      <button
        type="button"
        className="text-meta text-text-2 underline underline-offset-2"
        onClick={() => {
          setOpen(true)
        }}
      >
        Full output
      </button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false)
        }}
        title="Terminal output"
        width={900}
      >
        {q.isPending ? (
          <Skeleton className="h-40" />
        ) : q.error ? (
          <ErrorState error={q.error} />
        ) : (
          <pre className="m-0 font-mono text-xs whitespace-pre-wrap text-text-2">{q.data}</pre>
        )}
      </Dialog>
    </>
  )
}

function FilesTab({ timeline }: { timeline: TimelineState }) {
  const edits = timeline.items.filter((i) => i.kind === 'file_edit')
  const byPath = new Map<string, typeof edits>()
  for (const e of edits)
    if (e.kind === 'file_edit') byPath.set(e.edit.path, [...(byPath.get(e.edit.path) ?? []), e])
  const artifacts = timeline.items.filter((i) => i.kind === 'artifact')
  const [open, setOpen] = useState<string | null>(null)
  const openEdit = edits.find((e) => e.kind === 'file_edit' && e.key === open)
  const diffQ = useArtifactText(openEdit?.kind === 'file_edit' ? openEdit.edit.diff_ref : null)
  if (edits.length === 0 && artifacts.length === 0)
    return (
      <EmptyState icon="file" title="No file changed yet">
        Edits in the sandbox and files the agent produces appear here.
      </EmptyState>
    )
  return (
    <div className="flex flex-col gap-6 py-4">
      {byPath.size > 0 && (
        <section aria-label="Changed files">
          <h3 className="m-0 mb-2 text-meta font-medium text-text-3">Changed in the sandbox</h3>
          <ul className="m-0 list-none p-0">
            {[...byPath.entries()].map(([path, list]) => {
              const added = list.reduce((n, e) => n + (e.kind === 'file_edit' ? e.edit.added : 0), 0)
              const removed = list.reduce((n, e) => n + (e.kind === 'file_edit' ? e.edit.removed : 0), 0)
              const last = list[list.length - 1]
              return (
                <li key={path} className="border-b border-line">
                  <button
                    type="button"
                    className="flex w-full items-center gap-3 py-2.5 text-left"
                    onClick={() => {
                      setOpen(open === last?.key ? null : (last?.key ?? null))
                    }}
                    aria-expanded={open === last?.key}
                  >
                    <Icon name="file" size={14} className="text-text-3" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{path}</span>
                    <span className="font-mono text-xs text-ok">+{added}</span>
                    <span className="font-mono text-xs text-err">−{removed}</span>
                    <span className="text-meta text-text-3">{plural(list.length, 'edit')}</span>
                  </button>
                  {open === last?.key && (
                    <div className="pb-3">
                      {diffQ.isPending ? (
                        <Skeleton className="h-24" />
                      ) : diffQ.error ? (
                        <ErrorState error={diffQ.error} />
                      ) : (
                        <DiffViewer diff={diffQ.data} />
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}
      {artifacts.length > 0 && (
        <section aria-label="Produced files">
          <h3 className="m-0 mb-2 text-meta font-medium text-text-3">Produced</h3>
          <ul className="m-0 list-none p-0">
            {artifacts.map((a) =>
              a.kind === 'artifact' ? (
                <li key={a.key} className="flex items-center gap-3 border-b border-line py-2.5">
                  <Icon name="doc" size={14} className="text-text-3" />
                  <span className="min-w-0 flex-1 truncate">{a.artifact.name}</span>
                  <span className="text-meta text-text-3">{a.artifact.kind}</span>
                  <Button
                    size="xs"
                    variant="ghost"
                    icon="download"
                    onClick={() => void downloadArtifact(a.artifact.artifact_id, a.artifact.name)}
                  >
                    Download
                  </Button>
                </li>
              ) : null,
            )}
          </ul>
        </section>
      )}
    </div>
  )
}

function BrowserTab({ timeline }: { timeline: TimelineState }) {
  const shots = timeline.items.filter((i) => i.kind === 'browser')
  if (shots.length === 0)
    return (
      <EmptyState icon="globe" title="No browser session">
        Browser flows run gateway-side; their steps and screenshots appear here.
      </EmptyState>
    )
  return (
    <div className="grid gap-4 py-4 sm:grid-cols-2">
      {shots.map((s) =>
        s.kind === 'browser' ? (
          <figure
            key={s.key}
            className="m-0 flex flex-col gap-2 rounded-panel border border-line bg-panel p-3"
          >
            {s.browser.screenshot_ref ? (
              <img
                src={artifactUrl(s.browser.screenshot_ref)}
                alt={`Screenshot: ${s.browser.action}`}
                loading="lazy"
                className="rounded-btn border border-line"
              />
            ) : (
              <div className="flex h-32 items-center justify-center rounded-btn bg-raised text-meta text-text-3">
                No screenshot
              </div>
            )}
            <figcaption className="text-meta">
              <span className="font-medium">{s.browser.action}</span>{' '}
              <span className="font-mono text-text-3">{s.browser.url}</span>
            </figcaption>
          </figure>
        ) : null,
      )}
    </div>
  )
}

function ReasoningTab({ timeline, agents }: { timeline: TimelineState; agents: AgentIndex }) {
  const [open, setOpen] = useState<Set<number>>(() => new Set())
  if (timeline.thoughts.length === 0)
    return (
      <EmptyState icon="sparkle" title="No reasoning recorded">
        When the model shares its reasoning, each step appears here, collapsed.
      </EmptyState>
    )
  return (
    <ol className="m-0 flex list-none flex-col p-0 py-2">
      {timeline.thoughts.map((t) => {
        const isOpen = open.has(t.seq)
        return (
          <li key={t.seq} className="border-b border-line py-2.5">
            <button
              type="button"
              aria-expanded={isOpen}
              className="flex w-full items-start gap-3 text-left"
              onClick={() => {
                setOpen((cur) => {
                  const n = new Set(cur)
                  if (n.has(t.seq)) n.delete(t.seq)
                  else n.add(t.seq)
                  return n
                })
              }}
            >
              <Icon name={isOpen ? 'chevronDown' : 'chevronRight'} size={12} className="mt-1 text-text-3" />
              <span className="font-mono text-xs text-text-3">
                {new Date(t.ts).toLocaleTimeString('en-GB')}
              </span>
              <span className={`min-w-0 flex-1 ${isOpen ? '' : 'truncate'} text-text-2`}>
                {isOpen ? null : t.text}
              </span>
              {t.agent && <span className="text-sm text-text-3">{agents.label(t.agent)}</span>}
            </button>
            {isOpen && <Markdown className="mt-2 pl-7 text-text-2">{t.text}</Markdown>}
          </li>
        )
      })}
    </ol>
  )
}

function Attention({ jobs, agents }: { jobs: Job[]; agents: AgentIndex }) {
  const qc = useQueryClient()
  const toast = useToast()
  const can = useCan('missions:control')
  const [busy, setBusy] = useState<string | null>(null)
  const retry = async (j: Job) => {
    setBusy(j.id)
    try {
      await unwrap(api.POST('/jobs/{id}/retry', { params: { path: { id: j.id } }, body: {} }))
      await qc.invalidateQueries({ queryKey: qk.missionJobs(j.mission_id) })
      await qc.invalidateQueries({ queryKey: qk.mission(j.mission_id) })
      toast.ok('Retrying from the last checkpoint')
    } catch (e) {
      toast.error(e, 'Retry failed')
    } finally {
      setBusy(null)
    }
  }
  return (
    <div role="status" className="mt-2 flex flex-col gap-1.5 rounded-panel bg-wait-bg px-4 py-3">
      {jobs.slice(0, 3).map((j) => (
        <div key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium text-wait">{needsAttentionLabel(j.needs_attention_reason)}</span>
          <span className="text-meta text-text-2">
            {agents.label(j.agent)} · step {j.step_id}
            {j.item_key ? ` · ${j.item_key}` : ''} · attempt {j.attempt}
            {j.error ? ` · ${j.error.message}` : ''}
          </span>
          {can && (
            <Button size="xs" className="ml-auto" busy={busy === j.id} onClick={() => void retry(j)}>
              Retry
            </Button>
          )}
        </div>
      ))}
      {jobs.length > 3 && <span className="text-meta text-text-2">and {jobs.length - 3} more</span>}
    </div>
  )
}

function Composer({ mission, jobId }: { mission: Mission; jobId?: string }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const can = useCan('missions:control')
  const done = TERMINAL_MISSION.has(mission.status)
  const send = async () => {
    const t = text.trim()
    if (!t) return
    setBusy(true)
    try {
      await unwrap(
        api.POST('/missions/{id}/messages', {
          params: { path: { id: mission.id } },
          body: { text: t, ...(jobId ? { job_id: jobId } : {}) },
        }),
      )
      setText('')
      toast.ok('Sent. The agent reads it at its next step.')
    } catch (e) {
      toast.error(e, 'Could not guide the agent')
    } finally {
      setBusy(false)
    }
  }
  if (!can) return null
  return (
    <form
      className="flex items-end gap-2 rounded-panel border border-line-2 bg-panel py-2 pr-2 pl-4"
      onSubmit={(e) => {
        e.preventDefault()
        void send()
      }}
    >
      <label htmlFor="guide" className="sr-only">
        Guide the agent
      </label>
      <Textarea
        id="guide"
        rows={1}
        value={text}
        disabled={done}
        onChange={(e) => {
          setText(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            void send()
          }
        }}
        placeholder={done ? 'This mission has ended.' : 'Guide the agent: “skip archived repos”'}
        className="min-h-[34px] flex-1 resize-none border-none px-0 focus-visible:outline-none"
      />
      <button
        type="submit"
        aria-label="Send to the agent"
        disabled={busy || done || !text.trim()}
        className="flex h-8 w-8 flex-none items-center justify-center rounded-btn bg-inv-bg text-inv-text disabled:opacity-40"
      >
        <Icon name="arrowUp" size={15} />
      </button>
    </form>
  )
}

function StopDialog({ open, onClose, mission }: { open: boolean; onClose: () => void; mission: Mission }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const stop = async () => {
    setBusy(true)
    try {
      await unwrap(
        api.POST('/missions/{id}/cancel', {
          params: { path: { id: mission.id } },
          body: reason ? { reason } : {},
        }),
      )
      await qc.invalidateQueries({ queryKey: qk.mission(mission.id) })
      toast.ok('Mission stopped')
      onClose()
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Stop this mission?"
      footer={
        <>
          <Button onClick={onClose}>Keep running</Button>
          <Button variant="danger" busy={busy} onClick={() => void stop()}>
            Stop mission
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="m-0 text-text-2">
          Running jobs stop after their current step. Writes already approved still apply; pending ones are
          dropped.
        </p>
        <label className="flex flex-col gap-1.5">
          <span className="text-meta text-text-2">Why? The reason teaches the agent.</span>
          <Textarea
            rows={2}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value)
            }}
            placeholder="e.g. wrong repositories selected"
          />
        </label>
      </div>
    </Dialog>
  )
}

function Header({ mission, onStop }: { mission: Mission; onStop: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const can = useCan('missions:control')
  const [busy, setBusy] = useState<string | null>(null)
  const st = missionStatus(mission.status)
  const active = !TERMINAL_MISSION.has(mission.status)
  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: qk.mission(mission.id) })
      toast.ok(ok)
    } catch (e) {
      if (isNotServed(e)) toast.info('Take over is not available on this server yet.')
      else toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="flex items-center gap-2">
      <StatusText tone={st.tone} label={st.label} className="mr-2 text-ui" />
      {can && (mission.status === 'running' || mission.status === 'waiting') && (
        <Button
          size="sm"
          busy={busy === 'pause'}
          onClick={() =>
            void run(
              'pause',
              () =>
                unwrap(api.POST('/missions/{id}/pause', { params: { path: { id: mission.id } }, body: {} })),
              'Pausing after the current step',
            )
          }
        >
          Pause
        </Button>
      )}
      {can && mission.status === 'paused' && (
        <Button
          size="sm"
          busy={busy === 'resume'}
          onClick={() =>
            void run(
              'resume',
              () =>
                unwrap(api.POST('/missions/{id}/resume', { params: { path: { id: mission.id } }, body: {} })),
              'Resumed',
            )
          }
        >
          Resume
        </Button>
      )}
      {can && (mission.status === 'draft' || mission.status === 'planned') && (
        <Button
          size="sm"
          variant="primary"
          busy={busy === 'start'}
          onClick={() =>
            void run(
              'start',
              () =>
                unwrap(api.POST('/missions/{id}/start', { params: { path: { id: mission.id } }, body: {} })),
              'Started',
            )
          }
        >
          Start
        </Button>
      )}
      {can && active && mission.status !== 'draft' && mission.status !== 'planned' && (
        <Button
          size="sm"
          busy={busy === 'takeover'}
          onClick={() =>
            void run(
              'takeover',
              async () => {
                const jobs = await unwrap(
                  api.GET('/missions/{id}/jobs', {
                    params: { path: { id: mission.id }, query: { status: 'running,needs_attention,paused' } },
                  }),
                )
                const job = jobs.items[0]
                if (!job) throw new Error('No running job to take over')
                const s = await unwrap(
                  api.POST('/missions/{id}/take-over', {
                    params: { path: { id: mission.id } },
                    body: { job_id: job.id, mode: 'read_only' },
                  }),
                )
                window.open(s.terminal_url, '_blank', 'noopener,noreferrer')
              },
              'Workspace opened read-only in a new tab',
            )
          }
        >
          Take over
        </Button>
      )}
      {can && active && (
        <Button size="sm" variant="danger" onClick={onStop}>
          Stop
        </Button>
      )}
      {!active && (
        <>
          <Button
            size="sm"
            onClick={() => void navigate({ to: '/missions/$id/replay', params: { id: mission.id } })}
          >
            Replay
          </Button>
          <Button
            size="sm"
            onClick={() =>
              void navigate({
                to: '/missions/new',
                search: { prompt: mission.prompt ?? undefined, playbook: mission.playbook ?? undefined },
              })
            }
          >
            Run again
          </Button>
        </>
      )}
    </div>
  )
}

export function MissionDetail() {
  const { id } = useParams({ from: '/missions/$id' })
  const search = useSearch({ from: '/missions/$id' })
  const navigate = useNavigate()
  const mission = useMission(id)
  const agents = useAgentIndex()
  useAgents()
  const live = mission.data ? !TERMINAL_MISSION.has(mission.data.status) : true
  const jobs = useMissionJobs(id, { live })
  const { timeline } = useMissionTimeline(id)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stop, setStop] = useState(false)
  const [editPlan, setEditPlan] = useState(false)
  const now = useNow(1000)
  const canStart = useCan('missions:start')
  const tab = (
    ['activity', 'terminal', 'files', 'browser', 'reasoning'].includes(search.tab ?? '')
      ? search.tab
      : 'activity'
  ) as Tab
  const jobFilter = search.job

  const items = useMemo(
    () => (jobFilter ? timeline.items.filter((i) => i.jobId === jobFilter) : timeline.items),
    [timeline.items, jobFilter],
  )
  const filesCount = new Set(
    timeline.items
      .filter((i) => i.kind === 'file_edit')
      .map((i) => (i.kind === 'file_edit' ? i.edit.path : '')),
  ).size

  if (mission.error || !mission.data) {
    return (
      <>
        <TopBar crumbs={[{ label: 'Mission control', to: '/' }, { label: 'Mission' }]} />
        <main className="px-10 py-9">
          <QueryState query={mission} what="This mission" endpoint="GET /missions/{id}">
            {() => null}
          </QueryState>
        </main>
      </>
    )
  }
  const m = mission.data
  const steps = timeline.plan ?? planSteps(m)
  const agentSlug = missionAgent(m)
  const models = [...timeline.models]
  const attention = (jobs.data?.items ?? []).filter(
    (j) =>
      j.status === 'needs_attention' &&
      j.needs_attention_reason !== 'approval' &&
      j.needs_attention_reason !== 'question',
  )
  const subJobs = (jobs.data?.items ?? []).filter(
    (j) => (jobs.data?.items.length ?? 0) > 1 || j.parent_job_id,
  )
  const meta = [
    models.length ? models.join(', ') : agents.get(agentSlug)?.model,
    jobs.data && jobs.data.items.length > 1 ? plural(jobs.data.items.length, 'sub-agent') : null,
    m.started_at
      ? `${elapsed(m.started_at, m.ended_at, now)} ${m.ended_at ? 'total' : 'elapsed'}`
      : `created ${relative(m.created_at)}`,
    m.autonomy === 'read_only' ? 'read only' : 'writes need your approval',
    m.playbook ? `playbook ${m.playbook}${m.playbook_version ? ` v${m.playbook_version}` : ''}` : null,
  ].filter(Boolean)
  const setTab = (t: Tab) => {
    void navigate({
      to: '/missions/$id',
      params: { id },
      search: { ...search, tab: t === 'activity' ? undefined : t },
      replace: true,
    })
  }
  const pickJob = (jid: string) => {
    void navigate({
      to: '/missions/$id',
      params: { id },
      search: { ...search, job: search.job === jid ? undefined : jid },
      replace: true,
    })
  }

  return (
    <div className="flex h-screen min-h-0 flex-col">
      <TopBar
        crumbs={[{ label: 'Mission control', to: '/' }, { label: agents.label(agentSlug) }]}
        right={<Header mission={m} onStop={() => setStop(true)} />}
      />
      <div className="flex flex-col gap-1.5 border-b border-line px-7 pt-6 pb-5">
        <h1 className="m-0 text-2xl font-semibold">{m.title}</h1>
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-sm text-text-2">
          {meta.map((x, i) => (
            <span key={i}>{x}</span>
          ))}
        </div>
        {m.error && <div className="text-meta text-err">{m.error}</div>}
        {attention.length > 0 && <Attention jobs={attention} agents={agents} />}
        {m.status === 'waiting' && m.waiting_reason === 'approval' && m.pending_actions === 0 ? (
          // Every write is decided; the mission waits for the executor to report back.
          <span className="text-meta text-text-3">
            Every write is decided; waiting for the results from the executor.
          </span>
        ) : (
          m.status === 'waiting' &&
          m.waiting_reason && (
            <Link
              to="/review"
              search={{ kind: m.waiting_reason === 'question' ? 'questions' : 'writes' }}
              className="self-start text-meta text-wait"
            >
              {m.waiting_reason === 'question'
                ? 'A question is waiting for you'
                : `${plural(m.pending_actions ?? 1, 'write')} waiting for your approval`}{' '}
              →
            </Link>
          )
        )}
      </div>
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Plan and context"
          className="scrollbar-thin flex w-[300px] flex-none flex-col gap-7 overflow-y-auto border-r border-line px-6 py-6 max-lg:hidden"
        >
          <section aria-labelledby="plan" className="flex flex-col gap-3">
            <div className="flex items-baseline justify-between">
              <h2 id="plan" className="m-0 text-ui font-medium">
                Plan
              </h2>
              {canStart && (m.status === 'draft' || m.status === 'planned') && (
                <button
                  type="button"
                  className="text-meta text-text-3 hover:text-text"
                  onClick={() => setEditPlan(true)}
                >
                  Edit
                </button>
              )}
            </div>
            {steps.length ? (
              <PlanChecklist
                steps={steps}
                inputs={m.inputs}
                agentLabel={(s) => agents.label(s)}
                showAgents={new Set(steps.map((s) => s.agent)).size > 1}
              />
            ) : (
              <div className="text-meta text-text-3">No plan yet.</div>
            )}
          </section>
          {subJobs.length > 0 && (
            <SubAgents jobs={subJobs} onPick={pickJob} selected={jobFilter} agents={agents} />
          )}
          <Context mission={m} timeline={timeline} agents={agents} />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-4 border-b border-line px-6">
            <Tabs<Tab>
              label="Mission views"
              variant="pill"
              className="py-2.5"
              value={tab}
              onChange={setTab}
              items={[
                { id: 'activity', label: 'Activity' },
                { id: 'terminal', label: 'Terminal' },
                { id: 'files', label: 'Files', badge: filesCount || undefined },
                { id: 'browser', label: 'Browser' },
                { id: 'reasoning', label: 'Reasoning' },
              ]}
            />
            {jobFilter && (
              <button type="button" className="text-meta text-text-2" onClick={() => pickJob(jobFilter)}>
                Showing one sub-agent · show all
              </button>
            )}
          </div>
          <div ref={scrollRef} className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-6">
            {tab === 'activity' && (
              <EventTimeline
                timeline={timeline}
                items={items}
                agents={agents}
                scrollRef={scrollRef}
                empty={
                  <EmptyState
                    icon="missionControl"
                    title={
                      m.status === 'draft' || m.status === 'planned'
                        ? 'Not started'
                        : 'Waiting for the first event'
                    }
                  >
                    {m.status === 'draft' || m.status === 'planned'
                      ? 'Review the plan, then start the mission.'
                      : 'Tool calls, edits, guardrail blocks and questions stream in here live.'}
                  </EmptyState>
                }
              />
            )}
            {tab === 'terminal' && <TerminalTab timeline={timeline} />}
            {tab === 'files' && <FilesTab timeline={timeline} />}
            {tab === 'browser' && <BrowserTab timeline={timeline} />}
            {tab === 'reasoning' && <ReasoningTab timeline={timeline} agents={agents} />}
          </div>
          <div className="border-t border-line px-6 py-4">
            <Composer mission={m} jobId={jobFilter} />
            <div className="mt-1.5 text-sm text-text-3 max-md:hidden">
              ↵ send · ⇧↵ new line · {MOD}K palette
            </div>
          </div>
        </div>
      </div>
      <StopDialog open={stop} onClose={() => setStop(false)} mission={m} />
      {editPlan && <PlanEditor mission={m} open={editPlan} onClose={() => setEditPlan(false)} />}
      {jobs.error && !isNotServed(jobs.error) && (
        <span className="sr-only">{problemMessage(jobs.error)}</span>
      )}
    </div>
  )
}
