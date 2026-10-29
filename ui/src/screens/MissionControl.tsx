import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { useAgents, useConnectors, useInferenceHealth, useMissions, useReview } from '@/api/queries'
import type { Mission } from '@/api/types'
import { useMissionTimeline } from '@/api/useMissionTimeline'
import { AgentName, useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { DecisionRow, reviewItemId } from '@/components/DecisionRow'
import { Icon } from '@/components/Icon'
import { KpiStrip, type Kpi } from '@/components/KpiStrip'
import { PlanChecklist } from '@/components/PlanChecklist'
import { ErrorState, EmptyState, NotServed } from '@/components/states'
import { StatusMark } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, ProgressBar, SectionHeader, Skeleton } from '@/components/ui'
import { useCan, useCurrentMe } from '@/lib/auth'
import { compactNumber, countWord, elapsed, isToday, ms, percent, plural } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { missionAgent, missionAgents, nowLine, planSteps, progress } from '@/lib/mission'
import { missionStatus, TERMINAL_MISSION } from '@/lib/status'
import { useIsMobile, useNow } from '@/lib/useMedia'
import { TopBar } from '@/shell/TopBar'
import { useShellData } from '@/shell/useShellData'
import { MobileHome } from './MobileHome'

function firstName(name: string): string {
  return name.split(/\s+/)[0] ?? name
}

function useKpis(active: Mission[], finished: Mission[], now: number): Kpi[] {
  const shell = useShellData()
  const agents = useAgents()
  const connectors = useConnectors()
  const health = useInferenceHealth()
  const running = active.filter((m) => m.status === 'running' || m.status === 'waiting')
  const subAgents = active.reduce((n, m) => n + (m.jobs?.running ?? 0), 0)
  const workingAgents = new Set(running.flatMap((m) => missionAgents(m))).size
  const counts = shell.reviewCounts
  const week = finished.filter((m) => m.ended_at && now - Date.parse(m.ended_at) < 7 * 86400_000)
  const ok = week.filter((m) => m.status === 'succeeded').length
  const conns = connectors.data ?? []
  const expiring = conns.filter((c) => c.health.status === 'token_expiring').length
  const down = conns.filter((c) => c.health.status === 'unreachable').length
  const degraded = conns.filter((c) => c.health.status === 'degraded').length
  const pending = conns.filter((c) => c.review_status === 'pending_review').length
  const h = health.data
  const fast = h?.models.find((m) => m.ttft_p95_ms !== null && m.ttft_p95_ms !== undefined)
  const tps = h?.models.reduce((n, m) => n + (m.tokens_per_s ?? 0), 0)

  return [
    {
      label: 'Agents running',
      value: agents.data ? `${workingAgents} of ${agents.data.items.length}` : '…',
      sub: plural(subAgents, 'sub-agent'),
      to: '/agents',
    },
    {
      label: 'Waiting for you',
      value: counts ? String(counts.all) : '…',
      tone: counts && counts.all > 0 ? 'wait' : undefined,
      sub: counts ? `${plural(counts.writes, 'write')}, ${plural(counts.questions, 'question')}` : '',
      to: '/review',
    },
    {
      label: 'Tokens today',
      value: h ? compactNumber(h.tokens_today) : '…',
      unavailable: isNotServed(health.error),
      sub: shell.budgetPct !== null ? `${percent(shell.budgetPct)} of monthly budget` : 'No org budget set',
      to: '/inference',
    },
    {
      label: 'inferX',
      value: h ? (h.status === 'healthy' ? 'Healthy' : h.status === 'degraded' ? 'Degraded' : h.status === 'down' ? 'Down' : 'Unknown') : '…',
      tone: h ? (h.status === 'healthy' ? 'ok' : h.status === 'down' ? 'err' : h.status === 'degraded' ? 'wait' : 'idle') : undefined,
      unavailable: isNotServed(health.error),
      sub: h ? [fast?.ttft_p95_ms ? `p95 ${ms(fast.ttft_p95_ms)}` : null, tps ? `${compactNumber(tps)} tok/s` : null].filter(Boolean).join(' · ') || (h.gateway.ok ? 'Gateway reachable' : 'Gateway unreachable') : '',
      to: '/inference',
    },
    {
      label: 'Connectors',
      value: connectors.data ? String(conns.length) : '…',
      tone: down ? 'err' : expiring || degraded || pending ? 'wait' : conns.length ? 'ok' : undefined,
      sub: down
        ? `${down} unreachable`
        : pending
          ? `${pending} waiting for exposure review`
          : expiring
          ? `${plural(expiring, 'token')} expiring`
          : degraded
            ? `${degraded} degraded`
            : 'All healthy',
      to: '/connectors',
    },
    {
      label: 'Success, 7 days',
      value: week.length ? percent(ok / week.length) : '—',
      sub: week.length ? `${ok} of ${plural(week.length, 'mission')}` : 'No mission finished yet',
      to: '/history',
    },
  ]
}

function AgentRows({
  missions,
  idle,
  selected,
  onSelect,
  agents,
  liveNow,
}: {
  missions: Mission[]
  idle: string[]
  selected: string | null
  onSelect: (id: string) => void
  agents: AgentIndex
  liveNow: string | null
}) {
  const navigate = useNavigate()
  const now = useNow(1000)
  const grid = 'grid grid-cols-[minmax(0,1.2fr)_minmax(0,2.2fr)_minmax(0,1.5fr)_104px_56px] gap-4'
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[700px]" role="table" aria-label="Agents at work">
        <div role="row" className={`${grid} px-2.5 py-2.5 text-sm text-text-3`}>
          <span role="columnheader">Agent</span>
          <span role="columnheader">Mission</span>
          <span role="columnheader">Now</span>
          <span role="columnheader">Progress</span>
          <span role="columnheader" className="text-right">
            Time
          </span>
        </div>
        {missions.map((m) => {
          const st = missionStatus(m.status)
          const p = progress(m)
          const isSel = m.id === selected
          const line = isSel && liveNow && m.status === 'running' ? { text: liveNow, tone: 'mono' as const } : nowLine(m)
          return (
            <div
              key={m.id}
              role="row"
              tabIndex={0}
              aria-selected={isSel}
              onClick={() => {
                onSelect(m.id)
              }}
              onDoubleClick={() => {
                void navigate({ to: '/missions/$id', params: { id: m.id } })
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void navigate({ to: '/missions/$id', params: { id: m.id } })
              }}
              className={`${grid} cursor-pointer items-center rounded-btn px-2.5 py-[var(--row-y)] ${isSel ? 'bg-raised' : 'hover:bg-panel'}`}
            >
              <span role="cell" className="flex min-w-0 items-center gap-2">
                <StatusMark tone={st.tone} label={st.label} />
                <AgentName slug={missionAgent(m)} index={agents} />
              </span>
              <span role="cell" className="truncate">
                {m.title}
              </span>
              <span
                role="cell"
                className={`truncate ${line.tone === 'wait' ? 'text-meta text-wait' : line.tone === 'mono' ? 'font-mono text-sm text-text-2' : 'text-meta text-text-3'}`}
              >
                {line.text}
              </span>
              <span role="cell" className="flex items-center gap-2">
                <ProgressBar value={p.done} max={p.total || 1} className="flex-1" label={`${p.done} of ${p.total}`} />
                <span className="font-mono text-xs text-text-2">
                  {p.done}/{p.total}
                </span>
              </span>
              <span role="cell" className="text-right font-mono text-sm text-text-2">
                {elapsed(m.started_at ?? m.created_at, null, now)}
              </span>
            </div>
          )
        })}
        {idle.length > 0 && (
          <Link
            to="/agents"
            role="row"
            className={`${grid} items-center rounded-btn px-2.5 py-[var(--row-y)] text-text-3 no-underline hover:bg-panel`}
          >
            <span role="cell" className="flex min-w-0 items-center gap-2">
              <StatusMark tone="idle" label="Idle" />
              <span className="truncate">{idle.map((s) => agents.label(s)).join(', ')}</span>
            </span>
            <span role="cell">Idle</span>
            <span role="cell" />
            <span role="cell" />
            <span role="cell" />
          </Link>
        )}
      </div>
    </div>
  )
}

function Preview({ mission, agents, onLiveNow }: { mission: Mission; agents: AgentIndex; onLiveNow?: (t: string | null) => void }) {
  const { timeline } = useMissionTimeline(mission.id)
  const qc = useQueryClient()
  const toast = useToast()
  const canControl = useCan('missions:control')
  const [busy, setBusy] = useState(false)
  const steps = timeline.plan ?? planSteps(mission)
  const st = missionStatus(mission.status)
  const agent = missionAgent(mission)
  const live = timeline.lastActivity?.text ?? null
  useEffect(() => {
    onLiveNow?.(live)
  }, [live, onLiveNow])
  // Recent machine output: last terminal tails, else last tool results.
  const tail = useMemo(() => {
    const out: { text: string; tone?: 'err' }[] = []
    for (const it of [...timeline.items].reverse()) {
      if (out.length >= 4) break
      if (it.kind === 'terminal') {
        const lines = (it.term.output_tail ?? it.term.command).trim().split('\n').slice(-2)
        for (const l of lines.reverse()) out.push(it.term.exit_code ? { text: l, tone: 'err' } : { text: l })
      } else if (it.kind === 'tool' && it.result) {
        out.push(it.result.ok ? { text: `${it.call.operation ?? it.call.tool}  ${it.result.summary}` } : { text: `${it.call.operation ?? it.call.tool}  ${it.result.summary}`, tone: 'err' })
      } else if (it.kind === 'denied') {
        out.push({ text: `blocked  ${it.denied.what}`, tone: 'err' })
      }
    }
    return out.reverse()
  }, [timeline.items])

  const control = async (op: 'pause' | 'resume') => {
    setBusy(true)
    try {
      if (op === 'pause') await unwrap(api.POST('/missions/{id}/pause', { params: { path: { id: mission.id } }, body: {} }))
      else await unwrap(api.POST('/missions/{id}/resume', { params: { path: { id: mission.id } }, body: {} }))
      await qc.invalidateQueries({ queryKey: ['missions'] })
      toast.ok(op === 'pause' ? 'Paused after the current step' : 'Resumed')
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside aria-label="Selected agent preview" className="flex min-w-0 flex-[1_1_320px] flex-col rounded-panel border border-line bg-panel">
      <div className="flex flex-col gap-1 border-b border-line px-[18px] py-4">
        <div className={`flex items-center gap-2 text-sm ${st.tone === 'run' ? 'text-run' : st.tone === 'wait' ? 'text-wait' : 'text-text-3'}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${st.tone === 'run' ? 'animate-zpulse bg-run' : st.tone === 'wait' ? 'bg-wait' : 'bg-text-3'}`} aria-hidden="true" />
          {st.tone === 'run' ? 'Live' : st.label} · {agents.label(agent)}
        </div>
        <div className="text-[14.5px] font-medium">{mission.title}</div>
      </div>
      {steps.length > 0 ? (
        <PlanChecklist steps={steps} inputs={mission.inputs} className="px-[18px] py-3.5" />
      ) : (
        <div className="px-[18px] py-3.5 text-meta text-text-3">The plan appears once the orchestrator drafts it.</div>
      )}
      {tail.length > 0 && (
        <pre className="scrollbar-thin mx-[18px] overflow-x-auto rounded-btn border border-line bg-code px-3.5 py-3 font-mono text-xs leading-[1.7] text-text-2">
          {tail.map((l, i) => (
            <div key={i} className={l.tone === 'err' ? 'text-err' : ''}>
              {l.text}
            </div>
          ))}
        </pre>
      )}
      <div className="flex gap-2 px-[18px] py-4">
        <Link
          to="/missions/$id"
          params={{ id: mission.id }}
          className="flex h-8 flex-1 items-center justify-center rounded-btn bg-inv-bg font-medium text-inv-text no-underline hover:opacity-90"
        >
          Open
        </Link>
        {canControl && (mission.status === 'running' || mission.status === 'waiting') && (
          <Button busy={busy} onClick={() => void control('pause')}>
            Pause
          </Button>
        )}
        {canControl && mission.status === 'paused' && (
          <Button busy={busy} onClick={() => void control('resume')}>
            Resume
          </Button>
        )}
      </div>
    </aside>
  )
}

export function MissionControl() {
  const isMobile = useIsMobile()
  const me = useCurrentMe()
  const navigate = useNavigate()
  const search = useSearch({ from: '/' })
  const agents = useAgentIndex()
  const agentList = useAgents()
  const review = useReview('all')
  const active = useMissions({ status: 'queued,running,waiting,paused' }, { live: true })
  const finished = useMissions({ status: [...TERMINAL_MISSION].join(','), limit: 100 })
  const [liveNow, setLiveNow] = useState<string | null>(null)

  const activeList = useMemo(() => {
    const items = [...(active.data?.items ?? [])]
    // Recent activity first: waiting for you, then running by start time.
    const rank = (m: Mission) => (m.status === 'waiting' ? 0 : m.status === 'running' ? 1 : m.status === 'paused' ? 2 : 3)
    return items.sort((a, b) => rank(a) - rank(b) || Date.parse(b.started_at ?? b.created_at) - Date.parse(a.started_at ?? a.created_at))
  }, [active.data])
  const finishedList = useMemo(() => finished.data?.items ?? [], [finished.data])
  const minute = useNow(60_000)
  const kpis = useKpis(activeList, finishedList, minute)

  const selectedId = search.selected && activeList.some((m) => m.id === search.selected) ? search.selected : (activeList[0]?.id ?? null)
  const selected = activeList.find((m) => m.id === selectedId) ?? null
  const busyAgents = new Set(activeList.flatMap((m) => missionAgents(m)))
  const idle = (agentList.data?.items ?? [])
    .filter((a) => a.status !== 'disabled' && !busyAgents.has(a.name ?? '') && a.status !== 'working')
    .map((a) => a.name ?? a.id)
  const today = finishedList.filter((m) => isToday(m.ended_at))
  const decisions = review.data?.items ?? []
  const counts = review.data?.counts

  const select = (id: string) => {
    void navigate({ to: '/', search: { selected: id }, replace: true })
  }
  useHotkeys({
    j: () => {
      const i = activeList.findIndex((m) => m.id === selectedId)
      const next = activeList[Math.min(activeList.length - 1, i + 1)]
      if (next) select(next.id)
    },
    k: () => {
      const i = activeList.findIndex((m) => m.id === selectedId)
      const prev = activeList[Math.max(0, i - 1)]
      if (prev) select(prev.id)
    },
    enter: () => {
      if (selectedId) void navigate({ to: '/missions/$id', params: { id: selectedId } })
    },
  })

  if (isMobile) return <MobileHome />

  const working = activeList.filter((m) => m.status === 'running' || m.status === 'waiting').length
  const waiting = counts?.all ?? 0
  const lead = `${countWord(working)} ${working === 1 ? 'mission is' : 'missions are'} running. ${
    waiting === 0 ? 'Nothing is waiting for you.' : `${countWord(waiting)} ${waiting === 1 ? 'decision is' : 'decisions are'} waiting for you.`
  }`

  return (
    <>
      <TopBar crumbs={[{ label: me.workspace.name }, { label: 'Mission control' }]} />
      <main className="flex w-full max-w-[1320px] flex-col gap-[var(--section-gap)] px-10 pt-9 pb-14">
        <div className="flex flex-col gap-2">
          <h1 className="m-0 font-display-face text-display leading-[1.05]">
            {greetingFor(new Date())}, {firstName(me.user.name || me.user.username)}.
          </h1>
          <p className="m-0 text-[15px] text-text-2">{active.data && review.data ? lead : ' '}</p>
          <KpiStrip items={kpis} className="mt-[22px]" />
        </div>

        <section aria-labelledby="decisions" className="flex flex-col">
          <SectionHeader
            id="decisions"
            title="Decisions"
            right={
              <Link to="/review" className="text-text-3 no-underline hover:text-text">
                View all
              </Link>
            }
          />
          <div aria-live="polite" aria-relevant="additions">
            {review.error ? (
              <ErrorState error={review.error} className="mt-3" />
            ) : review.isPending ? (
              <div className="flex flex-col gap-2 py-3">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : decisions.length === 0 ? (
              <EmptyState title="No decision waiting">Writes and questions from agents land here first.</EmptyState>
            ) : (
              decisions.slice(0, 4).map((it) => <DecisionRow key={reviewItemId(it)} item={it} agents={agents} />)
            )}
            {decisions.length > 4 && (
              <Link to="/review" className="block py-3 text-meta text-text-3 no-underline hover:text-text">
                {decisions.length - 4} more in Review
              </Link>
            )}
          </div>
        </section>

        <div className="flex flex-wrap items-start gap-8">
          <section aria-labelledby="running" className="flex min-w-0 flex-[999_1_600px] flex-col">
            <SectionHeader id="running" title="Agents" right={<span>Sort by recent activity</span>} />
            {active.error ? (
              <ErrorState error={active.error} className="mt-3" />
            ) : active.isPending ? (
              <div className="flex flex-col gap-2 py-3">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : activeList.length === 0 ? (
              <EmptyState
                icon="missionControl"
                title="No agent is working right now"
                action={
                  <Link to="/missions/new" className="text-ui text-text no-underline">
                    <span className="inline-flex items-center gap-1.5">
                      <Icon name="plus" size={13} /> Start a mission
                    </span>
                  </Link>
                }
              >
                {idle.length > 0 && `${plural(idle.length, 'agent')} available: ${idle.slice(0, 6).map((s) => agents.label(s)).join(', ')}.`}
              </EmptyState>
            ) : (
              <AgentRows
                missions={activeList}
                idle={idle}
                selected={selectedId}
                onSelect={select}
                agents={agents}
                liveNow={liveNow}
              />
            )}
          </section>
          {selected && <Preview key={selected.id} mission={selected} agents={agents} onLiveNow={setLiveNow} />}
        </div>

        <section aria-labelledby="done" className="flex flex-col">
          <SectionHeader id="done" title="Finished today" />
          {finished.error ? (
            isNotServed(finished.error) ? (
              <NotServed what="Mission history" endpoint="GET /missions" compact />
            ) : (
              <ErrorState error={finished.error} className="mt-3" />
            )
          ) : today.length === 0 ? (
            <div className="py-3.5 text-ui text-text-3">{finished.isPending ? ' ' : 'Nothing finished yet today.'}</div>
          ) : (
            <ul className="m-0 flex list-none flex-wrap gap-x-7 gap-y-2 p-0 py-3.5 text-ui text-text-2">
              {today.map((m) => {
                const st = missionStatus(m.status)
                return (
                  <li key={m.id}>
                    <Link to="/missions/$id" params={{ id: m.id }} className="flex items-center gap-2 text-text-2 no-underline hover:text-text">
                      <StatusMark tone={st.tone} label={st.label} size={13} />
                      {m.title} · {m.status === 'succeeded' ? agents.label(missionAgent(m)) : st.label.toLowerCase()}
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </main>
    </>
  )
}

function greetingFor(d: Date): string {
  const h = d.getHours()
  return h < 5 ? 'Good evening' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}
