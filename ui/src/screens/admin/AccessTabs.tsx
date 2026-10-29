import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { BUILTIN_ROLES, PERMISSIONS, type PermissionName } from '@/api/permissions.gen'
import { qk, useTeams } from '@/api/queries'
import type { GroupMappingConfig, GroupMappingDryRun, Role, Team } from '@/api/types'
import { Dialog } from '@/components/Dialog'
import { Icon } from '@/components/Icon'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Field, IconButton, Input, Select, Textarea } from '@/components/ui'
import { compactNumber } from '@/lib/format'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'
import { PendingChange } from './PendingChange'
import { WhoCan } from './Users'

// ------------------------------------------------------------------ Teams

function TeamDialog({ team, onClose }: { team: Team | null; onClose: () => void }) {
  const [name, setName] = useState(team?.name ?? '')
  const [budget, setBudget] = useState<number | ''>(team?.monthly_token_budget ?? '')
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const save = async () => {
    setBusy(true)
    const body = { name, monthly_token_budget: budget === '' ? null : budget }
    try {
      await stepUp.run(
        () => (team ? unwrap(api.PATCH('/teams/{id}', { params: { path: { id: team.id } }, body })) : unwrap(api.POST('/teams', { body }))),
        { what: team ? 'Changing a team' : 'Creating a team', detail: `${name}${budget ? ` · ${compactNumber(budget)} tokens / month` : ''}` },
      )
      await qc.invalidateQueries({ queryKey: qk.teams })
      toast.ok(team ? 'Team saved' : 'Team created')
      onClose()
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={team ? `Edit ${team.name}` : 'New team'}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Monthly token budget" hint="Empty for no team budget">
          {(id, d) => <Input id={id} aria-describedby={d} type="number" value={budget} onChange={(e) => setBudget(e.target.value === '' ? '' : Number(e.target.value))} />}
        </Field>
      </div>
    </Dialog>
  )
}

export function TeamsTab() {
  const teams = useTeams()
  const [edit, setEdit] = useState<Team | null | 'new'>(null)
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="m-0 text-text-2">Teams own agents, playbooks, budgets and connectors. Members come from GitLab groups (Group mapping) and manual bindings.</p>
        <Button size="sm" icon="plus" onClick={() => setEdit('new')}>
          New team
        </Button>
      </div>
      <QueryState query={teams} what="Teams" endpoint="GET /teams">
        {() => (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line text-sm text-text-3">
                {['Team', 'GitLab groups', 'Members', 'Monthly budget', ''].map((h) => (
                  <th key={h} className="py-2.5 pr-3 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(teams.data ?? []).map((t) => (
                <tr key={t.id} className="border-b border-line">
                  <td className="py-3 pr-3 font-medium">{t.name}</td>
                  <td className="pr-3 font-mono text-[12.5px]">{(t.gitlab_groups ?? []).join(', ') || '—'}</td>
                  <td className="pr-3">{t.members_count ?? 0}</td>
                  <td className="pr-3 font-mono">{t.monthly_token_budget ? compactNumber(t.monthly_token_budget) : '—'}</td>
                  <td className="text-right">
                    <Button size="xs" onClick={() => setEdit(t)}>
                      Edit
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryState>
      {edit && <TeamDialog team={edit === 'new' ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  )
}

// ------------------------------------------------------------------ Roles and permissions

function RoleDialog({ role, onClose }: { role: Role | null; onClose: () => void }) {
  const [name, setName] = useState(role?.name ?? '')
  const [description, setDescription] = useState(role?.description ?? '')
  const [perms, setPerms] = useState<string[]>(role?.permissions ?? [])
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const save = async () => {
    setBusy(true)
    const body = { name, description, permissions: perms }
    try {
      await stepUp.run(() => (role ? unwrap(api.PATCH('/admin/roles/{id}', { params: { path: { id: role.id } }, body })) : unwrap(api.POST('/admin/roles', { body }))), {
        what: role ? 'Changing a role' : 'Creating a role',
        detail: `${name}: ${perms.length} permissions`,
      })
      await qc.invalidateQueries({ queryKey: qk.admin.roles })
      toast.ok('Role saved')
      onClose()
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={role ? `Edit role ${role.name}` : 'New custom role'}
      width={720}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={!/^[a-z][a-z0-9-]+$/.test(name) || perms.length === 0} onClick={() => void save()}>
            Re-authenticate and save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" hint="Lowercase, e.g. release-approver">{(id, d) => <Input id={id} aria-describedby={d} value={name} disabled={!!role} onChange={(e) => setName(e.target.value)} className="font-mono" />}</Field>
          <Field label="Description">{(id) => <Input id={id} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        </div>
        <fieldset className="m-0 grid gap-x-6 gap-y-2 border-none p-0 sm:grid-cols-2">
          <legend className="mb-2 text-meta text-text-2">Permissions</legend>
          {(Object.keys(PERMISSIONS) as PermissionName[]).map((p) => (
            <label key={p} className="flex items-start gap-2.5">
              <input type="checkbox" className="mt-[3px]" checked={perms.includes(p)} onChange={(e) => setPerms(e.target.checked ? [...perms, p] : perms.filter((x) => x !== p))} />
              <span className="flex flex-col">
                <span className="font-mono text-[12.5px]">{p}</span>
                <span className="text-sm text-text-3">{PERMISSIONS[p]}</span>
              </span>
            </label>
          ))}
        </fieldset>
      </div>
    </Dialog>
  )
}

export function RolesTab() {
  const roles = useQuery({ queryKey: qk.admin.roles, queryFn: () => unwrap(api.GET('/admin/roles')) })
  const [edit, setEdit] = useState<Role | null | 'new'>(null)
  const [whoCan, setWhoCan] = useState(false)
  const list = roles.data ?? []
  const builtinOrder = Object.keys(BUILTIN_ROLES)
  const sorted = [...list].sort((a, b) => Number(b.builtin) - Number(a.builtin) || builtinOrder.indexOf(a.name) - builtinOrder.indexOf(b.name))
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-text-2">Deny by default: every API route declares one permission. Built-in roles are read-only; custom roles are audited.</p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setWhoCan(true)}>
            Who can…?
          </Button>
          <Button size="sm" icon="plus" onClick={() => setEdit('new')}>
            New role
          </Button>
        </div>
      </div>
      <QueryState query={roles} what="Roles" endpoint="GET /admin/roles">
        {() => (
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full min-w-[820px] border-collapse text-ui">
              <caption className="sr-only">Permission matrix: permissions by role</caption>
              <thead>
                <tr className="border-b border-line text-sm text-text-3">
                  <th className="sticky left-0 bg-bg px-4 py-2.5 font-normal">Permission</th>
                  {sorted.map((r) => (
                    <th key={r.id} className="px-2 py-2.5 text-center font-normal">
                      {r.builtin ? (
                        r.name
                      ) : (
                        <button type="button" className="underline underline-offset-2" onClick={() => setEdit(r)}>
                          {r.name}
                        </button>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(Object.keys(PERMISSIONS) as PermissionName[]).map((p) => (
                  <tr key={p} className="border-b border-line last:border-b-0">
                    <th scope="row" className="sticky left-0 bg-bg px-4 py-2 text-left font-normal">
                      <span className="font-mono text-[12.5px]">{p}</span>
                      <span className="block text-sm text-text-3">{PERMISSIONS[p]}</span>
                    </th>
                    {sorted.map((r) => {
                      const has = r.permissions.includes(p) || r.permissions.includes('*')
                      return (
                        <td key={r.id} className="px-2 text-center">
                          {has ? <Icon name="check" size={14} className="inline text-ok" /> : <span className="text-text-3">·</span>}
                          <span className="sr-only">{has ? `${r.name} has ${p}` : `${r.name} does not have ${p}`}</span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
      {edit && <RoleDialog role={edit === 'new' ? null : edit} onClose={() => setEdit(null)} />}
      {whoCan && <WhoCan open onClose={() => setWhoCan(false)} />}
    </div>
  )
}

// ------------------------------------------------------------------ Group mapping

export function GroupMappingTab() {
  const q = useQuery({ queryKey: qk.admin.groupMapping, queryFn: () => unwrap(api.GET('/admin/group-mapping')) })
  const roles = useQuery({ queryKey: qk.admin.roles, queryFn: () => unwrap(api.GET('/admin/roles')) })
  const teams = useTeams()
  const qc = useQueryClient()
  const [draft, setDraft] = useState<GroupMappingConfig | null>(null)
  const [dry, setDry] = useState<GroupMappingDryRun | null>(null)
  const [dryBusy, setDryBusy] = useState(false)
  const toast = useToast()
  const cfg = draft ?? q.data
  const edit = (next: GroupMappingConfig) => {
    setDraft(next)
    setDry(null)
  }
  const runDry = async (c: GroupMappingConfig) => {
    setDryBusy(true)
    try {
      setDry(await unwrap(api.POST('/admin/group-mapping:dry-run', { body: c })))
    } catch (e) {
      toast.error(e, 'Dry run failed')
    } finally {
      setDryBusy(false)
    }
  }
  const dryText = dry
    ? (() => {
        const gains = dry.changes.filter((c) => c.change === 'gain')
        const losses = dry.changes.filter((c) => c.change === 'loss')
        const parts = [
          gains.length ? `${gains.length} ${gains.length === 1 ? 'person gains' : 'people gain'} ${[...new Set(gains.map((g) => g.role ?? g.team))].join(', ')}` : null,
          losses.length ? `${losses.length} ${losses.length === 1 ? 'person loses' : 'people lose'} ${[...new Set(losses.map((g) => g.role ?? g.team))].join(', ')}` : 'Nobody loses access',
          dry.denied_users?.length ? `${dry.denied_users.length} could no longer sign in` : null,
        ].filter(Boolean)
        return `Dry run on ${dry.users_evaluated} users: ${parts.join('. ')}.`
      })()
    : null
  return (
    <QueryState query={q} what="Group mapping" endpoint="GET /admin/group-mapping">
      {() =>
        !cfg ? null : (
          <div className="flex flex-col gap-6">
            {draft && q.data && (
              <PendingChange
                title="Group mapping"
                before={q.data}
                after={draft}
                dryRun={
                  dryText ?? (
                    <Button size="xs" busy={dryBusy} onClick={() => void runDry(draft)}>
                      Run a dry run first
                    </Button>
                  )
                }
                onDiscard={() => {
                  setDraft(null)
                  setDry(null)
                }}
                onSave={async () => {
                  await unwrap(api.PUT('/admin/group-mapping', { body: draft }))
                  await qc.invalidateQueries({ queryKey: qk.admin.groupMapping })
                  await qc.invalidateQueries({ queryKey: ['admin', 'users'] })
                  setDraft(null)
                  setDry(null)
                }}
              />
            )}
            <section aria-label="Role mapping" className="flex flex-col gap-2">
              <h3 className="m-0 text-[15px] font-semibold">GitLab group → role</h3>
              <p className="m-0 text-meta text-text-3">Matched on group paths from userinfo (inherited included), never on names. Re-evaluated at every sign-in and every hour.</p>
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {cfg.group_mapping.map((m, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <Input aria-label="GitLab group path" value={m.group} onChange={(e) => edit({ ...cfg, group_mapping: cfg.group_mapping.map((x, j) => (j === i ? { ...x, group: e.target.value } : x)) })} className="w-[320px] font-mono text-sm" />
                    <Icon name="arrowRight" size={14} className="text-text-3" />
                    <Select aria-label="Role" value={m.role} onChange={(e) => edit({ ...cfg, group_mapping: cfg.group_mapping.map((x, j) => (j === i ? { ...x, role: e.target.value } : x)) })} className="w-[150px]">
                      {(roles.data ?? []).map((r) => (
                        <option key={r.id} value={r.name}>
                          {r.name}
                        </option>
                      ))}
                    </Select>
                    <Select aria-label="Scope" value={m.scope} onChange={(e) => edit({ ...cfg, group_mapping: cfg.group_mapping.map((x, j) => (j === i ? { ...x, scope: e.target.value as 'org' | 'team' } : x)) })} className="w-[130px]">
                      <option value="org">org</option>
                      <option value="team">their team</option>
                    </Select>
                    <IconButton icon="x" label={`Remove ${m.group}`} onClick={() => edit({ ...cfg, group_mapping: cfg.group_mapping.filter((_, j) => j !== i) })} />
                  </li>
                ))}
              </ul>
              <Button size="sm" icon="plus" className="self-start" onClick={() => edit({ ...cfg, group_mapping: [...cfg.group_mapping, { group: '', role: 'viewer', scope: 'team' }] })}>
                Add a mapping
              </Button>
            </section>
            <section aria-label="Team mapping" className="flex flex-col gap-2">
              <h3 className="m-0 text-[15px] font-semibold">GitLab group → team</h3>
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {cfg.team_mapping.map((m, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <Input aria-label="GitLab group path" value={m.group} onChange={(e) => edit({ ...cfg, team_mapping: cfg.team_mapping.map((x, j) => (j === i ? { ...x, group: e.target.value } : x)) })} className="w-[320px] font-mono text-sm" />
                    <Icon name="arrowRight" size={14} className="text-text-3" />
                    <Select aria-label="Team" value={m.team} onChange={(e) => edit({ ...cfg, team_mapping: cfg.team_mapping.map((x, j) => (j === i ? { ...x, team: e.target.value } : x)) })} className="w-[200px]">
                      {[...new Set([m.team, ...(teams.data ?? []).map((t) => t.name)])].map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </Select>
                    <IconButton icon="x" label={`Remove ${m.group}`} onClick={() => edit({ ...cfg, team_mapping: cfg.team_mapping.filter((_, j) => j !== i) })} />
                  </li>
                ))}
              </ul>
              <Button size="sm" icon="plus" className="self-start" onClick={() => edit({ ...cfg, team_mapping: [...cfg.team_mapping, { group: '', team: teams.data?.[0]?.name ?? '' }] })}>
                Add a team mapping
              </Button>
            </section>
            <section aria-label="Sign-in policy" className="grid gap-4 sm:grid-cols-2">
              <Field label="Allowed groups" hint="Users outside these groups cannot sign in">
                {(id, d) => <Input id={id} aria-describedby={d} value={cfg.allowed_groups.join(', ')} onChange={(e) => edit({ ...cfg, allowed_groups: e.target.value.split(/[\s,]+/).filter(Boolean) })} className="font-mono text-sm" />}
              </Field>
              <Field label="When no group matches">
                {(id) => (
                  <Select id={id} value={cfg.no_match} onChange={(e) => edit({ ...cfg, no_match: e.target.value as 'viewer' | 'deny' })}>
                    <option value="viewer">Give viewer</option>
                    <option value="deny">Refuse sign-in</option>
                  </Select>
                )}
              </Field>
            </section>
          </div>
        )
      }
    </QueryState>
  )
}

export function JsonSettingsEditor<T>({ title, value, onSave }: { title: string; value: T; onSave: (v: T) => Promise<void> }) {
  const [text, setText] = useState<string | null>(null)
  let parsed: T | null = null
  let error: string | null = null
  if (text !== null) {
    try {
      parsed = JSON.parse(text) as T
    } catch (e) {
      error = e instanceof Error ? e.message : 'Invalid JSON'
    }
  }
  return (
    <div className="flex flex-col gap-3">
      {parsed !== null && (
        <PendingChange
          title={title}
          before={value}
          after={parsed}
          onDiscard={() => setText(null)}
          onSave={async () => {
            if (parsed !== null) await onSave(parsed)
            setText(null)
          }}
        />
      )}
      <Textarea aria-label={`${title} (JSON)`} rows={12} spellCheck={false} value={text ?? JSON.stringify(value, null, 2)} onChange={(e) => setText(e.target.value)} className="font-mono text-[12px]" />
      {error && (
        <span role="alert" className="text-meta text-err">
          {error}
        </span>
      )}
    </div>
  )
}

export function EmptyTab({ title, children }: { title: string; children: React.ReactNode }) {
  return <EmptyState title={title}>{children}</EmptyState>
}
