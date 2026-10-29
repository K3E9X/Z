import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import {
  qk,
  useBudgets,
  useInferenceHealth,
  useProviders,
  useRoutingPolicy,
  useUsage,
  type UsageBucketSize,
} from '@/api/queries'
import type { Budget, InferenceModel, Usage } from '@/api/types'
import { useAgentIndex } from '@/components/Avatar'
import { HBars, Legend, StackedBars, TokenMeter } from '@/components/charts'
import { Dialog } from '@/components/Dialog'
import { KpiStrip, type Kpi } from '@/components/KpiStrip'
import { NotServed, QueryState } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Checkbox, Field, Input, PageTitle, Select, Tabs, Toggle } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { clock, compactNumber, grouped, ms, percent, shortDate } from '@/lib/format'
import { useNow } from '@/lib/useMedia'
import { useStepUp } from '@/shell/stepup'
import { InferenceKeyPanel } from './InferenceKey'

type Range = '1h' | '24h' | '7d' | '30d'
const RANGE: Record<Range, { hours: number; bucket: UsageBucketSize }> = {
  '1h': { hours: 1, bucket: 'minute' },
  '24h': { hours: 24, bucket: 'hour' },
  '7d': { hours: 168, bucket: 'day' },
  '30d': { hours: 720, bucket: 'day' },
}

function totals(u: Usage | undefined) {
  const s = u?.series ?? []
  return {
    tin: s.reduce((n, x) => n + x.tokens_in, 0),
    tout: s.reduce((n, x) => n + x.tokens_out, 0),
    cached: s.reduce((n, x) => n + x.cached_tokens, 0),
    requests: s.reduce((n, x) => n + x.requests, 0),
  }
}

function bucketLabel(ts: string, bucket: UsageBucketSize): string {
  return bucket === 'day' ? shortDate(ts) : clock(ts)
}

function Panel({
  title,
  right,
  children,
  className = '',
}: {
  title: string
  right?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <section
      aria-label={title}
      className={`flex min-w-0 flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5 ${className}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="m-0 text-[15px] font-semibold">{title}</h2>
        {right && <div className="flex items-center gap-2 text-meta text-text-3">{right}</div>}
      </div>
      {children}
    </section>
  )
}

function modelStatus(m: InferenceModel): { tone: 'ok' | 'wait' | 'err' | 'idle'; label: string } {
  if (m.status === 'healthy') return { tone: 'ok', label: 'Healthy' }
  if (m.status === 'down') return { tone: 'err', label: 'Down' }
  if (m.status === 'unknown') return { tone: 'idle', label: 'Unknown' }
  if (
    m.replicas_desired &&
    m.replicas_ready !== null &&
    m.replicas_ready !== undefined &&
    m.replicas_ready < m.replicas_desired
  )
    return {
      tone: 'wait',
      label: `${m.replicas_desired - m.replicas_ready} replica${m.replicas_desired - m.replicas_ready > 1 ? 's' : ''} restarting`,
    }
  return { tone: 'wait', label: 'Degraded' }
}

function ProviderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'internal' | 'external'>('external')
  const [aliases, setAliases] = useState('')
  const [classes, setClasses] = useState<string[]>(['C0', 'C1'])
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await stepUp.run(
        () =>
          unwrap(
            api.POST('/providers', {
              body: {
                name,
                kind,
                aliases: aliases.split(/[\s,]+/).filter(Boolean),
                data_classes: classes as ('C0' | 'C1' | 'C2' | 'C3')[],
                enabled: false,
              },
            }),
          ),
        { what: 'Adding a model provider', detail: `${name} · ${kind} · ${classes.join(', ')}` },
      )
      await qc.invalidateQueries({ queryKey: qk.providers })
      toast.ok('Provider added, disabled until you enable it')
      onClose()
    } catch (e) {
      if (!(e instanceof Error && e.name === 'StepUpCancelled'))
        toast.error(
          e,
          isNotServed(e) ? 'Providers are not available on this server yet' : 'Provider not added',
        )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a provider"
      eyebrow="Models and keys live in the LiteLLM config; Z only knows aliases and the data classes each provider may receive"
      width={560}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void save()}>
            Add, disabled
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          {(id) => (
            <Input
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="External API"
            />
          )}
        </Field>
        <Field label="Kind">
          {(id) => (
            <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as 'internal' | 'external')}>
              <option value="external">External</option>
              <option value="internal">Internal</option>
            </Select>
          )}
        </Field>
        <Field label="Aliases" hint="As declared in LiteLLM, e.g. ext-large" className="sm:col-span-2">
          {(id, d) => (
            <Input
              id={id}
              aria-describedby={d}
              value={aliases}
              onChange={(e) => setAliases(e.target.value)}
              className="font-mono"
            />
          )}
        </Field>
        <fieldset className="m-0 flex gap-4 border-none p-0 sm:col-span-2">
          <legend className="mb-2 text-meta text-text-2">Data classes allowed</legend>
          {['C0', 'C1', 'C2', 'C3'].map((c) => (
            <Checkbox
              key={c}
              checked={classes.includes(c)}
              disabled={kind === 'external' && (c === 'C2' || c === 'C3')}
              onChange={(v) => setClasses(v ? [...classes, c] : classes.filter((x) => x !== c))}
              label={<span className="font-mono">{c}</span>}
            />
          ))}
        </fieldset>
        {kind === 'external' && (
          <p className="m-0 text-meta text-text-3 sm:col-span-2">
            C2 and C3 never leave the internal cluster; external providers can only receive C0 and C1.
          </p>
        )}
      </div>
    </Dialog>
  )
}

function BudgetsPanel() {
  const budgets = useBudgets()
  const canManage = useCan('guardrails:manage')
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [editing, setEditing] = useState<Budget[] | null>(null)
  const save = async () => {
    if (!editing) return
    try {
      await stepUp.run(
        () =>
          unwrap(
            api.PUT('/budgets', {
              body: editing.map((b) => ({
                scope: b.scope,
                limit: b.limit,
                period: b.period,
                alert_at: b.alert_at,
                hard: b.hard,
              })),
            }),
          ),
        {
          what: 'Changing token budgets',
          detail: `${editing.length} budgets`,
        },
      )
      await qc.invalidateQueries({ queryKey: qk.budgets })
      toast.ok('Budgets saved')
      setEditing(null)
    } catch (e) {
      if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e)
    }
  }
  return (
    <Panel
      title="Budgets"
      right={
        canManage && budgets.data ? (
          <Button size="xs" onClick={() => setEditing(editing ? null : budgets.data.map((b) => ({ ...b })))}>
            {editing ? 'Cancel' : 'Edit'}
          </Button>
        ) : undefined
      }
    >
      <QueryState query={budgets} what="Budgets" endpoint="GET /budgets" compact>
        {() =>
          editing ? (
            <div className="flex flex-col gap-2">
              {editing.map((b, i) => (
                <div key={b.id} className="grid grid-cols-[1fr_120px_90px_70px] items-center gap-2 text-ui">
                  <span className="font-mono text-[12.5px]">{b.scope}</span>
                  <Input
                    type="number"
                    aria-label={`${b.scope} limit`}
                    value={b.limit}
                    onChange={(e) =>
                      setEditing(
                        editing.map((x, j) => (j === i ? { ...x, limit: Number(e.target.value) } : x)),
                      )
                    }
                    className="h-7 font-mono text-sm"
                  />
                  <span className="text-meta text-text-3">per {b.period}</span>
                  <Input
                    type="number"
                    aria-label={`${b.scope} alert at`}
                    value={Math.round(b.alert_at * 100)}
                    onChange={(e) =>
                      setEditing(
                        editing.map((x, j) =>
                          j === i ? { ...x, alert_at: Number(e.target.value) / 100 } : x,
                        ),
                      )
                    }
                    className="h-7 font-mono text-sm"
                  />
                </div>
              ))}
              <Button size="sm" variant="primary" className="self-end" onClick={() => void save()}>
                Save budgets
              </Button>
            </div>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {(budgets.data ?? []).map((b) => (
                <li key={b.id} className="flex flex-col gap-1.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="font-mono text-[12.5px]">{b.scope}</span>
                    <span className="font-mono text-[12.5px]">
                      {compactNumber(b.used)} / {compactNumber(b.limit)}{' '}
                      <span className="text-text-3">per {b.period}</span>
                    </span>
                  </div>
                  <TokenMeter used={b.used} cap={b.limit} />
                  <span className="text-sm text-text-3">
                    alert at {percent(b.alert_at)} · {b.hard ? 'hard limit, stops new jobs' : 'soft limit'}
                  </span>
                </li>
              ))}
              {(budgets.data ?? []).length === 0 && <li className="text-meta text-text-3">No budget set.</li>}
            </ul>
          )
        }
      </QueryState>
    </Panel>
  )
}

/** Inference and usage (docs/09 screen 10). */
export function Inference() {
  const search = useSearch({ from: '/inference' })
  const navigate = useNavigate()
  const range = (search.range && search.range in RANGE ? search.range : '24h') as Range
  const minute = useNow(60_000)
  const { from, to } = useMemo(() => {
    const end = minute
    return {
      from: new Date(end - RANGE[range].hours * 3600_000).toISOString(),
      to: new Date(end).toISOString(),
    }
  }, [minute, range])
  const bucket = RANGE[range].bucket
  const health = useInferenceHealth()
  const byModel = useUsage('model', from, to, bucket)
  const byAgent = useUsage('agent', from, to, bucket)
  const byMission = useUsage('mission', from, to, bucket)
  const providers = useProviders()
  const routing = useRoutingPolicy()
  const budgets = useBudgets()
  const agents = useAgentIndex()
  const canProviders = useCan('providers:manage')
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [table, setTable] = useState(false)
  const [addProvider, setAddProvider] = useState(false)

  const h = health.data
  const t = totals(byModel.data)
  const orgBudget = budgets.data?.find((b) => b.scope === 'org' && b.period === 'month')
  const models = h?.models ?? []
  const worstTtft = Math.max(0, ...models.map((m) => m.ttft_p95_ms ?? 0))
  const throughput = models.reduce((n, m) => n + (m.tokens_per_s ?? 0), 0)
  const modelSeries = byModel.data?.series ?? []
  const labels = (modelSeries[0]?.buckets ?? []).map((b) => bucketLabel(b.ts, bucket))
  const stack = modelSeries.map((s) => ({
    key: s.key,
    label: s.label ?? s.key,
    values: s.buckets.map((b) => b.tokens_in + b.tokens_out),
  }))
  const agentRows = (byAgent.data?.series ?? [])
    .map((s) => ({ label: agents.label(s.key), value: s.tokens_in + s.tokens_out }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)
  const agentTotal = agentRows.reduce((n, r) => n + r.value, 0)
  const missionRows = (byMission.data?.series ?? [])
    .sort((a, b) => b.tokens_in + b.tokens_out - (a.tokens_in + a.tokens_out))
    .slice(0, 6)
  const warnings = models.filter((m) => m.status !== 'healthy' && m.status !== 'unknown')

  const kpis: Kpi[] = [
    {
      label: `Tokens, last ${range}`,
      value: compactNumber(t.tin + t.tout),
      sub: `${grouped(t.requests)} requests`,
      unavailable: isNotServed(byModel.error),
    },
    {
      label: 'Input / output',
      value: `${compactNumber(t.tin)} / ${compactNumber(t.tout)}`,
      sub: t.tin ? `Prefix cache hit ${percent(t.cached / t.tin)}` : '—',
      unavailable: isNotServed(byModel.error),
    },
    {
      label: 'p95 first token',
      value: worstTtft ? ms(worstTtft) : '—',
      sub: 'slowest alias',
      unavailable: isNotServed(health.error),
    },
    {
      label: 'Throughput now',
      value: throughput ? `${compactNumber(throughput)} tok/s` : '—',
      sub: `${models.length} aliases`,
      unavailable: isNotServed(health.error),
    },
    {
      label: 'Tokens today',
      value: h ? compactNumber(h.tokens_today) : '—',
      sub: h ? `checked ${clock(h.checked_at)}` : '',
      unavailable: isNotServed(health.error),
    },
    {
      label: 'Monthly budget',
      value: orgBudget ? percent(orgBudget.used / orgBudget.limit) : '—',
      sub: orgBudget
        ? `${compactNumber(orgBudget.used)} of ${compactNumber(orgBudget.limit)} tokens`
        : 'No org budget',
      unavailable: isNotServed(budgets.error),
      tone: orgBudget && orgBudget.used / orgBudget.limit >= orgBudget.alert_at ? 'wait' : undefined,
    },
  ]

  const toggleFallback = async (v: boolean) => {
    if (!routing.data) return
    try {
      await stepUp.run(
        () =>
          unwrap(
            api.PUT('/routing-policy', { body: { ...routing.data, external_fallback_when_degraded: v } }),
          ),
        {
          what: 'Changing the routing policy',
          detail: `External fallback when inferX is degraded: ${v ? 'on (C0–C1 only)' : 'off'}`,
        },
      )
      await qc.invalidateQueries({ queryKey: qk.routing })
      toast.ok('Routing policy saved')
    } catch (e) {
      if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e)
    }
  }

  return (
    <main className="flex w-full max-w-[1440px] flex-col gap-6 px-10 pt-11 pb-14 max-md:px-5">
      <PageTitle
        title="Inference and usage"
        lead="Health of the inferX cluster, token consumption by agent and mission, and where each model call is allowed to go. Every call passes through the Z gateway."
        right={
          <Tabs<Range>
            label="Time range"
            variant="pill"
            className="rounded-btn border border-line-2 p-1"
            value={range}
            onChange={(r) => void navigate({ to: '/inference', search: { range: r }, replace: true })}
            items={(['1h', '24h', '7d', '30d'] as Range[]).map((r) => ({ id: r, label: r }))}
          />
        }
      />
      {h ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-panel border border-line px-5 py-3 text-ui"
        >
          <span className="flex items-center gap-2 font-medium">
            <Dot tone={h.status === 'healthy' ? 'ok' : h.status === 'down' ? 'err' : 'wait'} />
            inferX is {h.status === 'healthy' ? 'serving' : h.status}
          </span>
          <span className="text-text-2">
            {h.nodes.length} nodes · {models.length} models
          </span>
          <span className="text-text-2">
            Gateway: LiteLLM,{' '}
            {h.gateway.ok
              ? h.gateway.latency_ms
                ? ms(h.gateway.latency_ms)
                : 'reachable'
              : (h.gateway.error ?? 'unreachable')}
          </span>
          {!h.prometheus_configured && (
            <span className="text-text-3">No Prometheus configured: gateway-side numbers only</span>
          )}
          {warnings.length > 0 && (
            <span className="ml-auto flex items-center gap-2 text-wait">
              <Dot tone="wait" />
              {warnings.length} warning{warnings.length > 1 ? 's' : ''}:{' '}
              {warnings.map((w) => `${w.alias} ${modelStatus(w).label.toLowerCase()}`).join(', ')}
            </span>
          )}
        </div>
      ) : isNotServed(health.error) ? null /* said once, in the cluster panel below */ : (
        <QueryState query={health} what="inferX health" endpoint="GET /inference/health">
          {() => null}
        </QueryState>
      )}
      <KpiStrip items={kpis} minWidth={170} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.85fr)_minmax(0,1fr)]">
        <Panel
          title="Tokens per bucket, by model"
          right={
            byModel.data ? (
              <Button size="xs" onClick={() => setTable((v) => !v)} aria-pressed={table}>
                {table ? 'View as chart' : 'View as table'}
              </Button>
            ) : undefined
          }
        >
          <QueryState query={byModel} what="Usage by model" endpoint="GET /usage?group_by=model">
            {() => (
              <>
                <Legend
                  items={stack.map((s) => ({
                    label: s.label,
                    value: compactNumber(s.values.reduce((a, b) => a + b, 0)),
                  }))}
                />
                {labels.length ? (
                  <StackedBars title="Tokens by model" labels={labels} series={stack} showTable={table} />
                ) : (
                  <div className="text-meta text-text-3">No model call in this range.</div>
                )}
              </>
            )}
          </QueryState>
        </Panel>
        <Panel title="By agent" right={agentTotal ? `${compactNumber(agentTotal)} total` : undefined}>
          <QueryState query={byAgent} what="Usage by agent" endpoint="GET /usage?group_by=agent">
            {() =>
              agentRows.length ? (
                <HBars rows={agentRows} />
              ) : (
                <div className="text-meta text-text-3">No agent used tokens in this range.</div>
              )
            }
          </QueryState>
        </Panel>
      </div>
      <Panel title="inferX cluster" right={h ? 'vLLM · OpenAI-compatible API' : undefined}>
        <QueryState query={health} what="The cluster view" endpoint="GET /inference/health">
          {() => (
            <div className="flex flex-wrap gap-8">
              <div className="min-w-0 flex-[999_1_620px] overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-ui">
                  <thead>
                    <tr className="text-sm text-text-3">
                      {[
                        'Alias',
                        'Provider',
                        'Replicas',
                        'Queue',
                        'p95 first token',
                        'Tokens/s',
                        'Context',
                        'Tool calls',
                        'Status',
                      ].map((c) => (
                        <th key={c} className="py-2 pr-3 font-normal">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((m) => {
                      const st = modelStatus(m)
                      return (
                        <tr key={m.alias} className="border-t border-line">
                          <td className="py-3 pr-3 font-mono">{m.alias}</td>
                          <td className="pr-3 text-text-2">
                            {m.provider}
                            {m.kind === 'external' && <span className="text-wait"> · external</span>}
                          </td>
                          <td className="pr-3 font-mono">
                            {m.replicas_ready ?? '—'}
                            {m.replicas_desired ? ` / ${m.replicas_desired}` : ''}
                          </td>
                          <td className="pr-3 font-mono">{m.requests_waiting ?? '—'}</td>
                          <td className="pr-3 font-mono">{ms(m.ttft_p95_ms)}</td>
                          <td className="pr-3 font-mono">{m.tokens_per_s ? grouped(m.tokens_per_s) : '—'}</td>
                          <td className="pr-3 font-mono">{m.context ? compactNumber(m.context) : '—'}</td>
                          <td className="pr-3">
                            {m.tool_call_ok === null || m.tool_call_ok === undefined ? (
                              '—'
                            ) : m.tool_call_ok ? (
                              <span className="text-ok">OK</span>
                            ) : (
                              <span className="text-err">failing</span>
                            )}
                          </td>
                          <td>
                            <span className="flex items-center gap-1.5">
                              <Dot tone={st.tone} />
                              {st.label}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <dl className="m-0 mt-5 grid grid-cols-2 gap-4 border-t border-line pt-4 sm:grid-cols-4">
                  {[
                    [
                      'KV cache in use',
                      models.length ? percent(Math.max(...models.map((m) => m.kv_cache_usage ?? 0))) : '—',
                    ],
                    ['Prefix cache hit', percent(h?.prefix_cache_hit_rate)],
                    [
                      'Preemptions, 24h',
                      h?.preemptions_24h !== null && h?.preemptions_24h !== undefined
                        ? grouped(h.preemptions_24h)
                        : '—',
                    ],
                    [
                      'Tool-call parse errors',
                      models.some(
                        (m) =>
                          m.tool_call_parse_error_rate !== null && m.tool_call_parse_error_rate !== undefined,
                      )
                        ? percent(Math.max(...models.map((m) => m.tool_call_parse_error_rate ?? 0)), 1)
                        : '—',
                    ],
                  ].map(([k, v]) => (
                    <div key={k} className="flex flex-col gap-1">
                      <dt className="text-meta text-text-3">{k}</dt>
                      <dd className="m-0 text-[19px] font-medium">{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div className="flex min-w-[260px] flex-[1_1_280px] flex-col gap-1">
                <div className="flex justify-between text-sm text-text-3">
                  <span>GPU nodes</span>
                  <span>Utilization</span>
                </div>
                {(h?.nodes ?? []).length === 0 && (
                  <span className="text-meta text-text-3">
                    {h?.prometheus_configured
                      ? 'No GPU metric found.'
                      : 'Needs a Prometheus endpoint with DCGM metrics.'}
                  </span>
                )}
                {(h?.nodes ?? []).map((n) => {
                  const hot = (n.gpu_temp_c ?? 0) >= 80
                  return (
                    <div key={n.name} className="flex flex-col gap-1 border-b border-line py-3">
                      <div className="grid grid-cols-[72px_1fr_44px] items-center gap-3">
                        <span className="font-mono text-[12.5px]">{n.name}</span>
                        <TokenMeter
                          used={n.gpu_util ?? 0}
                          cap={1}
                          label={`${n.name} GPU utilization ${percent(n.gpu_util)}`}
                        />
                        <span className="text-right font-mono text-[12.5px]">{percent(n.gpu_util)}</span>
                      </div>
                      <span className={`pl-[84px] text-sm ${hot ? 'text-wait' : 'text-text-3'}`}>
                        {hot ? `Hot · ${n.gpu_temp_c ?? ''} °C · ` : ''}VRAM {percent(n.gpu_mem_used_ratio)}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </QueryState>
      </Panel>
      <div className="grid gap-6 xl:grid-cols-3">
        <Panel title="Missions by consumption" right="Used / cap">
          <QueryState
            query={byMission}
            what="Usage by mission"
            endpoint="GET /usage?group_by=mission"
            compact
          >
            {() =>
              missionRows.length === 0 ? (
                <div className="text-meta text-text-3">No mission used tokens in this range.</div>
              ) : (
                <ul className="m-0 flex list-none flex-col p-0">
                  {missionRows.map((s) => {
                    const used = s.tokens_in + s.tokens_out
                    return (
                      <li
                        key={s.key}
                        className="flex flex-col gap-1.5 border-b border-line py-3 last:border-b-0"
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <Link to="/missions/$id" params={{ id: s.key }} className="truncate no-underline">
                            {s.label ?? s.key}
                          </Link>
                          <span className="flex-none font-mono text-[12.5px]">
                            {compactNumber(used)}{' '}
                            <span className="text-text-3">/ {s.cap ? compactNumber(s.cap) : '∞'}</span>
                          </span>
                        </div>
                        <TokenMeter used={used} cap={s.cap} />
                      </li>
                    )
                  })}
                </ul>
              )
            }
          </QueryState>
        </Panel>
        <Panel
          title="Model providers"
          right={
            canProviders && !isNotServed(providers.error) ? (
              <Button size="xs" onClick={() => setAddProvider(true)}>
                Add a provider
              </Button>
            ) : undefined
          }
        >
          <QueryState query={providers} what="Providers" endpoint="GET /providers" compact>
            {() => (
              <ul className="m-0 flex list-none flex-col p-0">
                {(providers.data ?? []).map((p) => (
                  <li key={p.id} className="flex items-start justify-between gap-3 border-b border-line py-3">
                    <span className="flex min-w-0 flex-col">
                      <span className="font-medium">{p.name}</span>
                      <span className="text-sm text-text-3">
                        {p.kind === 'internal' ? 'Internal' : 'External'} · {p.aliases.join(', ')} ·{' '}
                        {p.data_classes.join('–')}
                      </span>
                    </span>
                    <span className="flex items-center gap-1.5 text-meta">
                      <Dot tone={p.enabled ? 'ok' : 'idle'} />
                      {p.enabled ? (p.kind === 'internal' ? 'Primary' : 'Enabled') : 'Disabled'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
          <div className="flex flex-col gap-2 rounded-panel bg-raised px-4 py-3.5">
            <span className="font-medium">Routing policy</span>
            {routing.data ? (
              <>
                <span className="text-meta text-text-2">
                  {routing.data.never_external.join(' and ')} never leave the internal cluster. Missions start
                  at {routing.data.default_mission_class}; the class only goes up and is enforced by the
                  mission's LiteLLM key.
                </span>
                <label className="flex items-center gap-2.5 text-meta">
                  <Toggle
                    checked={routing.data.external_fallback_when_degraded}
                    disabled={!canProviders}
                    onChange={(v) => void toggleFallback(v)}
                    label="Fail over to an external API when inferX is degraded"
                  />
                  Fail over to an external API when inferX is degraded (C0–C1 only)
                </label>
              </>
            ) : isNotServed(routing.error) ? (
              <NotServed what="The routing policy" endpoint="GET /routing-policy" compact />
            ) : (
              <span className="text-meta text-text-3">
                C2 and above, secrets and customer data never leave inferX.
              </span>
            )}
          </div>
          {canProviders && <InferenceKeyPanel />}
        </Panel>
        <BudgetsPanel />
      </div>
      {addProvider && <ProviderDialog open onClose={() => setAddProvider(false)} />}
    </main>
  )
}
