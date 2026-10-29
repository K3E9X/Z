import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import {
  qk,
  useAgent,
  useAgents,
  useAgentVersions,
  useAssignments,
  useEvalRuns,
  useObjectives,
  useSkill,
  useSkills,
  useSkillVersions,
} from '@/api/queries'
import type { Agent, AgentProfile, Skill } from '@/api/types'
import { Avatar } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { diffTexts, DiffViewer } from '@/components/DiffViewer'
import { Icon } from '@/components/Icon'
import { Markdown } from '@/components/Markdown'
import { EmptyState, NotServed, QueryState } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, DefList, Field, Input, Select, Skeleton, Tabs, Textarea } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { compactNumber, humanDuration, initials, relative, titleCase } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { ObjectivesPanel } from './Objectives'

type Tab = 'profile' | 'objectives' | 'skills' | 'tools' | 'memory' | 'evals' | 'versions'

function templateName(a: Agent): string {
  return titleCase(a.template ?? a.name ?? '')
}

function statusTone(a: Agent): 'run' | 'wait' | 'idle' | 'ok' {
  if (a.status === 'disabled') return 'idle'
  if (a.status === 'working') return 'run'
  return 'idle'
}

const AUTONOMY: Record<string, string> = { propose: 'writes need approval', read_only: 'read only' }

function Roster({ agents, selected, onSelect }: { agents: Agent[]; selected: string | null; onSelect: (id: string) => void }) {
  const canWrite = useCan('agents:write')
  const working = agents.filter((a) => a.status === 'working')
  const available = agents.filter((a) => a.status !== 'working')
  const row = (a: Agent) => {
    const tone = statusTone(a)
    const template = templateName(a)
    const sub = a.status === 'working' ? `${a.current_missions?.length ?? 0} ${a.current_missions?.length === 1 ? 'mission' : 'missions'} running` : a.status === 'disabled' ? 'disabled' : 'idle'
    return (
      <li key={a.id}>
        <button
          type="button"
          onClick={() => onSelect(a.id)}
          aria-current={selected === a.id ? 'true' : undefined}
          className={`flex w-full items-center gap-3 rounded-panel px-2.5 py-2.5 text-left ${selected === a.id ? 'bg-raised' : 'hover:bg-panel'}`}
        >
          <Avatar avatar={a.avatar ?? null} name={a.display_name ?? a.name ?? '?'} size={30} />
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate">
              <span className="font-medium">{a.display_name}</span>
              {template.toLowerCase() !== (a.display_name ?? '').toLowerCase() && <span className="text-text-2"> · {template}</span>}
            </span>
            <span className="truncate text-meta text-text-3">{sub}</span>
          </span>
          <Dot tone={tone} className={tone === 'run' ? 'bg-run' : ''} />
        </button>
      </li>
    )
  }
  return (
    <aside aria-label="Agents" className="flex w-full flex-col gap-4 border-line px-3.5 pt-6 pb-6 md:w-[300px] md:flex-none md:border-r">
      <div className="flex items-center justify-between px-2">
        <h1 className="m-0 text-xl font-semibold">Agents</h1>
        {canWrite && (
          <Link to="/agents/new" className="flex h-8 items-center rounded-btn border border-line-2 px-3 no-underline hover:bg-raised">
            New agent
          </Link>
        )}
      </div>
      {working.length > 0 && (
        <section aria-label="Working">
          <h2 className="m-0 mb-1 px-2.5 text-meta font-normal text-text-3">Working</h2>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0">{working.map(row)}</ul>
        </section>
      )}
      <section aria-label="Available">
        <h2 className="m-0 mb-1 px-2.5 text-meta font-normal text-text-3">Available</h2>
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">{available.map(row)}</ul>
      </section>
    </aside>
  )
}

function NameDialog({ agent, mode, open, onClose }: { agent: Agent; mode: 'rename' | 'duplicate'; open: boolean; onClose: (newId?: string) => void }) {
  const [name, setName] = useState(mode === 'rename' ? (agent.display_name ?? '') : '')
  const [handle, setHandle] = useState(mode === 'rename' ? (agent.handle ?? '') : '')
  const [color, setColor] = useState(agent.avatar?.color ?? '#5DBE8A')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const qc = useQueryClient()
  const toast = useToast()
  const validHandle = /^[a-z0-9][a-z0-9-]{1,38}$/.test(handle)
  const save = async () => {
    setBusy(true)
    setError(null)
    const avatar = { initials: initials(name), color }
    try {
      if (mode === 'rename') {
        await unwrap(api.PATCH('/agents/{id}', { params: { path: { id: agent.id } }, body: { display_name: name, handle, avatar } }))
        toast.ok(`Renamed to ${name} (@${handle}). History is kept.`)
        await qc.invalidateQueries({ queryKey: qk.agents })
        await qc.invalidateQueries({ queryKey: qk.agent(agent.id) })
        onClose()
      } else {
        const created = await unwrap(api.POST('/agents/{id}/duplicate', { params: { path: { id: agent.id } }, body: { display_name: name, handle, avatar } }))
        toast.ok(`${name} created from ${agent.display_name}`)
        await qc.invalidateQueries({ queryKey: qk.agents })
        onClose(created.id)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Not saved')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={() => onClose()}
      title={mode === 'rename' ? `Rename ${agent.display_name}` : `Duplicate ${agent.display_name}`}
      eyebrow={mode === 'rename' ? 'The stable name, skills, tools and history stay the same' : 'Same profile, skills and tools; a new name, handle and avatar'}
      footer={
        <>
          <Button onClick={() => onClose()}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim() || !validHandle} onClick={() => void save()}>
            {mode === 'rename' ? 'Rename' : 'Duplicate'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-4">
          <Avatar avatar={{ initials: initials(name || '?'), color }} name={name || '?'} size={44} />
          <label className="flex items-center gap-2 text-meta text-text-2">
            Color
            <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-8 w-10 rounded-ctl border border-line-2 bg-transparent" />
          </label>
        </div>
        <Field label="Name your team uses" hint="Free text, e.g. Atlas, Vega, Nova">
          {(id, d) => (
            <Input
              id={id}
              aria-describedby={d}
              value={name}
              autoFocus
              onChange={(e) => {
                setName(e.target.value)
                if (mode === 'duplicate' || !handle) setHandle(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''))
              }}
            />
          )}
        </Field>
        <Field label="Handle" hint="For @mentions in the web app, the CLI and chat. Unique in the organisation." error={handle && !validHandle ? 'Lowercase letters, digits and dashes, 2 to 39 characters' : null}>
          {(id, d) => (
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-text-3">@</span>
              <Input id={id} aria-describedby={d} value={handle} onChange={(e) => setHandle(e.target.value.toLowerCase())} className="font-mono" />
            </div>
          )}
        </Field>
        {error && (
          <div role="alert" className="text-meta text-err">
            {error}
          </div>
        )}
      </div>
    </Dialog>
  )
}

function ProfileTab({ agent }: { agent: Agent }) {
  const canWrite = useCan('agents:write')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<AgentProfile>({})
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const start = () => {
    setDraft({
      role: agent.role,
      instructions: agent.instructions,
      persona: agent.persona,
      model: agent.model,
      escalate_model: agent.escalate_model,
      autonomy: agent.autonomy,
      limits: agent.limits,
    })
    setEditing(true)
  }
  const save = async () => {
    setBusy(true)
    try {
      await unwrap(api.PATCH('/agents/{id}', { params: { path: { id: agent.id } }, body: draft }))
      await qc.invalidateQueries({ queryKey: qk.agent(agent.id) })
      await qc.invalidateQueries({ queryKey: qk.agents })
      await qc.invalidateQueries({ queryKey: qk.agentVersions(agent.id) })
      toast.ok('Saved as a new version')
      setEditing(false)
    } catch (e) {
      toast.error(e, 'Not saved')
    } finally {
      setBusy(false)
    }
  }
  if (editing) {
    const limits = draft.limits ?? { max_steps: 60, max_tokens: 600000, timeout_s: 1800 }
    return (
      <div className="flex max-w-[760px] flex-col gap-4 py-6">
        <Field label="Role">{(id) => <Textarea id={id} rows={2} value={draft.role ?? ''} onChange={(e) => setDraft({ ...draft, role: e.target.value })} />}</Field>
        <Field label="Instructions" hint="Markdown. Short, numbered steps work best with open-weight models.">
          {(id, d) => <Textarea id={id} aria-describedby={d} rows={8} value={draft.instructions ?? ''} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} className="font-mono text-[12.5px]" />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-4">
          <Field label="Model">
            {(id) => (
              <Select id={id} value={draft.model ?? ''} onChange={(e) => setDraft({ ...draft, model: e.target.value })}>
                <option value="z-fast">z-fast</option>
                <option value="z-heavy">z-heavy</option>
              </Select>
            )}
          </Field>
          <Field label="Escalate to">
            {(id) => (
              <Select id={id} value={draft.escalate_model ?? ''} onChange={(e) => setDraft({ ...draft, escalate_model: e.target.value })}>
                <option value="z-heavy">z-heavy</option>
                <option value="z-fast">z-fast</option>
              </Select>
            )}
          </Field>
          <Field label="Autonomy">
            {(id) => (
              <Select id={id} value={draft.autonomy ?? 'propose'} onChange={(e) => setDraft({ ...draft, autonomy: e.target.value as 'propose' | 'read_only' })}>
                <option value="propose">Propose writes</option>
                <option value="read_only">Read only</option>
              </Select>
            )}
          </Field>
          <Field label="Tone">
            {(id) => (
              <Select id={id} value={draft.persona?.tone ?? 'concise'} onChange={(e) => setDraft({ ...draft, persona: { ...draft.persona, tone: e.target.value as 'concise' } })}>
                <option value="concise">Concise</option>
                <option value="detailed">Detailed</option>
                <option value="friendly">Friendly</option>
                <option value="formal">Formal</option>
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Max steps">{(id) => <Input id={id} type="number" value={limits.max_steps} onChange={(e) => setDraft({ ...draft, limits: { ...limits, max_steps: Number(e.target.value) } })} />}</Field>
          <Field label="Max tokens">{(id) => <Input id={id} type="number" value={limits.max_tokens} onChange={(e) => setDraft({ ...draft, limits: { ...limits, max_tokens: Number(e.target.value) } })} />}</Field>
          <Field label="Timeout (s)">{(id) => <Input id={id} type="number" value={limits.timeout_s} onChange={(e) => setDraft({ ...draft, limits: { ...limits, timeout_s: Number(e.target.value) } })} />}</Field>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setEditing(false)}>Cancel</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Save a new version
          </Button>
        </div>
      </div>
    )
  }
  return (
    <div className="flex max-w-[820px] flex-col gap-6 py-6">
      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-meta font-normal text-text-3">Instructions</h3>
        {agent.instructions ? <Markdown>{agent.instructions}</Markdown> : <span className="text-text-3">None</span>}
      </section>
      <DefList
        className="text-ui"
        rows={[
          ['Persona', `${agent.persona?.tone ?? 'concise'} · ${agent.persona?.language ?? 'en'}`],
          ['Escalates to', <span key="e" className="font-mono">{agent.escalate_model ?? '—'}</span>],
          ['Limits', <span key="l" className="font-mono">{agent.limits ? `${agent.limits.max_steps} steps · ${compactNumber(agent.limits.max_tokens)} tokens · ${humanDuration(agent.limits.timeout_s)}` : '—'}</span>],
          ['Visibility', agent.visibility ?? 'team'],
          ['Owner team', agent.owner_team ?? '—'],
          ['Pack', agent.pack ?? '—'],
          ['Stable name', <span key="n" className="font-mono">{agent.name}</span>],
          ['Updated', relative(agent.updated_at)],
        ]}
      />
      {canWrite && (
        <Button className="self-start" icon="edit" onClick={start}>
          Edit profile
        </Button>
      )}
    </div>
  )
}

function SkillDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const skill = useSkill(id)
  const versions = useSkillVersions(id)
  const canWrite = useCan('skills:write')
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const rollback = async (v: string) => {
    setBusy(v)
    try {
      await unwrap(api.POST('/skills/{id}/rollback', { params: { path: { id } }, body: { to_version: v, reason: 'Rolled back from the web app' } }))
      await qc.invalidateQueries({ queryKey: qk.skill(id) })
      await qc.invalidateQueries({ queryKey: qk.skillVersions(id) })
      await qc.invalidateQueries({ queryKey: ['skills'] })
      toast.ok(`Rolled back to ${v}`)
    } catch (e) {
      toast.error(e, 'Rollback failed')
    } finally {
      setBusy(null)
    }
  }
  return (
    <Dialog open onClose={onClose} title={skill.data?.name ?? 'Skill'} eyebrow={skill.data ? `${skill.data.origin} · v${skill.data.version}` : undefined} width={860}>
      {!skill.data ? (
        <Skeleton className="h-60" />
      ) : (
        <div className="flex flex-col gap-5">
          <p className="m-0 text-text-2">{skill.data.description}</p>
          {skill.data.content ? (
            <div className="rounded-panel border border-line bg-code p-4">
              <Markdown>{skill.data.content}</Markdown>
            </div>
          ) : (
            <div className="text-meta text-text-3">The SKILL.md content is not included in this response.</div>
          )}
          <section aria-label="Versions">
            <h3 className="m-0 mb-2 text-meta font-normal text-text-3">Versions</h3>
            <QueryState query={versions} what="Skill versions" endpoint="GET /skills/{id}/versions">
              {() => (
                <ul className="m-0 list-none p-0">
                  {(versions.data ?? []).map((v) => (
                    <li key={v.version} className="flex items-center gap-3 border-b border-line py-2">
                      <span className="w-14 font-mono">{v.version}</span>
                      <span className="text-meta text-text-2">
                        {v.origin} · {relative(v.created_at)}
                        {v.created_by ? ` · ${v.created_by}` : ''}
                      </span>
                      <span className="flex-1" />
                      {v.version === skill.data?.version ? (
                        <span className="text-meta text-ok">current</span>
                      ) : (
                        canWrite && (
                          <Button size="xs" busy={busy === v.version} onClick={() => void rollback(v.version)}>
                            Roll back to {v.version}
                          </Button>
                        )
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </QueryState>
          </section>
        </div>
      )}
    </Dialog>
  )
}

function lastChange(s: Skill): { text: string; sub?: React.ReactNode; tone?: 'wait' } {
  if (s.pending_proposal_id) return { text: 'Improvement proposed', sub: <Link to="/learning" search={{ proposal: s.pending_proposal_id }}>see the proposal</Link>, tone: 'wait' }
  const c = s.last_change
  if (!c) return { text: '—' }
  const when = relative(c.at)
  if (c.kind === 'learned') return { text: `Improved ${when}`, sub: c.proposal_id ? <Link to="/learning" search={{ proposal: c.proposal_id }}>by learning{c.by ? `, approved by ${c.by}` : ''}</Link> : 'by learning' }
  if (c.kind === 'rolled_back') return { text: `Rolled back ${when}`, sub: c.by ?? undefined }
  if (c.kind === 'edited') return { text: `Edited ${when}`, sub: c.by ?? undefined }
  return { text: `Created ${when}`, sub: c.by?.startsWith('pack:') ? `from the ${c.by.slice(5)} pack` : (c.by ?? undefined) }
}

function SkillsTab({ agent }: { agent: Agent }) {
  const all = useSkills()
  const canWrite = useCan('agents:write')
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const active = new Set(agent.skills ?? [])
  const list = (all.data?.items ?? []).filter((s) => active.has(s.name) || s.agents.includes(agent.name ?? ''))
  const others = (all.data?.items ?? []).filter((s) => !active.has(s.name))
  const setSkills = async (skills: string[]) => {
    try {
      await unwrap(api.PATCH('/agents/{id}', { params: { path: { id: agent.id } }, body: { skills } }))
      await qc.invalidateQueries({ queryKey: qk.agent(agent.id) })
      await qc.invalidateQueries({ queryKey: qk.agents })
      toast.ok('Skills updated (new agent version)')
    } catch (e) {
      toast.error(e)
    }
  }
  return (
    <div className="flex flex-col gap-4 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-text-2">Skills are versioned SKILL.md files. The agent loads the ones that fit the mission.</p>
        {canWrite && (
          <div className="flex gap-2">
            <Button size="sm" disabled title="POST /skills is not in the contract yet">
              Import SKILL.md
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              Add from a pack
            </Button>
          </div>
        )}
      </div>
      <QueryState query={all} what="Skills" endpoint="GET /skills">
        {() =>
          list.length === 0 ? (
            <EmptyState title="No skill yet">Add skills from a pack, or let the learning loop propose them.</EmptyState>
          ) : (
            <div className="overflow-x-auto rounded-card border border-line bg-panel">
              <table className="w-full min-w-[680px] border-collapse">
                <thead>
                  <tr className="border-b border-line text-sm text-text-3">
                    <th className="px-5 py-3 font-normal">Skill</th>
                    <th className="py-3 pr-4 font-normal">Source</th>
                    <th className="py-3 pr-4 font-normal">Version</th>
                    <th className="py-3 pr-4 font-normal">Last change</th>
                    <th className="py-3 pr-5 text-right font-normal">Active</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((s) => {
                    const lc = lastChange(s)
                    return (
                      <tr key={s.id} className="border-b border-line last:border-b-0">
                        <td className="px-5 py-3.5">
                          <button type="button" onClick={() => setOpen(s.id)} className="flex flex-col text-left">
                            <span className="font-mono text-[12.5px]">{s.name}</span>
                            <span className="text-meta text-text-2">{s.description}</span>
                          </button>
                        </td>
                        <td className="pr-4">{s.origin === 'pack' ? `${titleCase(s.pack ?? 'pack')} pack` : s.origin === 'learned' ? 'Learned' : 'Team'}</td>
                        <td className="pr-4 font-mono">{s.version}</td>
                        <td className="pr-4">
                          <div className="flex flex-col">
                            <span className={lc.tone === 'wait' ? 'text-wait' : ''}>{lc.text}</span>
                            {lc.sub && <span className="text-meta text-text-3 [&_a]:text-text-3 [&_a]:underline [&_a]:underline-offset-2">{lc.sub}</span>}
                          </div>
                        </td>
                        <td className="pr-5 text-right">
                          <input
                            type="checkbox"
                            className="h-[14px] w-[14px]"
                            checked={active.has(s.name)}
                            disabled={!canWrite}
                            aria-label={`${s.name} active for ${agent.display_name}`}
                            onChange={(e) => void setSkills(e.target.checked ? [...active, s.name] : [...active].filter((x) => x !== s.name))}
                          />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
        }
      </QueryState>
      {open && <SkillDrawer id={open} onClose={() => setOpen(null)} />}
      <Dialog open={adding} onClose={() => setAdding(false)} title="Add a skill" width={620}>
        <ul className="m-0 flex list-none flex-col p-0">
          {others.map((s) => (
            <li key={s.id} className="flex items-center gap-3 border-b border-line py-2.5">
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="font-mono text-[12.5px]">{s.name}</span>
                <span className="text-meta text-text-3">{s.description}</span>
              </span>
              <Button
                size="xs"
                onClick={() => {
                  void setSkills([...active, s.name])
                  setAdding(false)
                }}
              >
                Add
              </Button>
            </li>
          ))}
          {others.length === 0 && <li className="text-meta text-text-3">Every known skill is already active.</li>}
        </ul>
      </Dialog>
    </div>
  )
}

function ToolsTab({ agent }: { agent: Agent }) {
  const describe = (t: string): string => {
    if (t === '@read' || t === 'gateway:@read') return 'Every exposed read operation of the connectors attached to the mission'
    if (t.startsWith('gateway:')) return t.endsWith('.*') ? 'Read operations matching this glob, through the tool gateway' : 'One operation through the tool gateway'
    const local: Record<string, string> = {
      read: 'Read files in /workspace',
      edit: 'Edit files in /workspace',
      terminal: 'Sandboxed shell (git_clone or files workspaces only)',
      browser: 'Recorded browser flows, run gateway-side',
      code: 'Sandboxed Python for analysis and charts, no network',
      documents: 'Produce md, docx, pptx, xlsx, pdf and csv artifacts',
      spawn_subagent: 'Start sub-agents within the mission budget',
    }
    return local[t] ?? t
  }
  return (
    <div className="flex max-w-[860px] flex-col gap-5 py-6">
      <ul className="m-0 list-none rounded-card border border-line bg-panel p-0">
        {(agent.tools ?? []).map((t) => (
          <li key={t} className="flex items-baseline gap-4 border-b border-line px-5 py-3 last:border-b-0">
            <span className="w-[260px] flex-none font-mono text-[12.5px] break-all">{t}</span>
            <span className="text-text-2">{describe(t)}</span>
          </li>
        ))}
      </ul>
      <p className="m-0 text-meta text-text-3">
        Built-ins are implicit: ask_human, search_memory, report_objective, and propose_action for the writes of the current step. An agent never lists write
        operations; writes are proposals you approve.
      </p>
    </div>
  )
}

function MemoryTab({ agent }: { agent: Agent }) {
  return (
    <div className="flex max-w-[760px] flex-col gap-4 py-6">
      <DefList
        className="text-ui"
        rows={[
          ['Scopes', (agent.memory?.scopes ?? []).join(', ') || '—'],
          ['Facts per step', String(agent.memory?.top_k ?? 8)],
        ]}
      />
      <NotServed what="Browsing memory facts" endpoint="a memory facts endpoint (none in contracts/openapi.yaml yet)" />
      <p className="m-0 text-meta text-text-3">Facts are approved through Learning; C3 content never enters memory. Each fact shows its source and approver.</p>
    </div>
  )
}

function EvalsTab({ agent }: { agent: Agent }) {
  const runs = useEvalRuns(agent.name)
  return (
    <div className="flex flex-col gap-4 py-6">
      {agent.evals_summary && (
        <div className="text-ui">
          Last run: {agent.evals_summary.passed} of {agent.evals_summary.total} cases pass{agent.evals_summary.model ? ` on ${agent.evals_summary.model}` : ''}.
        </div>
      )}
      <QueryState query={runs} what="Eval runs" endpoint="GET /evals">
        {() =>
          (runs.data?.items ?? []).length === 0 ? (
            <EmptyState title="No eval run yet">Run the agent's suite from Evals to compare models.</EmptyState>
          ) : (
            <ul className="m-0 list-none p-0">
              {(runs.data?.items ?? []).map((r) => (
                <li key={r.id} className="flex items-center gap-4 border-b border-line py-2.5">
                  <span className="font-mono">{r.model}</span>
                  <span>{r.scores ? `${r.scores.cases_passed}/${r.scores.cases_total}` : r.status}</span>
                  <span className="text-meta text-text-3">{relative(r.created_at)}</span>
                  <Link to="/evals" search={{ run: r.id }} className="ml-auto text-meta">
                    Open
                  </Link>
                </li>
              ))}
            </ul>
          )
        }
      </QueryState>
      <Link to="/evals" search={{ target: agent.name }} className="text-meta text-text-2">
        Open Evals for {agent.display_name} →
      </Link>
    </div>
  )
}

function VersionsTab({ agent }: { agent: Agent }) {
  const v = useAgentVersions(agent.id)
  const [open, setOpen] = useState<string | null>(null)
  const list = v.data ?? []
  return (
    <div className="flex flex-col gap-3 py-6">
      <QueryState query={v} what="Versions" endpoint="GET /agents/{id}/versions">
        {() => (
          <ul className="m-0 list-none p-0">
            {list.map((ver, i) => {
              const prev = list[i + 1]
              return (
                <li key={ver.version} className="border-b border-line py-3">
                  <button type="button" className="flex w-full items-center gap-4 text-left" onClick={() => setOpen(open === ver.version ? null : ver.version)} aria-expanded={open === ver.version}>
                    <span className="w-16 font-mono">{ver.version}</span>
                    <span className="flex-1">{ver.change_note ?? (i === list.length - 1 ? 'First version' : 'Profile changed')}</span>
                    <span className="text-meta text-text-3">
                      {ver.created_by ?? 'system'} · {relative(ver.created_at)}
                    </span>
                    <Icon name={open === ver.version ? 'chevronDown' : 'chevronRight'} size={12} className="text-text-3" />
                  </button>
                  {open === ver.version && (
                    <div className="pt-3">
                      <DiffViewer files={diffTexts('agent.yaml', prev ? JSON.stringify(prev.profile, null, 2) : '', JSON.stringify(ver.profile, null, 2))} maxHeight={420} />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </QueryState>
    </div>
  )
}

function AgentDetail({ id }: { id: string }) {
  const q = useAgent(id)
  const search = useSearch({ from: '/agents' })
  const navigate = useNavigate()
  const canWrite = useCan('agents:write')
  const canStart = useCan('missions:start')
  const objectives = useObjectives(id)
  const assignments = useAssignments(id)
  const [dialog, setDialog] = useState<'rename' | 'duplicate' | null>(null)
  const tab = (['profile', 'objectives', 'skills', 'tools', 'memory', 'evals', 'versions'].includes(search.tab ?? '') ? search.tab : 'skills') as Tab
  if (!q.data) return <div className="flex-1 p-10">{q.error ? <QueryState query={q} what="This agent" endpoint="GET /agents/{id}">{() => null}</QueryState> : <Skeleton className="h-60" />}</div>
  const a = q.data
  const atRisk = (objectives.data ?? []).filter((o) => o.status === 'at_risk' || o.status === 'off_track').length
  return (
    <section aria-label={a.display_name ?? a.name ?? 'Agent'} className="flex min-w-0 flex-1 flex-col px-9 pt-6 pb-10 max-md:px-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <h2 className="m-0 flex flex-wrap items-baseline gap-x-3">
            <span className="font-display-face text-[38px] leading-none">{a.display_name}</span>
            <span className="font-mono text-[15px] text-text-2">
              @{a.handle}
              {templateName(a).toLowerCase() !== (a.display_name ?? '').toLowerCase() && ` · ${templateName(a)}`}
            </span>
          </h2>
          <p className="m-0 max-w-[560px] text-text-2">{a.role}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {canWrite && (
            <>
              <Button onClick={() => setDialog('rename')}>Rename</Button>
              <Button onClick={() => setDialog('duplicate')}>Duplicate</Button>
            </>
          )}
          {canStart && (
            <Button variant="primary" onClick={() => void navigate({ to: '/missions/new', search: { agent: a.handle } })}>
              Give it a mission
            </Button>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-1 text-ui">
        <span>
          <span className="text-text-3">Model</span> <span className="font-mono">{a.model}</span>
        </span>
        <span>
          <span className="text-text-3">Autonomy</span> {AUTONOMY[a.autonomy ?? 'propose']}
        </span>
        <span>
          <span className="text-text-3">Version</span> <span className="font-mono">v{a.version}</span>
        </span>
        <span>
          <span className="text-text-3">Evals</span> {a.evals_summary ? `${a.evals_summary.passed} of ${a.evals_summary.total} cases pass` : 'not run yet'}
        </span>
        {assignments.data && assignments.data.length > 0 && (
          <span>
            <span className="text-text-3">Looks after</span> {assignments.data.map((x) => x.object_ref).join(', ')}
          </span>
        )}
      </div>
      <Tabs<Tab>
        label="Agent sections"
        className="mt-6"
        value={tab}
        onChange={(t) => void navigate({ to: '/agents', search: { agent: id, tab: t }, replace: true })}
        items={[
          { id: 'profile', label: 'Profile' },
          { id: 'objectives', label: 'Objectives', badge: atRisk ? <span className="font-mono text-sm text-wait">{atRisk} at risk</span> : undefined },
          { id: 'skills', label: 'Skills' },
          { id: 'tools', label: 'Tools' },
          { id: 'memory', label: 'Memory' },
          { id: 'evals', label: 'Evals' },
          { id: 'versions', label: 'Versions' },
        ]}
      />
      {tab === 'profile' && <ProfileTab agent={a} />}
      {tab === 'objectives' && <ObjectivesPanel agent={a} />}
      {tab === 'skills' && <SkillsTab agent={a} />}
      {tab === 'tools' && <ToolsTab agent={a} />}
      {tab === 'memory' && <MemoryTab agent={a} />}
      {tab === 'evals' && <EvalsTab agent={a} />}
      {tab === 'versions' && <VersionsTab agent={a} />}
      {dialog && (
        <NameDialog
          agent={a}
          mode={dialog}
          open
          onClose={(newId) => {
            setDialog(null)
            if (newId) void navigate({ to: '/agents', search: { agent: newId } })
          }}
        />
      )}
    </section>
  )
}

export function Agents() {
  const q = useAgents()
  const search = useSearch({ from: '/agents' })
  const navigate = useNavigate()
  const list = useMemo(() => {
    const items = [...(q.data?.items ?? [])]
    return items.sort((x, y) => Number(y.status === 'working') - Number(x.status === 'working') || (x.display_name ?? '').localeCompare(y.display_name ?? ''))
  }, [q.data])
  const selected = search.agent && list.some((a) => a.id === search.agent || a.name === search.agent) ? (list.find((a) => a.id === search.agent || a.name === search.agent)?.id ?? null) : (list[0]?.id ?? null)
  const select = (id: string) => void navigate({ to: '/agents', search: { agent: id, tab: search.tab }, replace: true })
  useHotkeys({
    j: () => {
      const i = list.findIndex((a) => a.id === selected)
      const n = list[Math.min(list.length - 1, i + 1)]
      if (n) select(n.id)
    },
    k: () => {
      const i = list.findIndex((a) => a.id === selected)
      const n = list[Math.max(0, i - 1)]
      if (n) select(n.id)
    },
  })
  return (
    <main className="flex min-h-screen flex-wrap md:flex-nowrap">
      {q.isPending ? (
        <div className="w-[300px] p-6">
          <Skeleton className="h-60" />
        </div>
      ) : q.error ? (
        <div className="p-10">
          <QueryState query={q} what="Agents" endpoint="GET /agents">
            {() => null}
          </QueryState>
        </div>
      ) : (
        <Roster agents={list} selected={selected} onSelect={select} />
      )}
      {selected && <AgentDetail key={selected} id={selected} />}
    </main>
  )
}
