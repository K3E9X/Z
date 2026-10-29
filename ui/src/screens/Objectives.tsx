import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk, useObjectives, usePlaybooks } from '@/api/queries'
import type { Agent, Objective } from '@/api/types'
import { Sparkline } from '@/components/charts'
import { Dialog } from '@/components/Dialog'
import { EmptyState, QueryState } from '@/components/states'
import { StatusText } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Field, Input, Select, Toggle } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { compactNumber, relative } from '@/lib/format'
import { playbookTitle } from '@/lib/mission'

const STATUS = {
  on_track: { tone: 'ok' as const, label: 'On track' },
  at_risk: { tone: 'wait' as const, label: 'At risk' },
  off_track: { tone: 'err' as const, label: 'Off track' },
  paused: { tone: 'idle' as const, label: 'Paused' },
}

type ObjectiveWithId = Objective & { id?: string }

function History({ id, target }: { id: string; target: number }) {
  const h = useQuery({
    queryKey: qk.objectiveHistory(id),
    queryFn: () => unwrap(api.GET('/objectives/{id}/history', { params: { path: { id } } })),
  })
  const values = (h.data ?? []).map((c) => c.value).reverse()
  const last = h.data?.[0]
  return (
    <div className="flex items-center gap-3">
      <Sparkline
        values={values}
        label={`Trend: ${values.join(', ')} against target ${target}`}
        target={target}
      />
      <span className="text-meta text-text-3">
        {last ? `checked ${relative(last.ts)}` : 'not checked yet'}
      </span>
    </div>
  )
}

function ObjectiveRow({ o }: { o: ObjectiveWithId }) {
  const canWrite = useCan('agents:write')
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const st = STATUS[o.status ?? 'on_track']
  const id = o.id ?? ''
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true)
    try {
      await fn()
      await qc.invalidateQueries({ queryKey: ['agent'] })
      await qc.invalidateQueries({ queryKey: qk.objectiveHistory(id) })
      toast.ok(ok)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="flex flex-col gap-3 border-b border-line py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="font-medium">{o.title}</span>
          <span className="text-meta text-text-3">
            {o.metric.source === 'z' ? o.metric.query : o.metric.operation} · target{' '}
            {o.metric.direction === 'down' ? '≤' : '≥'} {o.metric.target} ·{' '}
            {typeof o.cadence === 'string' ? (
              <span className="font-mono">{o.cadence}</span>
            ) : (
              `on ${o.cadence.event}`
            )}{' '}
            · {o.mode === 'auto_start_read_only' ? 'starts read-only missions itself' : 'suggests missions'}
            {o.budget_per_period
              ? ` · ${compactNumber(o.budget_per_period.tokens)} tokens / ${o.budget_per_period.period}`
              : ''}
          </span>
          {(o.allowed_playbooks ?? []).length > 0 && (
            <span className="text-meta text-text-3">
              Playbooks: {(o.allowed_playbooks ?? []).map(playbookTitle).join(', ')}
            </span>
          )}
        </div>
        <StatusText tone={st.tone} label={st.label} />
      </div>
      <div className="flex flex-wrap items-center gap-4">
        {id && <History id={id} target={o.metric.target} />}
        <div className="flex-1" />
        {canWrite && id && (
          <>
            <label className="flex items-center gap-2 text-meta text-text-2">
              Active
              <Toggle
                checked={o.status !== 'paused'}
                disabled={busy}
                label={`${o.title} active`}
                onChange={(v) =>
                  void run(
                    () =>
                      unwrap(
                        api.PATCH('/objectives/{id}', { params: { path: { id } }, body: { paused: !v } }),
                      ),
                    v ? 'Resumed' : 'Paused',
                  )
                }
              />
            </label>
            <Button
              size="xs"
              busy={busy}
              onClick={() =>
                void run(
                  () => unwrap(api.POST('/objectives/{id}/evaluate', { params: { path: { id } }, body: {} })),
                  'Checked now',
                )
              }
            >
              Check now
            </Button>
          </>
        )}
      </div>
    </li>
  )
}

function CreateDialog({ agent, open, onClose }: { agent: Agent; open: boolean; onClose: () => void }) {
  const playbooks = usePlaybooks()
  const qc = useQueryClient()
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [query, setQuery] = useState('untriaged_findings')
  const [target, setTarget] = useState(0)
  const [direction, setDirection] = useState<'down' | 'up'>('down')
  const [cadence, setCadence] = useState('0 */4 * * *')
  const [mode, setMode] = useState<'suggest' | 'auto_start_read_only'>('suggest')
  const [tokens, setTokens] = useState(2_000_000)
  const [pb, setPb] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await unwrap(
        api.POST('/agents/{id}/objectives', {
          params: { path: { id: agent.id } },
          body: {
            agent: agent.name ?? agent.id,
            title,
            metric: { source: 'z', query, target, direction },
            cadence,
            mode,
            budget_per_period: { tokens, period: 'day' },
            ...(pb ? { allowed_playbooks: [pb] } : {}),
          } as never,
        }),
      )
      await qc.invalidateQueries({ queryKey: qk.objectives(agent.id) })
      toast.ok('Objective created')
      onClose()
    } catch (e) {
      toast.error(e, 'Objective not created')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`New objective for ${agent.display_name}`}
      eyebrow="The agent measures it on its cadence and proposes missions to close the gap"
      width={640}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!title.trim()} onClick={() => void save()}>
            Create objective
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Title" className="sm:col-span-2">
          {(id) => (
            <Input
              id={id}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Critical findings triaged within 48 hours"
            />
          )}
        </Field>
        <Field label="Metric (built-in)">
          {(id) => (
            <Select id={id} value={query} onChange={(e) => setQuery(e.target.value)}>
              {[
                'untriaged_findings',
                'open_proposals',
                'stale_tickets',
                'overdue_tickets',
                'mission_failures',
              ].map((q) => (
                <option key={q} value={q}>
                  {q.replace(/_/g, ' ')}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Target">
            {(id) => (
              <Input
                id={id}
                type="number"
                value={target}
                onChange={(e) => setTarget(Number(e.target.value))}
              />
            )}
          </Field>
          <Field label="Direction">
            {(id) => (
              <Select
                id={id}
                value={direction}
                onChange={(e) => setDirection(e.target.value as 'down' | 'up')}
              >
                <option value="down">down</option>
                <option value="up">up</option>
              </Select>
            )}
          </Field>
        </div>
        <Field label="Cadence (cron)">
          {(id) => (
            <Input
              id={id}
              value={cadence}
              onChange={(e) => setCadence(e.target.value)}
              className="font-mono"
            />
          )}
        </Field>
        <Field label="Mode">
          {(id) => (
            <Select
              id={id}
              value={mode}
              onChange={(e) => setMode(e.target.value as 'suggest' | 'auto_start_read_only')}
            >
              <option value="suggest">Suggest missions</option>
              <option value="auto_start_read_only">Start read-only missions</option>
            </Select>
          )}
        </Field>
        <Field label="Budget per day (tokens)">
          {(id) => (
            <Input id={id} type="number" value={tokens} onChange={(e) => setTokens(Number(e.target.value))} />
          )}
        </Field>
        <Field label="Allowed playbook">
          {(id) => (
            <Select id={id} value={pb} onChange={(e) => setPb(e.target.value)}>
              <option value="">Free-form plans only</option>
              {(playbooks.data?.items ?? []).map((p) => (
                <option key={p.id} value={p.name}>
                  {playbookTitle(p.name)}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  )
}

/** Objectives tab of an agent (docs/09 §5): metric, target, trend, status, last check, pause, create. */
export function ObjectivesPanel({ agent }: { agent: Agent }) {
  const q = useObjectives(agent.id)
  const canWrite = useCan('agents:write')
  const [creating, setCreating] = useState(false)
  return (
    <div className="flex flex-col gap-3 py-6">
      <div className="flex items-center justify-between gap-3">
        <p className="m-0 text-text-2">
          Standing goals. On their cadence the agent measures them, explains the gap and proposes missions;
          writes still need approval.
        </p>
        {canWrite && (
          <Button size="sm" icon="plus" onClick={() => setCreating(true)}>
            New objective
          </Button>
        )}
      </div>
      <QueryState query={q} what="Objectives" endpoint="GET /agents/{id}/objectives">
        {() =>
          (q.data ?? []).length === 0 ? (
            <EmptyState icon="target" title="No objective yet">
              For example: critical findings triaged within 48 hours, or no dependency older than 90 days.
            </EmptyState>
          ) : (
            <ul className="m-0 list-none p-0">
              {(q.data ?? []).map((o, i) => (
                <ObjectiveRow key={(o as ObjectiveWithId).id ?? i} o={o as ObjectiveWithId} />
              ))}
            </ul>
          )
        }
      </QueryState>
      {creating && <CreateDialog agent={agent} open onClose={() => setCreating(false)} />}
    </div>
  )
}
