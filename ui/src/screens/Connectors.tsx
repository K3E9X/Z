import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useConnector, useConnectors, useOperations } from '@/api/queries'
import type { Connector, ConnectorTestResult, Operation } from '@/api/types'
import { useAgentIndex } from '@/components/Avatar'
import { ConnectorHealth } from '@/components/ConnectorHealth'
import { Dialog } from '@/components/Dialog'
import { Icon } from '@/components/Icon'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Checkbox, PageTitle, Skeleton, Toggle } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { ms, relative } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { useStepUp } from '@/shell/stepup'

export const KIND_LABEL: Record<string, string> = {
  mcp: 'MCP',
  openapi: 'Internal API',
  rest: 'REST',
  graphql: 'GraphQL',
  cli: 'CLI',
  database: 'Database',
  browser: 'Browser',
  webhook: 'Webhook',
  builtin: 'Built-in',
}

export const ADD_KINDS: { kind: string; label: string }[] = [
  { kind: 'mcp', label: 'MCP server' },
  { kind: 'openapi', label: 'OpenAPI / REST' },
  { kind: 'graphql', label: 'GraphQL' },
  { kind: 'cli', label: 'CLI' },
  { kind: 'database', label: 'Database (read-only)' },
  { kind: 'browser', label: 'Browser session' },
  { kind: 'webhook', label: 'Inbound webhook' },
]

function identities(c: Connector): string {
  const ids = (c.credentials ?? []).map((x) => x.identity ?? x.ref)
  if (ids.length) return ids.join(' · ')
  return c.pack ? `from the ${c.pack} pack` : c.kind
}

/** "GET /assets/{id}" from an OpenAPI source_ref, or the MCP tool name. */
function opParts(o: Operation): { method: string; path: string } {
  const m = /^(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(.+)$/i.exec(o.source_ref)
  if (m?.[1] && m[2]) return { method: m[1].toUpperCase(), path: m[2] }
  return {
    method: o.class === 'read' ? 'READ' : o.class === 'destructive' ? 'DESTR' : 'WRITE',
    path: o.id.replace(`${o.connector}.`, ''),
  }
}

const CLASS_TONE: Record<string, string> = { read: 'text-ok', write: 'text-wait', destructive: 'text-err' }

function ExposureDialog({
  connector,
  ops,
  open,
  onClose,
}: {
  connector: Connector
  ops: Operation[]
  open: boolean
  onClose: () => void
}) {
  const [sel, setSel] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(ops.map((o) => [o.id, o.proposed_exposed || o.exposed])),
  )
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const changes = ops.filter((o) => sel[o.id] !== o.exposed)
  const save = async () => {
    setBusy(true)
    try {
      await stepUp.run(
        () =>
          unwrap(
            api.POST('/connectors/{id}/approve-exposure', {
              params: { path: { id: connector.id } },
              body: { operations: ops.map((o) => ({ id: o.id, exposed: !!sel[o.id] })) },
            }),
          ),
        {
          what: 'Changing what agents can reach',
          detail: `${connector.display_name} · ${ops.filter((o) => sel[o.id]).length} of ${ops.length} operations exposed`,
        },
      )
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.connectors }),
        qc.invalidateQueries({ queryKey: qk.connector(connector.id) }),
        qc.invalidateQueries({ queryKey: qk.operations(connector.id) }),
      ])
      toast.ok(`${connector.display_name}: exposure confirmed`)
      onClose()
    } catch (e) {
      if (e instanceof Error && e.name === 'StepUpCancelled') return
      toast.error(e, 'Exposure not saved')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Review exposure · ${connector.display_name}`}
      eyebrow="Agents see nothing from this connector until you confirm each operation"
      width={720}
      footer={
        <>
          <span className="mr-auto self-center text-meta text-text-3">
            {changes.length} {changes.length === 1 ? 'change' : 'changes'} · re-authentication required
          </span>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Confirm exposure
          </Button>
        </>
      }
    >
      <p className="m-0 mb-3 text-meta text-text-2">
        Pre-ticked operations are what the pack proposes. Reads are callable by agents; writes only through a
        proposal you approve; destructive operations always need two approvers. The platform classifier is a
        floor: a class can be raised, never lowered.
      </p>
      <ul className="m-0 flex list-none flex-col p-0">
        {ops.map((o) => {
          const p = opParts(o)
          return (
            <li key={o.id} className="flex items-start gap-3 border-b border-line py-2.5">
              <Checkbox
                checked={!!sel[o.id]}
                onChange={(v) => {
                  setSel((cur) => ({ ...cur, [o.id]: v }))
                }}
                label={
                  <span className="flex flex-wrap items-baseline gap-x-3">
                    <span className={`w-14 font-mono text-[12.5px] ${CLASS_TONE[o.class] ?? ''}`}>
                      {p.method}
                    </span>
                    <span className="font-mono text-[12.5px]">{p.path}</span>
                    <span className={`text-meta ${CLASS_TONE[o.class] ?? 'text-text-3'}`}>{o.class}</span>
                    {o.class !== o.classifier_class && (
                      <span className="text-meta text-text-3">raised from {o.classifier_class}</span>
                    )}
                  </span>
                }
                description={o.description}
              />
            </li>
          )
        })}
      </ul>
    </Dialog>
  )
}

function OperationRow({ op, canManage }: { op: Operation; canManage: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [busy, setBusy] = useState(false)
  const p = opParts(op)
  const label = op.class === 'read' ? 'Free' : op.class === 'write' ? 'Approval' : 'Two approvers'
  const toggle = async (v: boolean) => {
    setBusy(true)
    try {
      await stepUp.run(
        () =>
          unwrap(api.PATCH('/operations/{id}', { params: { path: { id: op.id } }, body: { exposed: v } })),
        {
          what: v ? 'Exposing an operation to agents' : 'Hiding an operation from agents',
          destructive: op.class === 'destructive' && v,
          detail: `${op.id} (${op.class})`,
        },
      )
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.operations(op.connector) }),
        qc.invalidateQueries({ queryKey: qk.connectors }),
      ])
    } catch (e) {
      if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="grid grid-cols-[56px_minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-line py-3 last:border-b-0">
      <span className={`font-mono text-[12.5px] ${op.exposed ? CLASS_TONE[op.class] : 'text-text-3'}`}>
        {p.method}
      </span>
      <span
        className={`font-mono text-[12.5px] break-all ${op.exposed ? '' : 'text-text-3'}`}
        title={op.description}
      >
        {p.path}
      </span>
      <span
        className={`text-meta ${op.exposed ? (op.class === 'read' ? 'text-text-3' : CLASS_TONE[op.class]) : 'text-text-3'}`}
      >
        {op.exposed ? label : 'Not exposed'}
      </span>
      <Toggle
        checked={op.exposed}
        onChange={(v) => void toggle(v)}
        disabled={!canManage || busy}
        label={`Expose ${op.id}`}
      />
    </li>
  )
}

function Detail({ id }: { id: string }) {
  const c = useConnector(id)
  const ops = useOperations(id)
  const agents = useAgentIndex()
  const canManage = useCan('connectors:manage')
  const toast = useToast()
  const stepUp = useStepUp()
  const [reviewing, setReviewing] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<ConnectorTestResult | null>(null)
  if (!c.data) return <Skeleton className="h-80" />
  const con = c.data
  const read = con.credentials?.find((x) => x.purpose === 'read')
  const write = con.credentials?.find((x) => x.purpose === 'write')
  const list = ops.data ?? []
  const rank = (o: Operation) => (o.exposed ? 0 : 2) + (o.class === 'read' ? 0 : 1)
  const shown = [...list].sort((a, b) => rank(a) - rank(b)).slice(0, 10)
  const rate =
    con.rate_limit && typeof con.rate_limit.per_minute === 'number'
      ? `${con.rate_limit.per_minute} requests / minute`
      : '—'
  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      const r = await stepUp.run(
        () => unwrap(api.POST('/connectors/{id}/test', { params: { path: { id } }, body: {} })),
        {
          what: 'Testing a connector',
          detail: con.display_name,
        },
      )
      setResult(r)
    } catch (e) {
      if (isNotServed(e))
        toast.info('Connection tests are not available on this server yet (POST /connectors/{id}/test).')
      else if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e, 'Test failed')
    } finally {
      setTesting(false)
    }
  }
  return (
    <aside
      aria-label={`${con.display_name} details`}
      className="flex flex-col rounded-card border border-line bg-panel"
    >
      <div className="flex flex-col gap-1.5 border-b border-line px-5 py-4">
        <h2 className="m-0 text-[17px] font-semibold">{con.display_name}</h2>
        <p className="m-0 text-meta text-text-2">
          {con.kind === 'openapi'
            ? 'Imported from its OpenAPI spec. You choose which operations agents see.'
            : con.kind === 'mcp'
              ? 'An MCP server re-exposed under canonical operation ids, filtered.'
              : con.kind === 'browser'
                ? 'A recorded browser session replayed gateway-side; agents never see the session.'
                : `${KIND_LABEL[con.kind] ?? con.kind} connector${con.pack ? ` from the ${con.pack} pack` : ''}.`}
        </p>
      </div>
      {con.review_status === 'pending_review' && (
        <div className="flex flex-col gap-2 border-b border-line bg-wait-bg px-5 py-3.5">
          <span className="text-ui font-medium text-wait">Waiting for exposure review</span>
          <span className="text-meta text-text-2">
            {list.filter((o) => o.proposed_exposed).length} of {list.length} operations are proposed by the
            pack. Agents see none of them until an admin confirms.
          </span>
          {canManage && (
            <Button
              size="sm"
              variant="primary"
              className="self-start"
              onClick={() => setReviewing(true)}
              disabled={!ops.data}
            >
              Review exposure
            </Button>
          )}
        </div>
      )}
      <dl className="m-0 grid grid-cols-[96px_1fr] gap-x-4 gap-y-2.5 border-b border-line px-5 py-4 text-ui">
        <dt className="text-text-3">Read as</dt>
        <dd className="m-0 font-mono text-[12.5px]">
          {read ? (read.identity ?? read.ref) : '—'}
          {read?.scope === 'user' && (
            <span className="font-sans text-text-3"> · each user's own account</span>
          )}
        </dd>
        <dt className="text-text-3">Write as</dt>
        <dd className="m-0">
          <span className="font-mono text-[12.5px]">{write ? (write.identity ?? write.ref) : '—'}</span>
          {write && <span className="text-text-3"> · executor only</span>}
        </dd>
        <dt className="text-text-3">Secret</dt>
        <dd className="m-0">
          {read || write
            ? `${(read ?? write)?.backend ?? ''}${(read ?? write)?.rotated_at ? ` · rotated ${relative((read ?? write)?.rotated_at)}` : ''}`
            : 'No credential configured'}
        </dd>
        <dt className="text-text-3">Rate limit</dt>
        <dd className="m-0">{rate}</dd>
        <dt className="text-text-3">Data class</dt>
        <dd className="m-0 font-mono">{con.data_class}</dd>
        <dt className="text-text-3">Used by</dt>
        <dd className="m-0">
          {con.used_by.length ? con.used_by.map((a) => agents.label(a)).join(', ') : 'No agent'}
        </dd>
      </dl>
      <div className="flex flex-col gap-1 border-b border-line px-5 pt-4 pb-1">
        <div className="flex items-baseline justify-between">
          <h3 className="m-0 text-meta font-normal text-text-3">Operations</h3>
          <span className="text-sm text-text-3">
            {con.operations_exposed} of {con.operations_total} exposed
          </span>
        </div>
        <QueryState query={ops} what="Operations" endpoint="GET /connectors/{id}/operations">
          {() => (
            <ul className="m-0 list-none p-0">
              {shown.map((o) => (
                <OperationRow key={o.id} op={o} canManage={canManage && con.review_status === 'approved'} />
              ))}
              {list.length > shown.length && (
                <li className="py-2.5">
                  <button
                    type="button"
                    className="text-meta text-text-2 underline underline-offset-2"
                    onClick={() => setReviewing(true)}
                    disabled={!canManage}
                  >
                    All {list.length} operations
                  </button>
                </li>
              )}
            </ul>
          )}
        </QueryState>
      </div>
      {result && (
        <div
          role="status"
          className={`border-b border-line px-5 py-3 text-meta ${result.ok ? 'text-ok' : 'text-err'}`}
        >
          {result.ok ? 'Connection OK' : 'Connection failed'} · {ms(result.latency_ms)}
          {result.status_code ? ` · HTTP ${result.status_code}` : ''} ·{' '}
          <span className="text-text-2">{result.detail}</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2 px-5 py-4">
        {canManage && (
          <Button busy={testing} onClick={() => void test()}>
            Test connection
          </Button>
        )}
        <Link
          to="/history"
          search={{ q: con.name }}
          className="flex h-8 items-center rounded-btn border border-line-2 px-3.5 no-underline hover:bg-raised"
        >
          View calls
        </Link>
      </div>
      {reviewing && ops.data && (
        <ExposureDialog connector={con} ops={ops.data} open={reviewing} onClose={() => setReviewing(false)} />
      )}
    </aside>
  )
}

export function Connectors() {
  const q = useConnectors({ live: true })
  const search = useSearch({ from: '/connectors' })
  const navigate = useNavigate()
  const agents = useAgentIndex()
  const canManage = useCan('connectors:manage')
  const list = useMemo(() => q.data ?? [], [q.data])
  const selected =
    search.connector && list.some((c) => c.id === search.connector) ? search.connector : (list[0]?.id ?? null)
  const select = (id: string) => {
    void navigate({ to: '/connectors', search: { connector: id }, replace: true })
  }
  useHotkeys({
    j: () => {
      const i = list.findIndex((c) => c.id === selected)
      const n = list[Math.min(list.length - 1, i + 1)]
      if (n) select(n.id)
    },
    k: () => {
      const i = list.findIndex((c) => c.id === selected)
      const n = list[Math.max(0, i - 1)]
      if (n) select(n.id)
    },
  })
  return (
    <main className="flex w-full max-w-[1440px] flex-col gap-7 px-10 pt-11 pb-14 max-md:px-5">
      <PageTitle
        title="Connectors"
        lead="Every tool your agents can reach. Agents only read; writes go through a separate service account, after approval. Secrets never reach the agent."
        right={
          canManage ? (
            <div className="flex max-w-[560px] flex-col items-end gap-2">
              <span className="text-meta text-text-3">Add a connector</span>
              <div className="flex flex-wrap justify-end gap-1.5">
                {ADD_KINDS.map((k) => (
                  <Link
                    key={k.kind}
                    to="/connectors/new"
                    search={{ kind: k.kind }}
                    className="flex h-[34px] items-center rounded-btn border border-line-2 px-3 no-underline hover:bg-raised"
                  >
                    {k.label}
                  </Link>
                ))}
              </div>
            </div>
          ) : undefined
        }
      />
      <QueryState query={q} what="Connectors" endpoint="GET /connectors">
        {() =>
          list.length === 0 ? (
            <EmptyState
              icon="connectors"
              title="No connector yet"
              action={canManage ? <Link to="/connectors/new">Add the first one</Link> : undefined}
            >
              Connectors come from packs or are added here from a spec, an MCP server, a CLI or a recorded
              browser session.
            </EmptyState>
          ) : (
            <div className="flex flex-wrap items-start gap-7">
              <div className="min-w-0 flex-[999_1_640px] overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse">
                  <thead>
                    <tr className="border-b border-line text-sm text-text-3">
                      <th className="py-2.5 pr-3 font-normal">Tool</th>
                      <th className="py-2.5 pr-3 font-normal">Type</th>
                      <th className="py-2.5 pr-3 font-normal">Health</th>
                      <th className="py-2.5 pr-3 text-right font-normal">p95</th>
                      <th className="py-2.5 pr-3 font-normal">Operations</th>
                      <th className="py-2.5 font-normal">Used by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((c) => {
                      const isSel = c.id === selected
                      return (
                        <tr
                          key={c.id}
                          aria-selected={isSel}
                          tabIndex={0}
                          onClick={() => select(c.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') select(c.id)
                          }}
                          className={`cursor-pointer border-b border-line ${isSel ? 'bg-raised shadow-[inset_2px_0_0_var(--text)]' : 'hover:bg-panel'}`}
                        >
                          <td className="py-[var(--cell-y)] pr-3 pl-2.5">
                            <div className="flex flex-col">
                              <span className="font-medium">{c.display_name}</span>
                              <span className="truncate text-meta text-text-3">{identities(c)}</span>
                            </div>
                          </td>
                          <td className="pr-3">{KIND_LABEL[c.kind] ?? c.kind}</td>
                          <td className="pr-3">
                            <ConnectorHealth connector={c} />
                          </td>
                          <td className="pr-3 text-right font-mono text-[12.5px]">
                            {c.health.latency_p95_ms ? ms(c.health.latency_p95_ms) : '—'}
                          </td>
                          <td className="pr-3 font-mono text-[12.5px]">
                            {c.operations_exposed} / {c.operations_total}
                          </td>
                          <td className="truncate">
                            {c.used_by.length === 0
                              ? '—'
                              : c.used_by.length <= 2
                                ? c.used_by.map((a) => agents.label(a)).join(', ')
                                : `${c.used_by.length} agents`}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div className="w-full flex-[1_1_340px] lg:max-w-[360px]">
                {selected ? <Detail key={selected} id={selected} /> : null}
              </div>
            </div>
          )
        }
      </QueryState>
      <p className="m-0 flex items-center gap-2 text-meta text-text-3">
        <Icon name="lock" size={12} /> Credentials are references to your secrets backend; Z never stores or
        shows their values.
      </p>
    </main>
  )
}
