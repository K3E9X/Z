import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ZEvent } from '@/api/events.gen'
import { artifactUrl, useArtifactText } from '@/api/artifacts'
import { useReplay } from '@/api/queries'
import { useAgentIndex } from '@/components/Avatar'
import { DiffViewer } from '@/components/DiffViewer'
import { TimelineRow } from '@/components/EventTimeline'
import { Icon } from '@/components/Icon'
import { PlanChecklist } from '@/components/PlanChecklist'
import { EmptyState, QueryState } from '@/components/states'
import { StatusText } from '@/components/StatusMark'
import { Button, Select } from '@/components/ui'
import { clock, clockDuration, compactNumber } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { planSteps } from '@/lib/mission'
import { missionStatus } from '@/lib/status'
import { emptyTimeline, reduceEvents } from '@/lib/timeline'
import { TopBar } from '@/shell/TopBar'

function markerTone(e: ZEvent): string | null {
  if (
    e.type === 'denied' ||
    (e.type === 'status' && ['failed', 'timed_out', 'budget_exceeded'].includes(e.payload.to))
  )
    return 'bg-err'
  if (e.type === 'question' || e.type === 'action_proposed') return 'bg-wait'
  if (e.type === 'judge' && e.payload.verdict !== 'clean') return 'bg-wait'
  if (e.type === 'file_edit' || e.type === 'tool_call' || e.type === 'terminal' || e.type === 'browser')
    return 'bg-text-3'
  return null
}

function DiffPane({ id }: { id: string }) {
  const q = useArtifactText(id)
  if (!q.data)
    return <div className="text-meta text-text-3">{q.error ? 'Diff not available' : 'Loading…'}</div>
  return <DiffViewer diff={q.data} maxHeight={300} />
}

/** Trace replay (docs/09 §5): a scrubber over a finished mission, synced with terminal, browser and diffs. */
export function Replay() {
  const { id } = useParams({ from: '/missions/$id/replay' })
  const search = useSearch({ from: '/missions/$id/replay' })
  const navigate = useNavigate()
  const q = useReplay(id)
  const agents = useAgentIndex()
  const events = useMemo(
    () => [...((q.data?.events ?? []) as unknown as ZEvent[])].sort((a, b) => a.seq - b.seq),
    [q.data],
  )
  const [pos, setPos] = useState<number>(() => search.seq ?? Number.MAX_SAFE_INTEGER)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(4)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const idx = Math.min(
    events.length - 1,
    Math.max(0, pos === Number.MAX_SAFE_INTEGER ? events.length - 1 : pos),
  )
  const upto = useMemo(() => reduceEvents(emptyTimeline(), events.slice(0, idx + 1)), [events, idx])
  const t0 = events[0] ? Date.parse(events[0].ts) : 0
  const tEnd = events.at(-1) ? Date.parse(events.at(-1)?.ts ?? '') : 0
  const cur = events[idx]

  useEffect(() => {
    if (!playing) return
    timer.current = setInterval(() => {
      setPos((p) => {
        const next = Math.min(events.length - 1, (p === Number.MAX_SAFE_INTEGER ? 0 : p) + 1)
        if (next >= events.length - 1) setPlaying(false)
        return next
      })
    }, 1000 / speed)
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [playing, speed, events.length])

  useHotkeys({
    space: () => setPlaying((p) => !p),
    arrowright: () => setPos(Math.min(events.length - 1, idx + 1)),
    arrowleft: () => setPos(Math.max(0, idx - 1)),
    home: () => setPos(0),
    end: () => setPos(events.length - 1),
  })

  const lastOf = <K extends ZEvent['type']>(type: K) => {
    for (let i = idx; i >= 0; i--) {
      const e = events[i]
      if (e?.type === type) return e as Extract<ZEvent, { type: K }>
    }
    return null
  }
  const term = lastOf('terminal')
  const shot = (() => {
    for (let i = idx; i >= 0; i--) {
      const e = events[i]
      if (e?.type === 'browser' && e.payload.screenshot_ref) return e
    }
    return null
  })()
  const edit = lastOf('file_edit')
  const m = q.data?.mission
  const recent = upto.items.slice(-40)
  const list = useRef<HTMLDivElement | null>(null)
  // The activity column follows the scrubber: the newest item at this point stays in view.
  useEffect(() => {
    const el = list.current
    if (el) el.scrollTop = el.scrollHeight
  }, [idx, recent.length])

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        crumbs={[
          { label: 'Mission control', to: '/' },
          ...(m ? [{ label: m.title, to: `/missions/${m.id}` }] : []),
          { label: 'Replay' },
        ]}
      />
      <QueryState query={q} what="Mission replay" endpoint="GET /missions/{id}/replay">
        {() =>
          events.length === 0 || !m ? (
            <EmptyState className="px-8" title="Nothing to replay">
              This mission has no events yet.
            </EmptyState>
          ) : (
            <main className="flex flex-col gap-5 px-7 py-6">
              <div className="flex flex-wrap items-center gap-4">
                <h1 className="m-0 text-xl font-semibold">{m.title}</h1>
                <StatusText tone={missionStatus(m.status).tone} label={missionStatus(m.status).label} />
                <span className="font-mono text-sm text-text-3">
                  {events.length} events · {clockDuration((tEnd - t0) / 1000)} ·{' '}
                  {compactNumber(m.tokens_in + m.tokens_out)} tokens · {q.data?.artifacts.length ?? 0}{' '}
                  artifacts
                </span>
              </div>
              <section
                aria-label="Scrubber"
                className="flex flex-col gap-2 rounded-panel border border-line bg-panel px-5 py-4"
              >
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    aria-label={playing ? 'Pause' : 'Play'}
                    onClick={() => {
                      if (idx >= events.length - 1) setPos(0)
                      setPlaying((p) => !p)
                    }}
                    className="flex h-9 w-9 flex-none items-center justify-center rounded-full border border-line-2 hover:bg-raised"
                  >
                    <Icon name={playing ? 'pause' : 'play'} size={13} />
                  </button>
                  <div className="relative flex-1">
                    <div className="pointer-events-none absolute inset-x-0 top-0 h-2" aria-hidden="true">
                      {events.map((e, i) => {
                        const tone = markerTone(e)
                        if (!tone) return null
                        return (
                          <span
                            key={e.seq}
                            className={`absolute top-0 h-2 w-[2px] ${tone}`}
                            style={{ left: `${(i / Math.max(1, events.length - 1)) * 100}%` }}
                          />
                        )
                      })}
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={events.length - 1}
                      value={idx}
                      onChange={(e) => {
                        setPlaying(false)
                        setPos(Number(e.target.value))
                      }}
                      aria-label="Position in the mission"
                      aria-valuetext={
                        cur
                          ? `${clock(cur.ts, true)}, event ${idx + 1} of ${events.length}: ${cur.type}`
                          : undefined
                      }
                      className="mt-3 w-full accent-[var(--text)]"
                    />
                  </div>
                  <span className="w-[120px] text-right font-mono text-sm text-text-2">
                    {cur ? clockDuration((Date.parse(cur.ts) - t0) / 1000) : '00:00'} /{' '}
                    {clockDuration((tEnd - t0) / 1000)}
                  </span>
                  <Select
                    aria-label="Speed"
                    value={String(speed)}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                    className="h-8 w-[84px]"
                  >
                    {[1, 4, 10, 30].map((s) => (
                      <option key={s} value={s}>
                        {s}×
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="flex flex-wrap gap-x-4 text-sm text-text-3">
                  <span>
                    Event {idx + 1} of {events.length} · <span className="font-mono">{cur?.type}</span> ·{' '}
                    {cur ? clock(cur.ts, true) : ''}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-[2px] bg-err" aria-hidden="true" /> blocked or failed
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-[2px] bg-wait" aria-hidden="true" /> question, write, judge flag
                  </span>
                  <span>Space play · ← → step</span>
                </div>
              </section>
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <section
                  aria-label="Activity up to this point"
                  className="flex min-w-0 flex-col rounded-panel border border-line px-5 py-3"
                >
                  <div className="flex items-baseline justify-between pb-1">
                    <h2 className="m-0 text-ui font-medium">Activity</h2>
                    <span className="text-sm text-text-3">{upto.items.length} items so far</span>
                  </div>
                  <div ref={list} className="scrollbar-thin max-h-[560px] overflow-y-auto">
                    {recent.map((it) => (
                      <TimelineRow key={it.key} item={it} timeline={upto} agents={agents} showAgent />
                    ))}
                  </div>
                </section>
                <div className="flex min-w-0 flex-col gap-5">
                  <section
                    aria-label="Plan at this point"
                    className="rounded-panel border border-line px-5 py-4"
                  >
                    <h2 className="m-0 mb-3 text-ui font-medium">Plan</h2>
                    <PlanChecklist steps={upto.plan ?? planSteps(m)} inputs={m.inputs} />
                  </section>
                  <section aria-label="Terminal" className="rounded-panel border border-line px-5 py-4">
                    <h2 className="m-0 mb-2 text-ui font-medium">Terminal</h2>
                    {term ? (
                      <pre className="scrollbar-thin m-0 max-h-[220px] overflow-auto rounded-btn border border-line bg-code px-3 py-2 font-mono text-xs leading-[1.6] text-text-2">
                        $ {term.payload.command}
                        {'\n'}
                        {term.payload.output_tail ?? ''}
                      </pre>
                    ) : (
                      <span className="text-meta text-text-3">No command yet at this point.</span>
                    )}
                  </section>
                  <section aria-label="Browser" className="rounded-panel border border-line px-5 py-4">
                    <h2 className="m-0 mb-2 text-ui font-medium">Browser</h2>
                    {shot?.type === 'browser' && shot.payload.screenshot_ref ? (
                      <img
                        src={artifactUrl(shot.payload.screenshot_ref)}
                        alt={`Screenshot: ${shot.payload.action}`}
                        className="max-h-[260px] rounded-btn border border-line"
                      />
                    ) : (
                      <span className="text-meta text-text-3">No screenshot yet at this point.</span>
                    )}
                  </section>
                  <section aria-label="Last diff" className="rounded-panel border border-line px-5 py-4">
                    <h2 className="m-0 mb-2 text-ui font-medium">
                      Last change{edit ? ` · ${edit.payload.path}` : ''}
                    </h2>
                    {edit?.payload.diff_ref ? (
                      <DiffPane id={edit.payload.diff_ref} />
                    ) : (
                      <span className="text-meta text-text-3">No file changed yet at this point.</span>
                    )}
                  </section>
                </div>
              </div>
              <div>
                <Button onClick={() => void navigate({ to: '/missions/$id', params: { id } })}>
                  Back to the mission
                </Button>
              </div>
            </main>
          )
        }
      </QueryState>
    </div>
  )
}
