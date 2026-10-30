import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { PERMISSIONS, type PermissionName } from '@/api/permissions.gen'
import { qk, useTeams } from '@/api/queries'
import type { EffectiveRole, User } from '@/api/types'
import { Avatar } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { EmptyState, QueryState } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Field, Input, Select, Skeleton } from '@/components/ui'
import { useCurrentMe } from '@/lib/auth'
import { clock, relative, shortDate } from '@/lib/format'
import { useHotkeys } from '@/lib/hotkeys'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'

function RoleChip({ r }: { r: EffectiveRole }) {
  const manual = r.source === 'manual'
  return (
    <span
      className={`inline-flex h-6 items-center gap-1.5 rounded-[6px] border px-2 text-sm ${manual ? 'border-transparent bg-wait-bg text-wait' : 'border-line-2'}`}
    >
      <span className="font-medium">{r.role}</span>
      <span className={manual ? '' : 'text-text-3'}>
        {manual ? 'manual' : 'GitLab group'}
        {r.scope !== 'org' ? ` · ${r.scope.replace(/^team:/, 'team ')}` : ''}
        {r.expires_at ? ` · until ${shortDate(r.expires_at)}` : ''}
      </span>
    </span>
  )
}

function statusLine(u: User): { tone: 'ok' | 'err' | 'idle'; label: string } {
  if (u.status === 'active') return { tone: 'ok', label: 'Active' }
  if (u.status === 'suspended') return { tone: 'err', label: 'Suspended' }
  return { tone: 'idle', label: 'Deprovisioned' }
}

function AddRole({ user, open, onClose }: { user: User; open: boolean; onClose: () => void }) {
  const roles = useQuery({ queryKey: qk.admin.roles, queryFn: () => unwrap(api.GET('/admin/roles')) })
  const teams = useTeams()
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [role, setRole] = useState('approver')
  const [scope, setScope] = useState('org')
  const [days, setDays] = useState(30)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      const expires = days > 0 ? new Date(Date.now() + days * 86400_000).toISOString() : null
      await stepUp.run(
        () =>
          unwrap(
            api.POST('/admin/role-bindings', {
              body: {
                subject: `user:${user.id}`,
                role,
                scope,
                expires_at: expires,
                ...(reason ? { reason } : {}),
              },
            }),
          ),
        {
          what: 'Granting a role',
          detail: `${user.username} · ${role} · ${scope}${expires ? ` · until ${shortDate(expires)}` : ''}`,
        },
      )
      await qc.invalidateQueries({ queryKey: ['admin'] })
      toast.ok(`${role} granted to ${user.name}`)
      onClose()
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'Role not granted')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Add a role for ${user.name}`}
      eyebrow="Manual bindings are audited with before and after; prefer GitLab groups for lasting access"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Re-authenticate and grant
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Role">
          {(id) => (
            <Select id={id} value={role} onChange={(e) => setRole(e.target.value)}>
              {(roles.data ?? []).map((r) => (
                <option key={r.id} value={r.name}>
                  {r.name}
                  {r.builtin ? '' : ' (custom)'}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Scope">
          {(id) => (
            <Select id={id} value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="org">Organisation</option>
              {(teams.data ?? []).map((t) => (
                <option key={t.id} value={`team:${t.id}`}>
                  Team {t.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Expires">
          {(id) => (
            <Select id={id} value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[7, 30, 90].map((d) => (
                <option key={d} value={d}>
                  In {d} days
                </option>
              ))}
              <option value={0}>Never</option>
            </Select>
          )}
        </Field>
        <Field label="Reason">
          {(id) => (
            <Input
              id={id}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Covering for the on-call approver"
            />
          )}
        </Field>
      </div>
    </Dialog>
  )
}

export function WhoCan({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [perm, setPerm] = useState<PermissionName>('actions:approve')
  const q = useQuery({
    queryKey: qk.admin.whoCan(perm),
    queryFn: () => unwrap(api.GET('/admin/who-can', { params: { query: { permission: perm } } })),
    enabled: open,
  })
  return (
    <Dialog open={open} onClose={onClose} title="Who can…?" width={620}>
      <div className="flex flex-col gap-4">
        <Select
          aria-label="Permission"
          value={perm}
          onChange={(e) => setPerm(e.target.value as PermissionName)}
        >
          {(Object.keys(PERMISSIONS) as PermissionName[]).map((p) => (
            <option key={p} value={p}>
              {p} · {PERMISSIONS[p]}
            </option>
          ))}
        </Select>
        <QueryState query={q} what="Who can" endpoint="GET /admin/who-can">
          {() => (
            <ul className="m-0 list-none p-0">
              {(q.data?.users ?? []).map((u, i) => (
                <li key={`${u.user}${i}`} className="flex justify-between gap-3 border-b border-line py-2">
                  <span>{u.user}</span>
                  <span className="text-meta text-text-3">
                    {u.scope} · via {u.via}
                  </span>
                </li>
              ))}
              {(q.data?.users ?? []).length === 0 && <li className="text-meta text-text-3">Nobody.</li>}
            </ul>
          )}
        </QueryState>
      </div>
    </Dialog>
  )
}

function UserPanel({ id }: { id: string }) {
  const me = useCurrentMe()
  const q = useQuery({
    queryKey: qk.admin.user(id),
    queryFn: () => unwrap(api.GET('/admin/users/{id}', { params: { path: { id } } })),
  })
  const bindings = useQuery({
    queryKey: qk.admin.bindings({ subject: `user:${id}` }),
    queryFn: () => unwrap(api.GET('/admin/role-bindings', { params: { query: { subject: `user:${id}` } } })),
  })
  const sessions = useQuery({
    queryKey: [...qk.admin.sessions, id],
    queryFn: () => unwrap(api.GET('/admin/sessions', { params: { query: { user: id, limit: 20 } } })),
  })
  const tokens = useQuery({
    queryKey: [...qk.admin.tokens, id],
    queryFn: () => unwrap(api.GET('/admin/tokens', { params: { query: { user: id, limit: 20 } } })),
  })
  const u = q.data
  const activity = useQuery({
    queryKey: ['audit', 'actor', u?.username],
    enabled: !!u,
    queryFn: () => unwrap(api.GET('/audit', { params: { query: { actor: u?.username ?? '', limit: 5 } } })),
  })
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  if (!u) return <Skeleton className="h-96" />
  const self = u.id === me.user.id
  const run = async (key: string, what: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key)
    try {
      await stepUp.run(fn, { what, detail: `${u.name} (@${u.username})`, destructive: key === 'suspend' })
      await qc.invalidateQueries({ queryKey: ['admin'] })
      toast.ok(ok)
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <aside
      aria-label={`${u.name} details`}
      className="flex flex-col rounded-card border border-line bg-panel"
    >
      <div className="flex items-center gap-3 border-b border-line px-5 py-4">
        <Avatar name={u.name || u.username} size={36} />
        <div className="flex min-w-0 flex-col">
          <span className="text-[16px] font-semibold">{u.name}</span>
          <span className="truncate font-mono text-sm text-text-3">
            @{u.username} · {u.issuer ?? 'gitlab'} sub {u.sub ?? '—'}
          </span>
        </div>
      </div>
      <section aria-label="Effective roles" className="flex flex-col gap-2.5 border-b border-line px-5 py-4">
        <h3 className="m-0 text-meta font-normal text-text-3">Effective roles</h3>
        {u.effective_roles.length === 0 && (
          <span className="text-meta text-text-3">
            None: {u.local_kind ? 'local account with fixed limits' : 'no mapped group'}.
          </span>
        )}
        {u.effective_roles.map((r, i) => {
          const binding = (bindings.data ?? []).find(
            (b) => b.role === r.role && b.scope === r.scope && b.source === 'manual',
          )
          return (
            <div key={i} className="grid grid-cols-[80px_1fr] gap-2 text-ui">
              <span className="font-medium">{r.role}</span>
              <span className="text-text-2">
                {r.scope === 'org' ? 'org' : r.scope.replace('team:', 'team ')} ·{' '}
                {r.source === 'gitlab'
                  ? `from GitLab group ${r.via ?? ''}`
                  : `manual${r.via ? `, granted by ${r.via}` : ''}`}
                {r.expires_at ? `, expires ${shortDate(r.expires_at)}` : ''}
                {binding && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className="text-text-2 underline underline-offset-2"
                      disabled={busy === binding.id}
                      onClick={() =>
                        void run(
                          binding.id,
                          'Revoking a role',
                          () =>
                            unwrap(
                              api.DELETE('/admin/role-bindings/{id}', {
                                params: { path: { id: binding.id } },
                              }),
                            ),
                          `${r.role} revoked`,
                        )
                      }
                    >
                      revoke
                    </button>
                  </>
                )}
              </span>
            </div>
          )
        })}
      </section>
      <section
        aria-label="Sessions and tokens"
        className="flex flex-col gap-2 border-b border-line px-5 py-4 text-ui"
      >
        <h3 className="m-0 text-meta font-normal text-text-3">Sessions and tokens</h3>
        {(sessions.data?.items ?? []).map((s) => (
          <div key={s.id} className="grid grid-cols-[48px_1fr] gap-2">
            <span className="text-text-3">{s.kind === 'web' ? 'Web' : 'CLI'}</span>
            <span>
              {s.device ?? s.user_agent?.slice(0, 40) ?? 'unknown'} · {s.ip ?? ''} · seen{' '}
              {relative(s.last_seen_at)}
              {s.step_up_at ? ` · step-up ${relative(s.step_up_at)}` : ''}
            </span>
          </div>
        ))}
        {(tokens.data?.items ?? []).map((t) => (
          <div key={t.id} className="grid grid-cols-[48px_1fr] gap-2">
            <span className="text-text-3">Token</span>
            <span>
              {t.name} · <span className="font-mono">{t.scopes.join(', ')}</span> · expires{' '}
              {shortDate(t.expires_at)}
            </span>
          </div>
        ))}
        {(sessions.data?.items ?? []).length + (tokens.data?.items ?? []).length === 0 && (
          <span className="text-meta text-text-3">No active session or token.</span>
        )}
      </section>
      <section
        aria-label="Recent activity"
        className="flex flex-col gap-1.5 border-b border-line px-5 py-4 text-ui"
      >
        <h3 className="m-0 text-meta font-normal text-text-3">Recent activity</h3>
        {(activity.data?.items ?? []).map((e) => (
          <div key={e.seq}>
            <span className="font-mono text-text-3">{clock(e.ts)}</span> {e.action.replace(/[._]/g, ' ')}{' '}
            {e.target_id ? <span className="text-text-3">· {e.target_type}</span> : null}
          </div>
        ))}
        {(activity.data?.items ?? []).length === 0 && (
          <span className="text-meta text-text-3">Nothing recorded yet.</span>
        )}
        <Link
          to="/history"
          search={{ q: u.username }}
          className="mt-1 text-meta text-text-2 underline underline-offset-2"
        >
          Full audit trail
        </Link>
      </section>
      <div className="flex flex-wrap gap-2 px-5 py-4">
        <Button size="sm" onClick={() => setAdding(true)}>
          Add a role
        </Button>
        <Button
          size="sm"
          busy={busy === 'revoke'}
          onClick={() =>
            void run(
              'revoke',
              'Revoking all sessions',
              () =>
                unwrap(api.POST('/admin/users/{id}/revoke-sessions', { params: { path: { id } }, body: {} })),
              'Sessions and CLI tokens revoked',
            )
          }
        >
          Revoke sessions
        </Button>
        {u.status === 'active' ? (
          <Button
            size="sm"
            variant="danger"
            disabled={self}
            title={self ? 'You cannot suspend yourself' : undefined}
            busy={busy === 'suspend'}
            onClick={() =>
              void run(
                'suspend',
                'Suspending a user',
                () =>
                  unwrap(
                    api.PATCH('/admin/users/{id}', { params: { path: { id } }, body: { action: 'suspend' } }),
                  ),
                `${u.name} suspended`,
              )
            }
          >
            Suspend
          </Button>
        ) : (
          <Button
            size="sm"
            busy={busy === 'reactivate'}
            onClick={() =>
              void run(
                'reactivate',
                'Reactivating a user',
                () =>
                  unwrap(
                    api.PATCH('/admin/users/{id}', {
                      params: { path: { id } },
                      body: { action: 'reactivate' },
                    }),
                  ),
                `${u.name} reactivated`,
              )
            }
          >
            Reactivate
          </Button>
        )}
      </div>
      {adding && <AddRole user={u} open onClose={() => setAdding(false)} />}
    </aside>
  )
}

export function UsersTab() {
  const search = useSearch({ from: '/admin' })
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [role, setRole] = useState('')
  const [source, setSource] = useState('')
  const [team, setTeam] = useState('')
  const [whoCan, setWhoCan] = useState(false)
  const users = useQuery({
    queryKey: qk.admin.users({ role }),
    queryFn: () =>
      unwrap(api.GET('/admin/users', { params: { query: { limit: 200, ...(role ? { role } : {}) } } })),
  })
  const teams = useTeams()
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (users.data?.items ?? []).filter(
      (u) =>
        (!needle || `${u.name} ${u.username} ${u.email ?? ''}`.toLowerCase().includes(needle)) &&
        (!source || u.effective_roles.some((r) => r.source === source)) &&
        (!team || u.teams.some((t) => t.id === team)),
    )
  }, [users.data, q, source, team])
  const selected = search.user && list.some((u) => u.id === search.user) ? search.user : (list[0]?.id ?? null)
  const select = (id: string) =>
    void navigate({ to: '/admin', search: { tab: 'users', user: id }, replace: true })
  useHotkeys({
    j: () => {
      const i = list.findIndex((u) => u.id === selected)
      const n = list[Math.min(list.length - 1, i + 1)]
      if (n) select(n.id)
    },
    k: () => {
      const i = list.findIndex((u) => u.id === selected)
      const n = list[Math.max(0, i - 1)]
      if (n) select(n.id)
    },
  })
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          data-search
          aria-label="Search people"
          placeholder="Search people"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="w-[360px] max-w-full"
        />
        <Select
          aria-label="Role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className="w-[140px]"
        >
          <option value="">Role: any</option>
          {['viewer', 'operator', 'approver', 'builder', 'auditor', 'admin'].map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Source"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="w-[160px]"
        >
          <option value="">Source: any</option>
          <option value="gitlab">GitLab group</option>
          <option value="manual">Manual</option>
        </Select>
        <Select
          aria-label="Team"
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          className="w-[160px]"
        >
          <option value="">Team: any</option>
          {(teams.data ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <div className="flex-1" />
        <Button onClick={() => setWhoCan(true)}>Who can…?</Button>
      </div>
      <QueryState query={users} what="Users" endpoint="GET /admin/users">
        {() =>
          list.length === 0 ? (
            <EmptyState icon="user" title="Nobody matches">
              People appear here at their first GitLab sign-in.
            </EmptyState>
          ) : (
            <div className="flex flex-wrap items-start gap-6">
              <div className="min-w-0 flex-[999_1_640px] overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse">
                  <thead>
                    <tr className="border-b border-line text-sm text-text-3">
                      {['Person', 'Team', 'Roles and source', 'Last sign-in', '2FA', 'Status'].map((h) => (
                        <th key={h} className="py-2.5 pr-3 pl-2.5 font-normal first:pl-2.5">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((u) => {
                      const st = statusLine(u)
                      const isSel = u.id === selected
                      return (
                        <tr
                          key={u.id}
                          tabIndex={0}
                          aria-selected={isSel}
                          onClick={() => select(u.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') select(u.id)
                          }}
                          className={`cursor-pointer border-b border-line ${isSel ? 'bg-raised shadow-[inset_2px_0_0_var(--text)]' : 'hover:bg-panel'}`}
                        >
                          <td className="py-[var(--cell-y)] pr-3 pl-2.5">
                            <div className="flex items-center gap-3">
                              <Avatar name={u.name || u.username} size={30} />
                              <span className="flex flex-col">
                                <span>{u.name}</span>
                                <span className="font-mono text-sm text-text-3">@{u.username}</span>
                              </span>
                            </div>
                          </td>
                          <td className="pr-3 pl-2.5">{u.teams.map((t) => t.name).join(', ') || '—'}</td>
                          <td className="pr-3 pl-2.5">
                            <div className="flex flex-col items-start gap-1 py-1">
                              {u.effective_roles.length ? (
                                u.effective_roles.map((r, i) => <RoleChip key={i} r={r} />)
                              ) : (
                                <span className="text-meta text-text-3">
                                  {u.local_kind ? `local · ${u.local_kind}` : 'none'}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="pr-3 pl-2.5">
                            {u.last_login_at ? relative(u.last_login_at) : 'never'}
                          </td>
                          <td className="pr-3 pl-2.5">{u.mfa_enforced ? 'Enforced' : '—'}</td>
                          <td className="pr-3 pl-2.5">
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
              </div>
              <div className="w-full flex-[1_1_320px] lg:max-w-[360px]">
                {selected && <UserPanel key={selected} id={selected} />}
              </div>
            </div>
          )
        }
      </QueryState>
      {whoCan && <WhoCan open onClose={() => setWhoCan(false)} />}
    </div>
  )
}
