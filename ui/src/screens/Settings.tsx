import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import {
  qk,
  useConnectors,
  useGuardrails,
  useInferenceModels,
  useMyConnections,
  useMySessions,
  useMyTokens,
  useRoutingPolicy,
} from '@/api/queries'
import type { Guardrail } from '@/api/types'
import { PERMISSIONS, type PermissionName } from '@/api/permissions.gen'
import { Dialog } from '@/components/Dialog'
import { Icon } from '@/components/Icon'
import { EmptyState, QueryState } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Checkbox, Field, Input, Radio, Select } from '@/components/ui'
import { useCan, useCurrentMe } from '@/lib/auth'
import { dateTime, relative, titleCase } from '@/lib/format'
import {
  DENSITIES,
  FONT_LABELS,
  FONTS,
  THEME_LABELS,
  THEMES,
  usePrefs,
  type Density,
  type Font,
  type Theme,
} from '@/lib/prefs'
import { useStepUp } from '@/shell/stepup'

type Section = 'appearance' | 'models' | 'guardrails' | 'connections' | 'access' | 'notifications'
const SECTIONS: { id: Section; label: string }[] = [
  { id: 'appearance', label: 'Appearance' },
  { id: 'models', label: 'Models and routing' },
  { id: 'guardrails', label: 'Guardrails' },
  { id: 'connections', label: 'Connections' },
  { id: 'access', label: 'Access and tokens' },
  { id: 'notifications', label: 'Notifications' },
]

function ThemeCard({ theme, selected, onSelect }: { theme: Theme; selected: boolean; onSelect: () => void }) {
  return (
    <label className="flex cursor-pointer flex-col gap-2.5">
      <span
        data-theme={theme}
        className={`flex h-[94px] overflow-hidden rounded-panel border-2 ${selected ? 'border-text' : 'border-transparent'}`}
        style={{ background: 'var(--bg)' }}
        aria-hidden="true"
      >
        <span
          className="w-[52px] border-r"
          style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
        />
        <span className="flex flex-1 flex-col gap-1.5 p-3.5">
          <span className="h-2 w-[60%] rounded-full" style={{ background: 'var(--text)' }} />
          <span className="h-1 w-[85%] rounded-full" style={{ background: 'var(--line-2)' }} />
          <span className="h-1 w-[70%] rounded-full" style={{ background: 'var(--line-2)' }} />
          <span className="mt-auto h-3 w-9 rounded-[3px]" style={{ background: 'var(--inv-bg)' }} />
        </span>
      </span>
      <span className="flex items-center gap-2.5">
        <input
          type="radio"
          name="theme"
          checked={selected}
          onChange={onSelect}
          className="h-[14px] w-[14px]"
        />
        {THEME_LABELS[theme]}
      </span>
    </label>
  )
}

function Appearance() {
  const p = usePrefs()
  return (
    <section aria-labelledby="s-appearance" className="flex flex-col gap-6">
      <h2 id="s-appearance" className="m-0 text-[17px] font-semibold">
        Appearance
      </h2>
      <fieldset className="m-0 flex flex-col gap-3 border-none p-0">
        <legend className="mb-3 text-meta text-text-2">Theme</legend>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {THEMES.map((t) => (
            <ThemeCard
              key={t}
              theme={t}
              selected={!p.followSystem && p.theme === t}
              onSelect={() => p.setTheme(t)}
            />
          ))}
        </div>
      </fieldset>
      <div className="grid gap-8 sm:grid-cols-[auto_auto_1fr]">
        <fieldset className="m-0 flex flex-col gap-2.5 border-none p-0">
          <legend className="mb-2.5 text-meta text-text-2">Density</legend>
          {DENSITIES.map((d) => (
            <Radio<Density>
              key={d}
              name="density"
              value={d}
              current={p.density}
              onChange={p.setDensity}
              label={titleCase(d)}
            />
          ))}
        </fieldset>
        <fieldset className="m-0 flex flex-col gap-2.5 border-none p-0">
          <legend className="mb-2.5 text-meta text-text-2">Typeface</legend>
          {FONTS.map((f) => (
            <Radio<Font>
              key={f}
              name="font"
              value={f}
              current={p.font}
              onChange={p.setFont}
              label={
                <span data-font={f} style={{ fontFamily: 'var(--sans)' }}>
                  {FONT_LABELS[f]}
                </span>
              }
            />
          ))}
        </fieldset>
        <fieldset className="m-0 flex flex-col gap-2.5 border-none p-0">
          <legend className="mb-2.5 text-meta text-text-2">Follow the system</legend>
          <Checkbox
            checked={p.followSystem}
            onChange={p.setFollowSystem}
            label="Day by day, Night by night"
            description="Switches with your operating system's light or dark setting."
          />
        </fieldset>
      </div>
      <p className="m-0 text-meta text-text-3">
        Stored in this browser only. High contrast meets WCAG AAA for text.
      </p>
    </section>
  )
}

function Models() {
  const models = useInferenceModels()
  const routing = useRoutingPolicy()
  return (
    <section aria-labelledby="s-models" className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="s-models" className="m-0 text-[17px] font-semibold">
          Models and routing
        </h2>
        {models.data && (
          <span className="flex items-center gap-2 text-meta text-text-2">
            <Dot tone={models.data.every((m) => m.status === 'healthy') ? 'ok' : 'wait'} />
            inferX via the Z gateway
          </span>
        )}
      </div>
      <QueryState query={models} what="The model list" endpoint="GET /inference/models">
        {() => (
          <ul className="m-0 list-none rounded-card border border-line bg-panel p-0">
            {(models.data ?? []).map((m) => (
              <li
                key={m.alias}
                className="grid grid-cols-[120px_1fr_auto] items-center gap-4 border-b border-line px-5 py-3.5 last:border-b-0"
              >
                <span className="font-mono">{m.alias}</span>
                <span className="text-text-2">
                  {m.provider} · {m.kind}
                  {m.context ? ` · ${Math.round(m.context / 1000)}k context` : ''}
                </span>
                <span
                  className={`text-meta ${m.tool_call_ok ? 'text-ok' : m.tool_call_ok === false ? 'text-err' : 'text-text-3'}`}
                >
                  {m.tool_call_ok === null || m.tool_call_ok === undefined
                    ? 'Not probed yet'
                    : m.tool_call_ok
                      ? 'Tool calling OK'
                      : 'Tool calling failing'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <p className="m-0 text-meta text-text-3">
        Agents only name aliases; the alias → model mapping lives in the LiteLLM configuration and every alias
        is gated by the tool-calling check before use.
        {routing.data
          ? ` ${routing.data.never_external.join(' and ')} never leave the internal cluster.`
          : ''}
      </p>
    </section>
  )
}

function GuardrailRow({
  name,
  g,
  onSave,
  canManage,
}: {
  name: string
  g: Guardrail
  onSave: (v: unknown) => Promise<void>
  canManage: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [num, setNum] = useState(typeof g.value === 'number' ? g.value : 0)
  const set = (v: unknown) => {
    setBusy(true)
    onSave(v)
      .catch(() => undefined)
      .finally(() => {
        setBusy(false)
      })
  }
  const label = g.description ?? titleCase(name)
  return (
    <li className="flex items-center gap-4 border-b border-line py-4">
      <div className="flex min-w-0 flex-1 flex-col">
        <span>{label}</span>
        <span className="text-meta text-text-3">
          {g.locked ? (
            'Locked on by the platform. Packs, playbooks and prompts cannot change it.'
          ) : (
            <span className="font-mono">{name}</span>
          )}
        </span>
      </div>
      {typeof g.value === 'boolean' ? (
        <span className="flex items-center gap-2">
          {g.locked && <Icon name="lock" size={13} className="text-text-3" />}
          <input
            type="checkbox"
            className="h-[15px] w-[15px]"
            aria-label={label}
            checked={g.value}
            disabled={g.locked || !canManage || busy}
            onChange={(e) => set(e.target.checked)}
          />
        </span>
      ) : typeof g.value === 'number' ? (
        <span className="flex items-center gap-2">
          <Input
            type="number"
            aria-label={label}
            value={num}
            disabled={g.locked || !canManage || busy}
            onChange={(e) => setNum(Number(e.target.value))}
            className="h-8 w-[100px] text-right font-mono"
          />
          {num !== g.value && (
            <Button size="xs" busy={busy} onClick={() => set(num)}>
              Save
            </Button>
          )}
        </span>
      ) : (
        <span className="flex items-center gap-2 font-mono text-[12.5px]">
          {g.locked && <Icon name="lock" size={13} className="text-text-3" />}
          {JSON.stringify(g.value)}
        </span>
      )}
    </li>
  )
}

function Guardrails() {
  const q = useGuardrails()
  const canManage = useCan('guardrails:manage')
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const save = (name: string) => async (value: unknown) => {
    const current = q.data
    const g = current?.[name]
    if (!current || !g) return
    try {
      await stepUp.run(
        () => unwrap(api.PUT('/settings/guardrails', { body: { ...current, [name]: { ...g, value } } })),
        {
          what: 'Changing a guardrail',
          detail: `${name}: ${JSON.stringify(g.value)} → ${JSON.stringify(value)}`,
        },
      )
      await qc.invalidateQueries({ queryKey: qk.guardrails })
      toast.ok('Guardrail saved')
    } catch (e) {
      if (!(e instanceof Error && e.name === 'StepUpCancelled')) toast.error(e, 'Not saved')
      throw e
    }
  }
  return (
    <section aria-labelledby="s-guardrails" className="flex flex-col gap-2">
      <h2 id="s-guardrails" className="m-0 text-[17px] font-semibold">
        Guardrails
      </h2>
      <QueryState query={q} what="Guardrails" endpoint="GET /settings/guardrails">
        {() => (
          <ul className="m-0 list-none border-t border-line p-0">
            {Object.entries(q.data ?? {})
              .sort(([, a], [, b]) => Number(b.locked) - Number(a.locked))
              .map(([k, g]) => (
                <GuardrailRow key={k} name={k} g={g} onSave={save(k)} canManage={canManage} />
              ))}
          </ul>
        )}
      </QueryState>
      {!canManage && (
        <p className="m-0 text-meta text-text-3">
          Only admins change guardrails. Every change needs a fresh sign-in and is audited.
        </p>
      )}
    </section>
  )
}

function Connections() {
  const q = useMyConnections()
  const connectors = useConnectors()
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const delegated = (connectors.data ?? []).filter(
    (c) =>
      c.credentials?.some((x) => x.scope === 'user') ||
      JSON.stringify(c.manifest.auth ?? {}).includes('"oauth"') ||
      (c.manifest as Record<string, unknown>).oauth !== undefined,
  )
  const connect = async (connector: string) => {
    setBusy(connector)
    try {
      const r = await unwrap(
        api.POST('/me/connections/{connector}/oauth/start', {
          params: { path: { connector } },
          body: { purpose: 'read' },
        }),
      )
      window.location.assign(r.redirect_url)
    } catch (e) {
      toast.error(
        e,
        isNotServed(e) ? 'Personal connections are not available on this server yet' : 'Could not connect',
      )
      setBusy(null)
    }
  }
  const revoke = async (id: string) => {
    setBusy(id)
    try {
      await unwrap(api.DELETE('/me/connections/{id}', { params: { path: { id } } }))
      await qc.invalidateQueries({ queryKey: qk.myConnections })
      toast.ok('Revoked. Agents can no longer act as you on it.')
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <section aria-labelledby="s-connections" className="flex flex-col gap-4">
      <h2 id="s-connections" className="m-0 text-[17px] font-semibold">
        Connections
      </h2>
      <p className="m-0 text-text-2">
        Your own accounts (Outlook, Teams, OneDrive, Google…). Agents use them only in missions you start or
        triggers you own, with read and write granted separately. Every call shows “runs as you”.
      </p>
      <QueryState query={q} what="Personal connections" endpoint="GET /me/connections">
        {() =>
          (q.data ?? []).length === 0 ? (
            <EmptyState icon="link" title="No personal connection">
              Connect an account below when a playbook needs to act as you.
            </EmptyState>
          ) : (
            <ul className="m-0 list-none rounded-card border border-line bg-panel p-0">
              {(q.data ?? []).map((c) => (
                <li
                  key={c.id}
                  className="flex flex-wrap items-center gap-4 border-b border-line px-5 py-3.5 last:border-b-0"
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="font-medium">
                      {connectors.data?.find((x) => x.name === c.connector)?.display_name ?? c.connector}
                    </span>
                    <span className="text-meta text-text-3">
                      read {c.read_scopes.join(', ') || '—'} · write {c.write_scopes.join(', ') || '—'} · last
                      used {c.last_used_at ? relative(c.last_used_at) : 'never'}
                    </span>
                  </span>
                  <span className="flex items-center gap-1.5 text-meta">
                    <Dot tone={c.status === 'active' ? 'ok' : c.status === 'expired' ? 'wait' : 'idle'} />
                    {titleCase(c.status)}
                  </span>
                  {c.status === 'active' && (
                    <Button size="xs" variant="danger" busy={busy === c.id} onClick={() => void revoke(c.id)}>
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )
        }
      </QueryState>
      {delegated.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {delegated.map((c) => (
            <Button key={c.id} size="sm" busy={busy === c.name} onClick={() => void connect(c.name)}>
              Connect {c.display_name}
            </Button>
          ))}
        </div>
      )}
    </section>
  )
}

function TokenDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useCurrentMe()
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState('')
  const [days, setDays] = useState(30)
  const [scopes, setScopes] = useState<string[]>(['missions:read'])
  const [created, setCreated] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const mine = me.permissions
    .map((p) => p.permission)
    .filter((p, i, a) => a.indexOf(p) === i && p in PERMISSIONS) as PermissionName[]
  const save = async () => {
    setBusy(true)
    try {
      const t = await unwrap(api.POST('/me/tokens', { body: { name, scopes, expires_in_days: days } }))
      setCreated(t.token ?? null)
      await qc.invalidateQueries({ queryKey: qk.myTokens })
    } catch (e) {
      toast.error(e, 'Token not created')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New API token"
      eyebrow="For scripts. Scoped, expiring, stored hashed."
      width={600}
      footer={
        created ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              busy={busy}
              disabled={!name.trim() || scopes.length === 0}
              onClick={() => void save()}
            >
              Create token
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="flex flex-col gap-3">
          <p className="m-0 text-text-2">Copy it now: it is shown once and never again.</p>
          <Input
            readOnly
            value={created}
            aria-label="New token"
            className="font-mono text-sm"
            onFocus={(e) => e.target.select()}
          />
          <Button
            size="sm"
            icon="copy"
            className="self-start"
            onClick={() => void navigator.clipboard.writeText(created).then(() => toast.ok('Copied'))}
          >
            Copy
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-[1fr_160px]">
            <Field label="Name">
              {(id) => (
                <Input
                  id={id}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="nightly-report"
                />
              )}
            </Field>
            <Field label="Expires in">
              {(id) => (
                <Select id={id} value={days} onChange={(e) => setDays(Number(e.target.value))}>
                  {[7, 30, 60, 90].map((d) => (
                    <option key={d} value={d}>
                      {d} days
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <fieldset className="m-0 grid gap-2 border-none p-0 sm:grid-cols-2">
            <legend className="mb-2 text-meta text-text-2">Scopes (only permissions you hold)</legend>
            {mine.map((p) => (
              <Checkbox
                key={p}
                checked={scopes.includes(p)}
                onChange={(v) => setScopes(v ? [...scopes, p] : scopes.filter((x) => x !== p))}
                label={<span className="font-mono text-[12.5px]">{p}</span>}
              />
            ))}
          </fieldset>
        </div>
      )}
    </Dialog>
  )
}

function Access() {
  const sessions = useMySessions()
  const tokens = useMyTokens()
  const qc = useQueryClient()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const revokeSession = async (id: string) => {
    try {
      await unwrap(api.DELETE('/me/sessions/{id}', { params: { path: { id } } }))
      await qc.invalidateQueries({ queryKey: qk.mySessions })
      toast.ok('Session revoked')
    } catch (e) {
      toast.error(e)
    }
  }
  const revokeToken = async (id: string) => {
    try {
      await unwrap(api.DELETE('/me/tokens/{id}', { params: { path: { id } } }))
      await qc.invalidateQueries({ queryKey: qk.myTokens })
      toast.ok('Token revoked')
    } catch (e) {
      toast.error(e)
    }
  }
  return (
    <section aria-labelledby="s-access" className="flex flex-col gap-6">
      <h2 id="s-access" className="m-0 text-[17px] font-semibold">
        Access and tokens
      </h2>
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-meta font-normal text-text-3">Your sessions (web and CLI)</h3>
        <QueryState query={sessions} what="Sessions" endpoint="GET /me/sessions">
          {() => (
            <ul className="m-0 list-none border-t border-line p-0">
              {(sessions.data ?? []).map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-4 border-b border-line py-3">
                  <span className="w-12 font-mono text-sm uppercase text-text-3">{s.kind}</span>
                  <span className="min-w-0 flex-1">
                    {s.device ?? s.user_agent?.slice(0, 60) ?? 'Unknown device'}
                    <span className="text-meta text-text-3">
                      {' '}
                      · {s.ip ?? 'no IP'} · seen {relative(s.last_seen_at)} · expires {relative(s.expires_at)}
                    </span>
                  </span>
                  {s.current ? (
                    <span className="text-meta text-ok">this session</span>
                  ) : (
                    <Button size="xs" variant="danger" onClick={() => void revokeSession(s.id)}>
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
              {(sessions.data ?? []).length === 0 && (
                <li className="py-3 text-meta text-text-3">No other session.</li>
              )}
            </ul>
          )}
        </QueryState>
      </div>
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h3 className="m-0 text-meta font-normal text-text-3">Personal API tokens</h3>
          <Button size="xs" icon="plus" onClick={() => setCreating(true)}>
            New token
          </Button>
        </div>
        <QueryState query={tokens} what="API tokens" endpoint="GET /me/tokens">
          {() => (
            <ul className="m-0 list-none border-t border-line p-0">
              {(tokens.data ?? []).map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-4 border-b border-line py-3">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{t.name}</span>
                    <span className="text-meta text-text-3">
                      {' '}
                      · {t.scopes.join(', ')} · expires{' '}
                      {dateTime(t.expires_at).split(' ').slice(0, 3).join(' ')} · used{' '}
                      {t.last_used_at ? relative(t.last_used_at) : 'never'}
                    </span>
                  </span>
                  {t.revoked_at ? (
                    <span className="text-meta text-text-3">revoked</span>
                  ) : (
                    <Button size="xs" variant="danger" onClick={() => void revokeToken(t.id)}>
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
              {(tokens.data ?? []).length === 0 && (
                <li className="py-3 text-meta text-text-3">No token. Prefer `z login` for the CLI.</li>
              )}
            </ul>
          )}
        </QueryState>
      </div>
      {creating && <TokenDialog open onClose={() => setCreating(false)} />}
    </section>
  )
}

function Notifications() {
  return (
    <section aria-labelledby="s-notif" className="flex flex-col gap-3">
      <h2 id="s-notif" className="m-0 text-[17px] font-semibold">
        Notifications
      </h2>
      <p className="m-0 text-text-2">
        Decisions waiting for you appear in Review and on the bell everywhere in the app. Mattermost, Slack,
        email and webhook notifications are configured by admins (Admin → Platform); personal notification
        preferences are not in the API yet.
      </p>
    </section>
  )
}

export function Settings() {
  const search = useSearch({ from: '/settings' })
  const navigate = useNavigate()
  const section = (SECTIONS.some((s) => s.id === search.section) ? search.section : 'appearance') as Section
  return (
    <main className="flex min-h-screen flex-wrap md:flex-nowrap">
      <nav
        aria-label="Settings sections"
        className="flex w-full flex-col gap-1 border-line px-3.5 pt-7 md:w-[220px] md:flex-none md:border-r"
      >
        <h1 className="m-0 mb-4 px-2.5 text-xl font-semibold">Settings</h1>
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={section === s.id ? 'page' : undefined}
            onClick={() => void navigate({ to: '/settings', search: { section: s.id }, replace: true })}
            className={`flex h-8 items-center rounded-ctl px-2.5 text-left ${section === s.id ? 'bg-raised text-text' : 'text-text-2 hover:text-text'}`}
          >
            {s.label}
          </button>
        ))}
      </nav>
      <div className="flex w-full max-w-[920px] flex-col gap-12 px-10 pt-8 pb-16 max-md:px-5">
        {section === 'appearance' && (
          <>
            <Appearance />
            <Models />
            <Guardrails />
          </>
        )}
        {section === 'models' && <Models />}
        {section === 'guardrails' && <Guardrails />}
        {section === 'connections' && <Connections />}
        {section === 'access' && <Access />}
        {section === 'notifications' && <Notifications />}
      </div>
    </main>
  )
}
