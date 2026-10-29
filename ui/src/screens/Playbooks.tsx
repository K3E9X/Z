import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { useConnectors, usePlaybooks } from '@/api/queries'
import type { Playbook } from '@/api/types'
import { useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { Icon } from '@/components/Icon'
import { stepTitle } from '@/components/PlanChecklist'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Field, Input, PageTitle, Select, Tabs, Textarea } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { describeCron } from '@/lib/cron'
import { compactNumber, relative, titleCase } from '@/lib/format'
import { playbookTitle } from '@/lib/mission'

export function triggerText(p: Playbook): string {
  const t = p.trigger
  if (!t) return 'On demand'
  if (t.kind === 'schedule') return describeCron(t.cron)
  if (t.kind === 'gitlab') return `GitLab ${t.event ?? 'event'}${t.filter && typeof t.filter.label === 'string' ? ` labelled “${t.filter.label}”` : ''}`
  if (t.kind === 'jira') return `Jira ${t.event ?? 'issue'}${t.filter && typeof t.filter.label === 'string' ? ` labelled “${t.filter.label}”` : ''}`
  return `${titleCase(t.kind)}${t.event ? ` · ${t.event}` : ''}`
}

export function writesText(p: Playbook, connectorName: (s: string) => string): string {
  const writes = [...new Set((p.steps ?? []).flatMap((s) => s.writes ?? []))]
  if (writes.length === 0) return p.autonomy === 'read_only' ? 'Nothing, read only' : 'Nothing, delivers a document'
  const byConnector = new Map<string, string[]>()
  for (const w of writes) {
    const [c, ...rest] = w.split('.')
    byConnector.set(c ?? w, [...(byConnector.get(c ?? w) ?? []), rest.join('.')])
  }
  return `${[...byConnector.entries()].map(([c, ops]) => `${connectorName(c)} ${ops.map((o) => o.split('.')[0]?.replace(/_/g, ' ')).filter((x, i, a) => a.indexOf(x) === i).join(', ')}`).join('; ')}, after approval`
}

function Card({ p, agents, connectorName, onOpen }: { p: Playbook; agents: AgentIndex; connectorName: (s: string) => string; onOpen: () => void }) {
  const canStart = useCan('missions:start')
  return (
    <article className="flex flex-col gap-4 border-r border-b border-line bg-bg px-6 py-6">
      <div className="flex flex-col gap-1.5">
        <span className="text-meta text-text-3">{titleCase(p.domain ?? 'general')}</span>
        <h2 className="m-0 text-[16px] font-semibold">
          <button type="button" onClick={onOpen} className="text-left hover:underline hover:underline-offset-2">
            {playbookTitle(p.name)}
          </button>
        </h2>
        <p className="m-0 text-text-2">{p.description}</p>
      </div>
      <dl className="m-0 grid grid-cols-[96px_1fr] gap-y-1.5 text-ui">
        <dt className="text-text-3">Team</dt>
        <dd className="m-0">{(p.team ?? []).map((a) => agents.label(a)).join(', ') || '—'}</dd>
        <dt className="text-text-3">Tools</dt>
        <dd className="m-0">{(p.connectors ?? []).map(connectorName).join(', ') || 'Workspace only'}</dd>
        <dt className="text-text-3">Writes</dt>
        <dd className="m-0">{writesText(p, connectorName)}</dd>
        <dt className="text-text-3">Trigger</dt>
        <dd className="m-0">{triggerText(p)}</dd>
      </dl>
      <div className="mt-auto flex items-center justify-between pt-2">
        <span className="font-mono text-sm text-text-3">
          v{p.version} · {p.usage_count === 0 ? 'not used yet' : p.usage_count === 1 ? 'used once' : p.usage_count === 2 ? 'used twice' : `used ${p.usage_count} times`}
        </span>
        {canStart && (
          <Link to="/missions/new" search={{ playbook: p.name }} className="flex h-[30px] items-center rounded-ctl bg-inv-bg px-3.5 font-medium text-inv-text no-underline hover:opacity-90">
            Use
          </Link>
        )}
      </div>
    </article>
  )
}

function DetailDialog({ p, agents, onClose }: { p: Playbook; agents: AgentIndex; onClose: () => void }) {
  return (
    <Dialog open onClose={onClose} title={playbookTitle(p.name)} eyebrow={`${titleCase(p.domain ?? '')} · v${p.version}${p.pack ? ` · ${p.pack} pack` : ''}`} width={760}>
      <div className="flex flex-col gap-5">
        <p className="m-0 text-text-2">{p.description}</p>
        <section aria-label="Steps">
          <h3 className="m-0 mb-2 text-meta font-normal text-text-3">Steps</h3>
          <ol className="m-0 list-none p-0">
            {(p.steps ?? []).map((s, i) => (
              <li key={s.id ?? i} className="flex flex-col gap-1 border-b border-line py-3">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-sm text-text-3">{String(i + 1).padStart(2, '0')}</span>
                  <span className="font-medium">{stepTitle(s)}</span>
                </div>
                <div className="flex flex-wrap gap-x-4 pl-8 text-meta text-text-3">
                  <span>{agents.label(s.agent)}</span>
                  {s.fanout && <span>fan-out over {s.fanout.over}{s.fanout.concurrency ? `, ${s.fanout.concurrency} in parallel` : ''}</span>}
                  {s.depends_on?.length ? <span>after {s.depends_on.join(', ')}</span> : null}
                  {s.writes?.length ? <span className="text-wait">writes {s.writes.join(', ')}</span> : null}
                  {s.workspace && s.workspace !== 'none' && <span>workspace {s.workspace}</span>}
                </div>
              </li>
            ))}
          </ol>
        </section>
        <dl className="m-0 grid grid-cols-[140px_1fr] gap-y-1.5 text-ui">
          <dt className="text-text-3">Autonomy</dt>
          <dd className="m-0">{p.autonomy === 'read_only' ? 'Read only' : 'Writes need approval'}{p.four_eyes ? ' · four eyes' : ''}</dd>
          <dt className="text-text-3">Budget</dt>
          <dd className="m-0 font-mono">{p.budget?.max_tokens ? `${compactNumber(p.budget.max_tokens)} tokens` : '—'}{p.budget?.timeout_s ? ` · ${Math.round(p.budget.timeout_s / 60)} min` : ''}</dd>
          <dt className="text-text-3">Data class</dt>
          <dd className="m-0 font-mono">{p.data_class ?? 'C2'}</dd>
          <dt className="text-text-3">Saves per run</dt>
          <dd className="m-0">{p.baseline_minutes ? `${p.baseline_minutes} minutes of manual work` : '—'}</dd>
          <dt className="text-text-3">Last run</dt>
          <dd className="m-0">{p.last_run_at ? relative(p.last_run_at) : 'never'}</dd>
        </dl>
      </div>
    </Dialog>
  )
}

function CreateDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const agents = useAgentIndex()
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const [description, setDescription] = useState('')
  const [agent, setAgent] = useState('')
  const [prompt, setPrompt] = useState('')
  const [writes, setWrites] = useState('')
  const [busy, setBusy] = useState(false)
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  const save = async () => {
    setBusy(true)
    try {
      const w = writes.split(/[\s,]+/).filter(Boolean)
      await unwrap(
        api.POST('/playbooks', {
          body: {
            name: slug,
            version: '0.1.0',
            domain: domain || 'general',
            description,
            team: [agent],
            connectors: [...new Set(w.map((x) => x.split('.')[0] ?? x))],
            steps: [{ id: 'main', agent, prompt, ...(w.length ? { writes: w } : {}) }],
            autonomy: 'propose',
          } as never,
        }),
      )
      await qc.invalidateQueries({ queryKey: ['playbooks'] })
      toast.ok(`Playbook ${slug} created`)
      onClose()
    } catch (e) {
      toast.error(e, 'Playbook not created')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create a playbook"
      eyebrow="Tip: any successful mission can be saved as a playbook from its page"
      width={640}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!slug || !agent || !prompt.trim()} onClick={() => void save()}>
            Create
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint={slug ? `Saved as ${slug}` : undefined}>{(id, d) => <Input id={id} aria-describedby={d} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Domain">{(id) => <Input id={id} value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="vulnerabilities, code, identity…" />}</Field>
        <Field label="Description" className="sm:col-span-2">{(id) => <Textarea id={id} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        <Field label="Agent">
          {(id) => (
            <Select id={id} value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">Choose…</option>
              {[...agents.bySlug.values()]
                .filter((a, i, arr) => arr.findIndex((x) => x.id === a.id) === i)
                .map((a) => (
                  <option key={a.id} value={a.name}>
                    {a.display_name}
                  </option>
                ))}
            </Select>
          )}
        </Field>
        <Field label="Writes" hint="Full operation ids, comma-separated">{(id, d) => <Input id={id} aria-describedby={d} value={writes} onChange={(e) => setWrites(e.target.value)} className="font-mono text-sm" />}</Field>
        <Field label="Instructions" className="sm:col-span-2">{(id) => <Textarea id={id} rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} />}</Field>
      </div>
    </Dialog>
  )
}

export function Playbooks() {
  const search = useSearch({ from: '/playbooks' })
  const navigate = useNavigate()
  const q = usePlaybooks()
  const connectors = useConnectors()
  const agents = useAgentIndex()
  const canWrite = useCan('playbooks:write')
  const [text, setText] = useState(search.q ?? '')
  const [creating, setCreating] = useState(false)
  const all = useMemo(() => q.data?.items ?? [], [q.data])
  const domains = [...new Set(all.map((p) => p.domain ?? 'general'))].sort()
  const domain = search.domain ?? 'all'
  const needle = text.trim().toLowerCase()
  const list = all
    .filter((p) => domain === 'all' || (p.domain ?? 'general') === domain)
    .filter((p) => !needle || `${p.name ?? ''} ${p.description ?? ''} ${(p.connectors ?? []).join(' ')} ${(p.team ?? []).join(' ')}`.toLowerCase().includes(needle))
    .sort((a, b) => b.usage_count - a.usage_count || (a.name ?? '').localeCompare(b.name ?? ''))
  const connectorName = (s: string) => connectors.data?.find((c) => c.name === s)?.display_name ?? titleCase(s)
  const open = all.find((p) => p.id === search.playbook || p.name === search.playbook) ?? null

  return (
    <main className="flex w-full max-w-[1320px] flex-col gap-8 px-[72px] pt-11 pb-14 max-lg:px-8 max-md:px-5">
      <PageTitle
        title="Playbooks"
        lead="Ready-to-run missions, each with its team of agents, its skills and its guardrails. Any successful mission can become a playbook."
        right={canWrite ? <Button onClick={() => setCreating(true)}>Create a playbook</Button> : undefined}
      />
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex w-[386px] max-w-full items-center">
          <Icon name="search" size={14} className="pointer-events-none absolute left-3 text-text-3" />
          <Input data-search aria-label="Search playbooks" placeholder="Search" value={text} onChange={(e) => setText(e.target.value)} className="w-full pl-9" />
        </label>
        <Tabs<string>
          label="Domains"
          variant="pill"
          value={domain}
          onChange={(d) => void navigate({ to: '/playbooks', search: { domain: d === 'all' ? undefined : d }, replace: true })}
          items={[{ id: 'all', label: 'All' }, ...domains.map((d) => ({ id: d, label: titleCase(d) }))]}
        />
      </div>
      <QueryState query={q} what="Playbooks" endpoint="GET /playbooks">
        {() =>
          list.length === 0 ? (
            <EmptyState icon="playbooks" title={needle ? 'No playbook matches' : 'No playbook yet'}>
              Playbooks come with packs; save a mission as a playbook to add yours.
            </EmptyState>
          ) : (
            <div className="-mr-px -mb-px grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] overflow-hidden rounded-card border-t border-l border-line">
              {list.map((p) => (
                <Card key={p.id} p={p} agents={agents} connectorName={connectorName} onOpen={() => void navigate({ to: '/playbooks', search: { ...search, playbook: p.name } })} />
              ))}
            </div>
          )
        }
      </QueryState>
      {open && <DetailDialog p={open} agents={agents} onClose={() => void navigate({ to: '/playbooks', search: { ...search, playbook: undefined } })} />}
      {creating && <CreateDialog open onClose={() => setCreating(false)} />}
    </main>
  )
}
