import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useAgents, useSkills, useTeams } from '@/api/queries'
import type { AgentProfile } from '@/api/types'
import { Avatar } from '@/components/Avatar'
import { NotServed } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Checkbox, Field, Input, PageTitle, Select, Tabs, Textarea } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { initials, titleCase } from '@/lib/format'

type Way = 'describe' | 'template' | 'duplicate'

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 39)
}

/** Agent builder (docs/09 §5): describe → drafted profile → edit → create; or from a template, or by duplicating. */
export function AgentBuilder() {
  const search = useSearch({ from: '/agents/new' })
  const navigate = useNavigate()
  const agents = useAgents()
  const skills = useSkills()
  const teams = useTeams()
  const canWrite = useCan('agents:write')
  const qc = useQueryClient()
  const toast = useToast()
  const [way, setWay] = useState<Way>(search.from ? 'duplicate' : 'template')
  const [description, setDescription] = useState('')
  const [drafting, setDrafting] = useState<'idle' | 'busy' | 'unavailable'>('idle')
  const [questions, setQuestions] = useState<string[]>([])
  const [source, setSource] = useState(search.from ?? '')
  const [profile, setProfile] = useState<AgentProfile | null>(null)
  const [name, setName] = useState('')
  const [handle, setHandle] = useState('')
  const [color, setColor] = useState('#74AEFF')
  const [team, setTeam] = useState('')
  const [busy, setBusy] = useState(false)
  const list = agents.data?.items ?? []
  const validHandle = /^[a-z0-9][a-z0-9-]{1,38}$/.test(handle)
  const taken = list.some((a) => a.handle === handle)

  const draft = async () => {
    setDrafting('busy')
    try {
      const d = await unwrap(api.POST('/agents/draft', { body: { description, ...(team ? { team } : {}) } }))
      setProfile(d.profile)
      setQuestions(d.questions ?? [])
      setName(d.profile.display_name ?? '')
      setHandle(d.profile.handle ?? slug(d.profile.display_name ?? ''))
      setDrafting('idle')
    } catch (e) {
      if (isNotServed(e)) setDrafting('unavailable')
      else {
        setDrafting('idle')
        toast.error(e, 'Could not draft the agent')
      }
    }
  }

  const create = async () => {
    setBusy(true)
    const avatar = { initials: initials(name), color }
    try {
      let id: string
      if (way === 'duplicate') {
        const src = list.find((a) => a.id === source || a.name === source)
        if (!src) throw new Error('Choose the agent to duplicate')
        id = (
          await unwrap(
            api.POST('/agents/{id}/duplicate', {
              params: { path: { id: src.id } },
              body: { display_name: name, handle, avatar },
            }),
          )
        ).id
      } else if (way === 'template' || !profile) {
        const tpl = list.find((a) => a.id === source || a.name === source)
        if (!tpl?.name) throw new Error('Choose a template')
        id = (
          await unwrap(
            api.POST('/agents', {
              body: {
                template: tpl.template ?? tpl.name,
                display_name: name,
                handle,
                avatar,
                ...(team ? { owner_team: team } : {}),
              },
            }),
          )
        ).id
      } else {
        id = (
          await unwrap(
            api.POST('/agents', {
              body: {
                ...profile,
                name: profile.name ?? slug(name),
                display_name: name,
                handle,
                avatar,
              } as never,
            }),
          )
        ).id
      }
      await qc.invalidateQueries({ queryKey: qk.agents })
      toast.ok(`${name} (@${handle}) is ready`, {
        label: 'Give it a mission',
        run: () => void navigate({ to: '/missions/new', search: { agent: handle } }),
      })
      void navigate({ to: '/agents', search: { agent: id } })
    } catch (e) {
      toast.error(e, 'Agent not created')
    } finally {
      setBusy(false)
    }
  }

  if (!canWrite) {
    return (
      <main className="mx-auto w-full max-w-[860px] px-10 py-12">
        <PageTitle
          title="New agent"
          lead="Creating agents needs the agents:write permission (builder role)."
        />
      </main>
    )
  }
  const sourceAgent = list.find((a) => a.id === source || a.name === source)
  return (
    <main className="mx-auto flex w-full max-w-[900px] flex-col gap-8 px-10 pt-12 pb-16 max-md:px-5">
      <PageTitle
        title="New agent"
        lead="Agents are named by your team. Start from a template, duplicate one you trust, or describe the agent you need."
      />
      <Tabs<Way>
        label="How to start"
        value={way}
        onChange={(w) => {
          setWay(w)
          setProfile(null)
        }}
        items={[
          { id: 'template', label: 'From a template' },
          { id: 'duplicate', label: 'Duplicate an agent' },
          { id: 'describe', label: 'Describe it' },
        ]}
      />
      {way === 'describe' && (
        <section aria-label="Describe the agent" className="flex flex-col gap-3">
          <Field
            label="Describe the agent you need"
            hint="What it looks after, which tools it reads, what it may propose, how it should talk."
          >
            {(id, d) => (
              <Textarea
                id={id}
                aria-describedby={d}
                rows={5}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="An agent that watches our Jira SECOPS backlog, finds stale tickets and proposes owners and deadlines from the CMDB. Concise, in English."
              />
            )}
          </Field>
          <Button
            variant="primary"
            className="self-start"
            busy={drafting === 'busy'}
            disabled={!description.trim()}
            onClick={() => void draft()}
          >
            Draft the profile
          </Button>
          {drafting === 'unavailable' && <NotServed what="The agent builder" endpoint="POST /agents/draft" />}
          {questions.length > 0 && (
            <ul className="m-0 list-disc pl-5 text-text-2">
              {questions.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          )}
        </section>
      )}
      {(way === 'template' || way === 'duplicate') && (
        <Field label={way === 'template' ? 'Template' : 'Agent to duplicate'} hint={sourceAgent?.role}>
          {(id, d) => (
            <Select
              id={id}
              aria-describedby={d}
              value={sourceAgent?.id ?? ''}
              onChange={(e) => {
                setSource(e.target.value)
                const a = list.find((x) => x.id === e.target.value)
                if (a && !name) setColor(a.avatar?.color ?? color)
              }}
            >
              <option value="">Choose…</option>
              {list
                .filter((a) => (way === 'template' ? !a.template || a.template === a.name : true))
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {way === 'template'
                      ? titleCase(a.template ?? a.name ?? '')
                      : `${a.display_name} (@${a.handle})`}
                  </option>
                ))}
            </Select>
          )}
        </Field>
      )}
      {(way !== 'describe' || profile) && (
        <section
          aria-label="Name and identity"
          className="flex flex-col gap-5 rounded-card border border-line bg-panel px-6 py-5"
        >
          <div className="flex items-center gap-4">
            <Avatar avatar={{ initials: initials(name || '?'), color }} name={name || '?'} size={48} />
            <div className="flex flex-col">
              <span className="font-display-face text-[26px] leading-none">{name || 'Name it'}</span>
              <span className="font-mono text-sm text-text-3">@{handle || 'handle'}</span>
            </div>
            <label className="ml-auto flex items-center gap-2 text-meta text-text-2">
              Color
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="h-8 w-10 rounded-ctl border border-line-2 bg-transparent"
              />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name your team uses">
              {(id) => (
                <Input
                  id={id}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value)
                    setHandle(slug(e.target.value))
                  }}
                  placeholder="Atlas"
                />
              )}
            </Field>
            <Field
              label="Handle"
              error={
                handle && !validHandle
                  ? 'Lowercase, digits and dashes'
                  : taken
                    ? 'Already used in the organisation'
                    : null
              }
            >
              {(id) => (
                <Input
                  id={id}
                  value={handle}
                  onChange={(e) => setHandle(e.target.value.toLowerCase())}
                  className="font-mono"
                />
              )}
            </Field>
            <Field label="Owner team">
              {(id) => (
                <Select id={id} value={team} onChange={(e) => setTeam(e.target.value)}>
                  <option value="">Your team</option>
                  {(teams.data ?? []).map((t) => (
                    <option key={t.id} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          {profile && way === 'describe' && (
            <div className="flex flex-col gap-4 border-t border-line pt-4">
              <Field label="Role">
                {(id) => (
                  <Textarea
                    id={id}
                    rows={2}
                    value={profile.role ?? ''}
                    onChange={(e) => setProfile({ ...profile, role: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Instructions">
                {(id) => (
                  <Textarea
                    id={id}
                    rows={6}
                    value={profile.instructions ?? ''}
                    onChange={(e) => setProfile({ ...profile, instructions: e.target.value })}
                    className="font-mono text-[12.5px]"
                  />
                )}
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Model">
                  {(id) => (
                    <Select
                      id={id}
                      value={profile.model ?? 'z-fast'}
                      onChange={(e) => setProfile({ ...profile, model: e.target.value })}
                    >
                      <option value="z-fast">z-fast</option>
                      <option value="z-heavy">z-heavy</option>
                    </Select>
                  )}
                </Field>
                <Field
                  label="Tools"
                  hint="Neutral names: read, terminal, gateway:<connector>.<resource>.* (reads only)"
                >
                  {(id, d) => (
                    <Input
                      id={id}
                      aria-describedby={d}
                      value={(profile.tools ?? []).join(', ')}
                      onChange={(e) =>
                        setProfile({ ...profile, tools: e.target.value.split(/[\s,]+/).filter(Boolean) })
                      }
                      className="font-mono text-sm"
                    />
                  )}
                </Field>
              </div>
              <fieldset className="m-0 grid gap-2 border-none p-0 sm:grid-cols-2">
                <legend className="mb-2 text-meta text-text-2">Skills</legend>
                {(skills.data?.items ?? []).map((s) => (
                  <Checkbox
                    key={s.id}
                    checked={(profile.skills ?? []).includes(s.name)}
                    onChange={(v) =>
                      setProfile({
                        ...profile,
                        skills: v
                          ? [...(profile.skills ?? []), s.name]
                          : (profile.skills ?? []).filter((x) => x !== s.name),
                      })
                    }
                    label={<span className="font-mono text-[12.5px]">{s.name}</span>}
                    description={s.description}
                  />
                ))}
              </fieldset>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button onClick={() => window.history.back()}>Cancel</Button>
            <Button
              variant="primary"
              busy={busy}
              disabled={!name.trim() || !validHandle || taken || (way !== 'describe' && !sourceAgent)}
              onClick={() => void create()}
            >
              Create {name || 'the agent'}
            </Button>
          </div>
        </section>
      )}
    </main>
  )
}
