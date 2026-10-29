import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { saveBlob } from '@/api/artifacts'
import { api, rawFetch, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { useAudit } from '@/api/queries'
import type { AuditEvent } from '@/api/types'
import { diffTexts, DiffViewer } from '@/components/DiffViewer'
import { Icon } from '@/components/Icon'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Input, Select } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { clock, dateTime, shortHash, titleCase } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { useNow } from '@/lib/useMedia'
import { useStepUp } from '@/shell/stepup'

export type AuditCategory = 'approval' | 'write' | 'guardrail' | 'edit' | 'read' | 'check' | 'mission' | 'auth' | 'admin' | 'config' | 'other'

const CATEGORY_LABEL: Record<AuditCategory, string> = {
  approval: 'Approval',
  write: 'Write proposed',
  guardrail: 'Guardrail',
  edit: 'Edit',
  read: 'Read',
  check: 'Check',
  mission: 'Mission',
  auth: 'Sign-in',
  admin: 'Admin',
  config: 'Configuration',
  other: 'Other',
}
const CATEGORY_TONE: Partial<Record<AuditCategory, string>> = { approval: 'text-ok', write: 'text-wait', guardrail: 'text-err', check: 'text-text' }

export function categoryOf(e: Pick<AuditEvent, 'action' | 'result'>): AuditCategory {
  const a = e.action
  if (e.result === 'denied' || /denied|guardrail|blocked/.test(a)) return 'guardrail'
  if (/^action\.(approved|rejected|applied)/.test(a)) return 'approval'
  if (/^action\.(proposed)/.test(a)) return 'write'
  if (/^action\.edited|plan_edited|\.edited$|file_edit/.test(a)) return 'edit'
  if (/^tool\.|^gateway\./.test(a)) return 'read'
  if (/^judge\./.test(a)) return 'check'
  if (/^mission\.|^job\.|^question\./.test(a)) return 'mission'
  if (/^auth\.|session/.test(a)) return 'auth'
  if (/^admin\.|role|binding|group_mapping|user\./.test(a)) return 'admin'
  if (/^connector|^operation|^catalog|^agent|^skill|^playbook|^guardrail|^provider|^routing|^budget/.test(a)) return 'config'
  return 'other'
}

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : null
}

export function describe(e: AuditEvent): string {
  const after = (e.after ?? {}) as Record<string, unknown>
  const verb = titleCase(e.action.split('.').slice(1).join(' ').replace(/_/g, ' ') || e.action)
  const noun = e.action.split('.')[0] ?? ''
  const detail = str(after.title) ?? str(after.operation) ?? str(after.summary) ?? str(after.reason) ?? null
  const subject = noun === 'action' ? 'Write' : titleCase(noun)
  return `${subject} ${verb.toLowerCase()}${detail ? ` · ${detail}` : ''}${e.result !== 'success' ? ` (${e.result})` : ''}`
}

function actorLabel(a: string): string {
  if (a.startsWith('agent:')) return titleCase(a.slice(6))
  if (a.startsWith('system:')) return `System (${a.slice(7)})`
  if (a.startsWith('local:')) return `${a.slice(6)} (local account)`
  return a
}

const RANGES: Record<string, { label: string; hours: number | null }> = {
  '1h': { label: 'Last hour', hours: 1 },
  '24h': { label: 'Last 24 hours', hours: 24 },
  '7d': { label: 'Last 7 days', hours: 168 },
  '30d': { label: 'Last 30 days', hours: 720 },
  all: { label: 'All time', hours: null },
}

function Detail({ e }: { e: AuditEvent }) {
  const missionId = e.target_type === 'mission' ? e.target_id : str((e.after as Record<string, unknown> | null)?.mission)
  const before = e.before ? JSON.stringify(e.before, null, 2) : ''
  const after = e.after ? JSON.stringify(e.after, null, 2) : ''
  return (
    <aside aria-label="Event detail" className="flex flex-col rounded-card border border-line bg-panel">
      <div className="flex flex-col gap-1 border-b border-line px-5 py-4">
        <span className="text-meta text-text-3">Event #{e.seq}</span>
        <h2 className="m-0 text-[15px] font-semibold">{describe(e)}</h2>
      </div>
      <dl className="m-0 grid grid-cols-[96px_1fr] gap-x-4 gap-y-2.5 border-b border-line px-5 py-4 text-ui">
        <dt className="text-text-3">When</dt>
        <dd className="m-0 font-mono text-[12.5px]">{dateTime(e.ts)} UTC</dd>
        <dt className="text-text-3">Actor</dt>
        <dd className="m-0">
          {actorLabel(e.actor)}
          {e.session_id ? <span className="text-text-3">, via SSO</span> : null}
        </dd>
        <dt className="text-text-3">Executed as</dt>
        <dd className="m-0 font-mono text-[12.5px]">{e.runs_as ?? '—'}</dd>
        <dt className="text-text-3">Target</dt>
        <dd className="m-0 font-mono text-[12.5px] break-all">
          {e.target_type}
          {e.target_id ? `/${e.target_id}` : ''}
        </dd>
        {e.team && (
          <>
            <dt className="text-text-3">Team</dt>
            <dd className="m-0">{e.team}</dd>
          </>
        )}
        <dt className="text-text-3">Result</dt>
        <dd className={`m-0 ${e.result === 'success' ? '' : 'text-err'}`}>{e.result}</dd>
        {e.actor_ip && (
          <>
            <dt className="text-text-3">IP</dt>
            <dd className="m-0 font-mono text-[12.5px]">{e.actor_ip}</dd>
          </>
        )}
        {e.request_id && (
          <>
            <dt className="text-text-3">Request</dt>
            <dd className="m-0 font-mono text-[12.5px]">{e.request_id.slice(0, 16)}</dd>
          </>
        )}
        <dt className="text-text-3">Hash</dt>
        <dd className="m-0 font-mono text-[12.5px]" title={e.hash}>
          {shortHash(e.hash)}
        </dd>
        <dt className="text-text-3">Previous</dt>
        <dd className="m-0 font-mono text-[12.5px] text-text-3" title={e.prev_hash}>
          {shortHash(e.prev_hash)}
        </dd>
      </dl>
      {(before || after) && (
        <div className="border-b border-line px-5 py-4">
          <h3 className="m-0 mb-2 text-meta font-normal text-text-3">{before ? 'Before → after' : 'Recorded values'}</h3>
          <DiffViewer files={diffTexts(e.target_type, before, after, 50)} showFileHeader={false} lineNumbers={false} maxHeight={260} />
        </div>
      )}
      {missionId && (
        <div className="flex flex-col gap-2 px-5 py-4">
          <span className="text-meta text-text-3">Replay the mission</span>
          <Link
            to="/missions/$id/replay"
            params={{ id: missionId }}
            className="flex items-center gap-3 rounded-btn no-underline"
            aria-label="Open the replay of this mission"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full border border-line-2">
              <Icon name="play" size={12} />
            </span>
            <span className="h-1 flex-1 rounded-full bg-line-2">
              <span className="block h-1 w-1/3 rounded-full bg-text" />
            </span>
            <span className="font-mono text-sm text-text-2">open</span>
          </Link>
        </div>
      )}
    </aside>
  )
}

export function History() {
  const search = useSearch({ from: '/history' })
  const navigate = useNavigate()
  const toast = useToast()
  const stepUp = useStepUp()
  const canExport = useCan('audit:export')
  const canReadAll = useCan('audit:read')
  const range = search.range && RANGES[search.range] ? search.range : '24h'
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [pages, setPages] = useState<AuditEvent[][]>([])
  const [q, setQ] = useState(search.q ?? '')
  const type = (search.type ?? 'all') as AuditCategory | 'all'
  const hours = RANGES[range]?.hours ?? null
  const minute = useNow(60_000)
  const from = useMemo(() => (hours && minute ? new Date(minute - hours * 3600_000).toISOString() : undefined), [hours, minute])
  const audit = useAudit(from ? { from } : {}, cursor)
  const [exporting, setExporting] = useState(false)

  const events = useMemo(() => {
    const seen = new Set<number>()
    const all = [...(audit.data?.items ?? []), ...pages.flat()].filter((e) => (seen.has(e.seq) ? false : (seen.add(e.seq), true)))
    const needle = q.trim().toLowerCase()
    return all
      .filter((e) => type === 'all' || categoryOf(e) === type)
      .filter((e) => !needle || [e.actor, e.action, e.target_type, e.target_id ?? '', e.runs_as ?? '', JSON.stringify(e.after ?? {})].some((x) => x.toLowerCase().includes(needle)))
      .sort((a, b) => b.seq - a.seq)
  }, [audit.data, pages, q, type])
  const selected = events.find((e) => e.seq === search.seq) ?? events[0] ?? null

  const set = (patch: Record<string, string | number | undefined>) => {
    setPages([])
    setCursor(undefined)
    void navigate({ to: '/history', search: { ...search, ...patch }, replace: true })
  }
  useHotkeys({
    j: () => {
      const i = events.findIndex((e) => e.seq === selected?.seq)
      const n = events[Math.min(events.length - 1, i + 1)]
      if (n) void navigate({ to: '/history', search: { ...search, seq: n.seq }, replace: true })
    },
    k: () => {
      const i = events.findIndex((e) => e.seq === selected?.seq)
      const n = events[Math.max(0, i - 1)]
      if (n) void navigate({ to: '/history', search: { ...search, seq: n.seq }, replace: true })
    },
  })

  const exportLog = async () => {
    setExporting(true)
    try {
      const res = await stepUp.run(() => rawFetch(`/audit/export?format=json${from ? `&from=${encodeURIComponent(from)}` : ''}`), {
        what: 'Exporting the full audit log',
        detail: `${RANGES[range]?.label ?? ''} · JSON lines · the export itself is audited`,
      })
      saveBlob(await res.blob(), `z-audit-${new Date().toISOString().slice(0, 10)}.jsonl`)
    } catch (e) {
      if (isNotServed(e)) toast.info('Audit export is not available on this server yet.')
      else if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e, 'Export failed')
    } finally {
      setExporting(false)
    }
  }
  const loadOlder = async () => {
    const next = audit.data?.next_cursor
    if (!next) return
    try {
      const page = await unwrap(api.GET('/audit', { params: { query: { limit: 200, cursor: next, ...(from ? { from } : {}) } } }))
      setPages((p) => [...p, page.items])
      setCursor(undefined)
      if (!page.next_cursor) toast.info('This is the oldest event in the range.')
    } catch (e) {
      toast.error(e)
    }
  }

  return (
    <main className="flex w-full max-w-[1440px] flex-col gap-5 px-8 pt-7 pb-14 max-md:px-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0 text-xl font-semibold">History and audit</h1>
        <div className="flex gap-2">
          {canExport && (
            <Button busy={exporting} onClick={() => void exportLog()}>
              Export audit log
            </Button>
          )}
          <Button onClick={() => saveBlob(new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' }), 'z-history.json')}>Download JSON</Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Input
          data-search
          aria-label="Filter by agent, tool, resource"
          placeholder="Filter by agent, tool, resource…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="w-[400px] max-w-full"
        />
        <Select aria-label="Time range" value={range} onChange={(e) => set({ range: e.target.value })} className="w-[160px]">
          {Object.entries(RANGES).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </Select>
        <Select aria-label="Event type" value={type} onChange={(e) => set({ type: e.target.value === 'all' ? undefined : e.target.value })} className="w-[180px]">
          <option value="all">All types</option>
          {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        {!canReadAll && <span className="self-center text-meta text-text-3">You see the events about you; auditors and admins see everything.</span>}
      </div>
      <QueryState query={audit} what="History" endpoint="GET /audit">
        {() =>
          events.length === 0 ? (
            <EmptyState icon="history" title="No event in this range">Every API mutation, approval, tool call and denied action lands here, hash-chained.</EmptyState>
          ) : (
            <div className="flex flex-wrap items-start gap-6">
              <div className="min-w-0 flex-[999_1_640px] overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse">
                  <thead>
                    <tr className="border-y border-line text-sm text-text-3">
                      <th className="px-3 py-2.5 font-normal">Time</th>
                      <th className="py-2.5 pr-3 font-normal">Actor</th>
                      <th className="py-2.5 pr-3 font-normal">Type</th>
                      <th className="py-2.5 pr-3 font-normal">Event</th>
                      <th className="py-2.5 pr-3 font-normal">Resource</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((e) => {
                      const cat = categoryOf(e)
                      const isSel = e.seq === selected?.seq
                      return (
                        <tr
                          key={e.seq}
                          tabIndex={0}
                          aria-selected={isSel}
                          onClick={() => void navigate({ to: '/history', search: { ...search, seq: e.seq }, replace: true })}
                          onKeyDown={(ev) => {
                            if (ev.key === 'Enter') void navigate({ to: '/history', search: { ...search, seq: e.seq }, replace: true })
                          }}
                          className={`cursor-pointer border-b border-line align-top ${isSel ? 'bg-raised' : 'hover:bg-panel'}`}
                        >
                          <td className="px-3 py-[var(--cell-y)] font-mono text-[12.5px] whitespace-nowrap" title={dateTime(e.ts)}>
                            {clock(e.ts, true)}
                          </td>
                          <td className="py-[var(--cell-y)] pr-3">{actorLabel(e.actor)}</td>
                          <td className={`py-[var(--cell-y)] pr-3 ${CATEGORY_TONE[cat] ?? ''}`}>{CATEGORY_LABEL[cat]}</td>
                          <td className="py-[var(--cell-y)] pr-3">{describe(e)}</td>
                          <td className="py-[var(--cell-y)] pr-3 font-mono text-[12.5px] break-all">{e.target_id ? `${e.target_type}/${e.target_id.slice(0, 18)}` : e.target_type}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {audit.data?.next_cursor && (
                  <Button size="sm" className="mt-3" onClick={() => void loadOlder()}>
                    Load older events
                  </Button>
                )}
              </div>
              <div className="w-full flex-[1_1_320px] lg:max-w-[340px]">{selected && <Detail e={selected} />}</div>
            </div>
          )
        }
      </QueryState>
    </main>
  )
}
