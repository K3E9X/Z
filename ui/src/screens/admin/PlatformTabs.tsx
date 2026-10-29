import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useAgents, useChannels, useGuardrails, useTeams } from '@/api/queries'
import type { AuditVerify, AuthSettings } from '@/api/types'
import { Dialog } from '@/components/Dialog'
import { NotServed, QueryState } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Checkbox, DefList, Field, Input, Select } from '@/components/ui'
import { dateTime, relative, titleCase } from '@/lib/format'
import { useCan } from '@/lib/auth'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'
import { PendingChange } from './PendingChange'

// ------------------------------------------------------------------ Sign-in

export function SignInTab() {
  const q = useQuery({
    queryKey: qk.admin.authSettings,
    queryFn: () => unwrap(api.GET('/admin/auth-settings')),
  })
  const qc = useQueryClient()
  const toast = useToast()
  const [draft, setDraft] = useState<AuthSettings | null>(null)
  const [testing, setTesting] = useState(false)
  const s = draft ?? q.data
  const set = (patch: Partial<AuthSettings>) => {
    if (s) setDraft({ ...s, ...patch })
  }
  const test = async () => {
    setTesting(true)
    try {
      const r = await unwrap(api.POST('/admin/auth-settings:test', { body: s ?? {} }))
      if (r.ok)
        toast.ok(
          `GitLab reachable · discovery ${r.discovery ? 'ok' : 'failed'} · JWKS ${r.jwks ? 'ok' : 'failed'}`,
        )
      else toast.error(new Error(r.detail ?? 'test failed'), 'Sign-in test failed')
    } catch (e) {
      toast.error(e, 'Sign-in test failed')
    } finally {
      setTesting(false)
    }
  }
  const local = s?.local_accounts as Record<string, unknown> | undefined
  const boot = (local?.bootstrap ?? {}) as { enabled?: boolean; state?: string; expires_at?: string }
  const tests = (local?.test_accounts ?? []) as { name?: string; role?: string; expires_at?: string }[]
  const breakglass = (local?.breakglass ?? {}) as { enabled?: boolean }
  return (
    <QueryState query={q} what="Sign-in settings" endpoint="GET /admin/auth-settings">
      {() =>
        !s ? null : (
          <div className="flex flex-col gap-7">
            {draft && q.data && (
              <PendingChange
                title="Sign-in settings"
                before={{ ...q.data, client_secret: undefined }}
                after={{ ...draft, client_secret: draft.client_secret ? '•••• (new secret)' : undefined }}
                onDiscard={() => setDraft(null)}
                onSave={async () => {
                  await unwrap(api.PUT('/admin/auth-settings', { body: draft }))
                  await qc.invalidateQueries({ queryKey: qk.admin.authSettings })
                  setDraft(null)
                }}
              />
            )}
            <section aria-label="GitLab OIDC" className="grid gap-4 sm:grid-cols-2">
              <h3 className="m-0 text-[15px] font-semibold sm:col-span-2">GitLab OIDC</h3>
              <Field label="Mode">
                {(id) => (
                  <Select
                    id={id}
                    value={s.mode}
                    onChange={(e) => set({ mode: e.target.value as 'oidc' | 'dev' })}
                  >
                    <option value="oidc">GitLab OIDC</option>
                    <option value="dev">Development (single local user)</option>
                  </Select>
                )}
              </Field>
              <Field label="Issuer">
                {(id) => (
                  <Input
                    id={id}
                    value={s.issuer_url}
                    onChange={(e) => set({ issuer_url: e.target.value })}
                    placeholder="https://gitlab.example.internal"
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Client id">
                {(id) => (
                  <Input
                    id={id}
                    value={s.client_id}
                    onChange={(e) => set({ client_id: e.target.value })}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Client secret" hint="Write-only: never shown back">
                {(id, d) => (
                  <Input
                    id={id}
                    aria-describedby={d}
                    type="password"
                    autoComplete="off"
                    value={s.client_secret ?? ''}
                    onChange={(e) => set({ client_secret: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Scopes">
                {(id) => (
                  <Input
                    id={id}
                    value={s.scopes.join(' ')}
                    onChange={(e) => set({ scopes: e.target.value.split(/\s+/).filter(Boolean) })}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Groups claim">
                {(id) => (
                  <Input
                    id={id}
                    value={s.groups_claim}
                    onChange={(e) => set({ groups_claim: e.target.value })}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
                <Button busy={testing} onClick={() => void test()}>
                  Test login settings
                </Button>
                <span className="flex items-center gap-2 text-meta text-text-2">
                  <Dot tone={s.jwks?.ok ? 'ok' : s.jwks ? 'err' : 'idle'} />
                  JWKS{' '}
                  {s.jwks?.ok
                    ? `ok · ${s.jwks.keys ?? 0} keys · fetched ${relative(s.jwks.fetched_at)}`
                    : (s.jwks?.error ?? 'not fetched yet')}
                </span>
              </div>
            </section>
            <section aria-label="Sessions" className="grid gap-4 sm:grid-cols-4">
              <h3 className="m-0 text-[15px] font-semibold sm:col-span-4">Sessions and step-up</h3>
              <Field label="Idle (minutes)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    value={s.session?.idle_minutes ?? 120}
                    onChange={(e) => set({ session: { ...s.session, idle_minutes: Number(e.target.value) } })}
                  />
                )}
              </Field>
              <Field label="Absolute (hours)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    value={s.session?.absolute_hours ?? 12}
                    onChange={(e) =>
                      set({ session: { ...s.session, absolute_hours: Number(e.target.value) } })
                    }
                  />
                )}
              </Field>
              <Field label="CLI tokens (days)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    value={s.session?.cli_token_days ?? 30}
                    onChange={(e) =>
                      set({ session: { ...s.session, cli_token_days: Number(e.target.value) } })
                    }
                  />
                )}
              </Field>
              <Field label="Step-up max age (s)">
                {(id) => (
                  <Input
                    id={id}
                    type="number"
                    value={s.step_up_max_age_s ?? 300}
                    onChange={(e) => set({ step_up_max_age_s: Number(e.target.value) })}
                  />
                )}
              </Field>
            </section>
            <section aria-label="Local accounts" className="flex flex-col gap-3">
              <h3 className="m-0 text-[15px] font-semibold">Local accounts</h3>
              <DefList
                className="text-ui"
                rows={[
                  [
                    'Bootstrap admin',
                    boot.enabled
                      ? `${boot.state ?? 'active'}${boot.expires_at ? ` · disabled ${relative(boot.expires_at)} or at the first GitLab admin sign-in` : ''}`
                      : 'disabled',
                  ],
                  [
                    'Test accounts',
                    tests.length
                      ? tests
                          .map(
                            (t) =>
                              `${t.name ?? '?'} (${t.role ?? 'operator'}${t.expires_at ? `, until ${dateTime(t.expires_at).split(' ').slice(0, 3).join(' ')}` : ''})`,
                          )
                          .join(', ')
                      : 'none (refused in production)',
                  ],
                  [
                    'Break-glass',
                    breakglass.enabled ? (
                      <span key="b" className="text-err">
                        enabled · every use alerts all admins
                      </span>
                    ) : (
                      'disabled'
                    ),
                  ],
                ]}
              />
              <p className="m-0 text-meta text-text-3">
                Local accounts are configured on the server (z-server bootstrap, config/z.yaml), not here;
                they are rate-limited, locked after 5 failures and always audited.
              </p>
            </section>
          </div>
        )
      }
    </QueryState>
  )
}

// ------------------------------------------------------------------ Sessions and tokens

export function SessionsTab() {
  const sessions = useQuery({
    queryKey: qk.admin.sessions,
    queryFn: () => unwrap(api.GET('/admin/sessions', { params: { query: { limit: 200 } } })),
  })
  const tokens = useQuery({
    queryKey: qk.admin.tokens,
    queryFn: () => unwrap(api.GET('/admin/tokens', { params: { query: { limit: 200 } } })),
  })
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const revoke = async (kind: 'session' | 'token', id: string, who: string) => {
    try {
      await stepUp.run(
        () =>
          kind === 'session'
            ? unwrap(api.DELETE('/admin/sessions/{id}', { params: { path: { id } } }))
            : unwrap(api.DELETE('/admin/tokens/{id}', { params: { path: { id } } })),
        { what: kind === 'session' ? 'Revoking a session' : 'Revoking an API token', detail: who },
      )
      await qc.invalidateQueries({ queryKey: kind === 'session' ? qk.admin.sessions : qk.admin.tokens })
      toast.ok('Revoked')
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e)
    }
  }
  return (
    <div className="flex flex-col gap-8">
      <section aria-label="Active sessions" className="flex flex-col gap-2">
        <h3 className="m-0 text-[15px] font-semibold">Web sessions and CLI tokens</h3>
        <QueryState query={sessions} what="Sessions" endpoint="GET /admin/sessions">
          {() => (
            <table className="w-full border-collapse text-ui">
              <thead>
                <tr className="border-b border-line text-sm text-text-3">
                  {['User', 'Kind', 'Device', 'IP', 'Last seen', 'Expires', ''].map((h) => (
                    <th key={h} className="py-2.5 pr-3 font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(sessions.data?.items ?? []).map((s) => (
                  <tr key={s.id} className="border-b border-line">
                    <td className="py-2.5 pr-3">{s.user}</td>
                    <td className="pr-3 uppercase text-text-2">{s.kind}</td>
                    <td className="pr-3">{s.device ?? s.user_agent?.slice(0, 40) ?? '—'}</td>
                    <td className="pr-3 font-mono text-[12.5px]">{s.ip ?? '—'}</td>
                    <td className="pr-3">{relative(s.last_seen_at)}</td>
                    <td className="pr-3">{relative(s.expires_at)}</td>
                    <td className="text-right">
                      <Button
                        size="xs"
                        variant="danger"
                        onClick={() => void revoke('session', s.id, `${s.user} · ${s.kind}`)}
                      >
                        Revoke
                      </Button>
                    </td>
                  </tr>
                ))}
                {(sessions.data?.items ?? []).length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-3 text-meta text-text-3">
                      No server-side session (development mode signs requests in without one).
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </QueryState>
      </section>
      <section aria-label="API tokens" className="flex flex-col gap-2">
        <h3 className="m-0 text-[15px] font-semibold">Personal API tokens</h3>
        <QueryState query={tokens} what="API tokens" endpoint="GET /admin/tokens">
          {() => (
            <table className="w-full border-collapse text-ui">
              <thead>
                <tr className="border-b border-line text-sm text-text-3">
                  {['User', 'Name', 'Scopes', 'Last used', 'Expires', ''].map((h) => (
                    <th key={h} className="py-2.5 pr-3 font-normal">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(tokens.data?.items ?? []).map((t) => (
                  <tr key={t.id} className="border-b border-line">
                    <td className="py-2.5 pr-3">{t.user}</td>
                    <td className="pr-3">{t.name}</td>
                    <td className="pr-3 font-mono text-[12.5px]">{t.scopes.join(', ')}</td>
                    <td className="pr-3">{t.last_used_at ? relative(t.last_used_at) : 'never'}</td>
                    <td className="pr-3">{relative(t.expires_at)}</td>
                    <td className="text-right">
                      {t.revoked_at ? (
                        <span className="text-meta text-text-3">revoked</span>
                      ) : (
                        <Button
                          size="xs"
                          variant="danger"
                          onClick={() => void revoke('token', t.id, `${t.user} · ${t.name}`)}
                        >
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
                {(tokens.data?.items ?? []).length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-3 text-meta text-text-3">
                      No API token.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </QueryState>
      </section>
    </div>
  )
}

// ------------------------------------------------------------------ Audit log

export function AuditTab({
  verify,
  onVerified,
}: {
  verify: AuditVerify | null
  onVerified: (v: AuditVerify) => void
}) {
  const canSettings = useCan('users:manage')
  const canVerify = useCan('audit:export')
  const q = useQuery({
    queryKey: qk.admin.auditSettings,
    queryFn: () => unwrap(api.GET('/admin/audit-settings')),
    enabled: canSettings,
  })
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<{
    retention_days: number
    checkpoint_minutes: number
    forward: Record<string, unknown>
  } | null>(null)
  const s = draft ?? q.data
  const fwd = (s?.forward ?? {}) as {
    syslog?: { enabled?: boolean; url?: string; format?: string; status?: ForwardStatus }
    https?: { enabled?: boolean; url?: string; token_ref?: string; status?: ForwardStatus }
  }
  const runVerify = async () => {
    setBusy(true)
    try {
      const r = await stepUp.run(() => unwrap(api.POST('/admin/audit:verify', { body: {} })), {
        what: 'Verifying the audit chain',
        detail: 'Reads every event and checkpoint',
      })
      onVerified(r)
      if (r.ok) toast.ok(`Chain verified: ${r.checked} events, ${r.checkpoints_verified} signed checkpoints`)
      else
        toast.error(
          new Error(`first bad event #${r.first_bad_seq ?? '?'}${r.detail ? `: ${r.detail}` : ''}`),
          'The audit chain does not verify',
        )
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  const setFwd = (k: 'syslog' | 'https', patch: Record<string, unknown>) => {
    if (s) setDraft({ ...s, forward: { ...fwd, [k]: { ...(fwd[k] ?? {}), ...patch } } })
  }
  return (
    <div className="flex flex-col gap-7">
      <section
        aria-label="Chain"
        className="flex flex-wrap items-center gap-4 rounded-card border border-line bg-panel px-5 py-4"
      >
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="font-medium">Tamper-evident chain</span>
          <span className="text-meta text-text-2">
            hash = sha256(prev_hash ‖ canonical_json(event)); a signed checkpoint every{' '}
            {s?.checkpoint_minutes ?? 60} minutes. The app's database role can only insert and read.
          </span>
          {verify && (
            <span className={`text-meta ${verify.ok ? 'text-ok' : 'text-err'}`}>
              {verify.ok
                ? `Verified ${verify.checked} events and ${verify.checkpoints_verified} checkpoints, no gap`
                : `Broken at event #${verify.first_bad_seq ?? '?'} ${verify.detail ?? ''}`}
            </span>
          )}
        </div>
        {canVerify && (
          <Button busy={busy} onClick={() => void runVerify()}>
            Verify the chain
          </Button>
        )}
        <Link
          to="/history"
          className="flex h-8 items-center rounded-btn border border-line-2 px-3.5 no-underline hover:bg-raised"
        >
          Search the log
        </Link>
      </section>
      {canSettings && (
        <QueryState query={q} what="Audit settings" endpoint="GET /admin/audit-settings">
          {() =>
            !s ? null : (
              <div className="flex flex-col gap-5">
                {draft && q.data && (
                  <PendingChange
                    title="Audit retention and forwarding"
                    before={q.data}
                    after={draft}
                    onDiscard={() => setDraft(null)}
                    onSave={async () => {
                      await unwrap(api.PUT('/admin/audit-settings', { body: draft }))
                      await qc.invalidateQueries({ queryKey: qk.admin.auditSettings })
                      setDraft(null)
                    }}
                  />
                )}
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field
                    label="Retention (days)"
                    hint="Minimum 90, independent of mission data"
                    error={s.retention_days < 90 ? 'At least 90 days' : null}
                  >
                    {(id, d) => (
                      <Input
                        id={id}
                        aria-describedby={d}
                        type="number"
                        min={90}
                        value={s.retention_days}
                        onChange={(e) => setDraft({ ...s, retention_days: Number(e.target.value) })}
                      />
                    )}
                  </Field>
                  <Field label="Checkpoint every (minutes)">
                    {(id) => (
                      <Input
                        id={id}
                        type="number"
                        value={s.checkpoint_minutes}
                        onChange={(e) => setDraft({ ...s, checkpoint_minutes: Number(e.target.value) })}
                      />
                    )}
                  </Field>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="flex flex-col gap-2.5 rounded-panel border border-line px-4 py-3.5">
                    <Checkbox
                      checked={!!fwd.syslog?.enabled}
                      onChange={(v) => setFwd('syslog', { enabled: v })}
                      label="Forward as syslog (RFC 5424 over TLS)"
                    />
                    <Input
                      aria-label="Syslog URL"
                      value={fwd.syslog?.url ?? ''}
                      onChange={(e) => setFwd('syslog', { url: e.target.value })}
                      className="font-mono text-sm"
                      placeholder="tls://syslog.example.internal:6514"
                    />
                    <ForwardState status={fwd.syslog?.status} enabled={!!fwd.syslog?.enabled} />
                  </div>
                  <div className="flex flex-col gap-2.5 rounded-panel border border-line px-4 py-3.5">
                    <Checkbox
                      checked={!!fwd.https?.enabled}
                      onChange={(v) => setFwd('https', { enabled: v })}
                      label="Forward as JSON over HTTPS"
                    />
                    <Input
                      aria-label="HTTPS URL"
                      value={fwd.https?.url ?? ''}
                      onChange={(e) => setFwd('https', { url: e.target.value })}
                      className="font-mono text-sm"
                      placeholder="https://logs.example.internal/z"
                    />
                    <Input
                      aria-label="Bearer token reference"
                      value={fwd.https?.token_ref ?? ''}
                      onChange={(e) => setFwd('https', { token_ref: e.target.value || undefined })}
                      className="font-mono text-sm"
                      placeholder="env:Z_AUDIT_TOKEN (a secret reference, never the value)"
                    />
                    <ForwardState status={fwd.https?.status} enabled={!!fwd.https?.enabled} />
                  </div>
                </div>
              </div>
            )
          }
        </QueryState>
      )}
    </div>
  )
}

type ForwardStatus = {
  last_seq?: number
  last_sent_at?: string | null
  last_error?: string | null
  last_error_at?: string | null
}

/** Delivery state of one forwarding target, reported by the audit worker (at-least-once, in order). */
function ForwardState({ status, enabled }: { status?: ForwardStatus; enabled: boolean }) {
  if (!enabled) return null
  if (!status) return <span className="text-meta text-text-2">Waiting for the first delivery</span>
  if (status.last_error)
    return (
      <span className="text-meta text-err">
        Failing: {status.last_error}. Retrying from event #{(status.last_seq ?? 0) + 1}
      </span>
    )
  return (
    <span className="text-meta text-ok">
      Delivered up to event #{status.last_seq ?? 0}
      {status.last_sent_at ? `, last sent ${new Date(status.last_sent_at).toLocaleString()}` : ''}
    </span>
  )
}

// ------------------------------------------------------------------ Platform (guardrails summary, channels, health)

type ChannelField = { key: string; label: string; placeholder: string; required?: boolean; hint?: string }
const REF = 'A secret reference (env:, file:, vault:, kubernetes:), never the value'
/** What each channel kind needs (server: channels.validate_config). Secrets are references only. */
const CHANNEL_FIELDS: Record<'teams' | 'slack' | 'mattermost' | 'email', ChannelField[]> = {
  teams: [
    {
      key: 'app_id',
      label: 'Bot app id',
      placeholder: '00000000-0000-0000-0000-000000000000',
      required: true,
    },
    {
      key: 'app_password',
      label: 'Bot app password',
      placeholder: 'vault:kv/z/teams#password',
      required: true,
      hint: REF,
    },
  ],
  slack: [
    {
      key: 'signing_secret',
      label: 'Signing secret',
      placeholder: 'env:SLACK_SIGNING_SECRET',
      required: true,
      hint: REF,
    },
    { key: 'bot_token', label: 'Bot token (thread replies)', placeholder: 'env:SLACK_BOT_TOKEN', hint: REF },
  ],
  mattermost: [
    {
      key: 'token',
      label: 'Slash command token',
      placeholder: 'env:MATTERMOST_TOKEN',
      required: true,
      hint: REF,
    },
    { key: 'server_url', label: 'Server URL', placeholder: 'https://chat.example.internal' },
  ],
  email: [
    {
      key: 'secret',
      label: 'Mail relay HMAC secret',
      placeholder: 'env:Z_MAIL_RELAY_SECRET',
      required: true,
      hint: REF,
    },
  ],
}

function ChannelDialog({ onClose }: { onClose: () => void }) {
  const agents = useAgents()
  const teams = useTeams()
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [kind, setKind] = useState<'teams' | 'slack' | 'mattermost' | 'email'>('mattermost')
  const [agent, setAgent] = useState('orchestrator')
  const [allowed, setAllowed] = useState<string[]>([])
  const [config, setConfig] = useState<Record<string, string>>({})
  const [contentUpTo, setContentUpTo] = useState('C1')
  const [busy, setBusy] = useState(false)
  const fields = CHANNEL_FIELDS[kind]
  const missing = fields.filter((f) => f.required && !config[f.key]?.trim())
  const save = async () => {
    setBusy(true)
    try {
      const values = Object.fromEntries(
        fields.filter((f) => config[f.key]?.trim()).map((f) => [f.key, config[f.key]?.trim() ?? '']),
      )
      await stepUp.run(
        () =>
          unwrap(
            api.POST('/channels', {
              body: {
                kind,
                name: titleCase(kind),
                default_agent: agent,
                allowed_teams: allowed,
                config: { ...values, content_up_to: contentUpTo },
                enabled: true,
              },
            }),
          ),
        { what: 'Adding a channel', detail: `${kind} · default agent ${agent}` },
      )
      await qc.invalidateQueries({ queryKey: qk.channels })
      toast.ok('Channel added')
      onClose()
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'Channel not added')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a channel"
      eyebrow="People mention @Z or an agent there; destructive approvals always come back to the Z app"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={missing.length > 0} onClick={() => void save()}>
            Add channel
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Kind">
          {(id) => (
            <Select
              id={id}
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as typeof kind)
                setConfig({})
              }}
            >
              <option value="teams">Microsoft Teams</option>
              <option value="slack">Slack</option>
              <option value="mattermost">Mattermost</option>
              <option value="email">Email</option>
            </Select>
          )}
        </Field>
        <Field label="Default agent">
          {(id) => (
            <Select id={id} value={agent} onChange={(e) => setAgent(e.target.value)}>
              {(agents.data?.items ?? []).map((a) => (
                <option key={a.id} value={a.name}>
                  {a.display_name} (@{a.handle})
                </option>
              ))}
            </Select>
          )}
        </Field>
        {fields.map((f) => (
          <Field key={`${kind}-${f.key}`} label={f.label} hint={f.hint}>
            {(id, d) => (
              <Input
                id={id}
                aria-describedby={d}
                value={config[f.key] ?? ''}
                placeholder={f.placeholder}
                onChange={(e) => setConfig({ ...config, [f.key]: e.target.value })}
                className="font-mono text-sm"
              />
            )}
          </Field>
        ))}
        <Field
          label="Content posted up to"
          hint="Follow-ups carry summaries and questions only for missions up to this data class; above it they link to Z"
        >
          {(id, d) => (
            <Select
              id={id}
              aria-describedby={d}
              value={contentUpTo}
              onChange={(e) => setContentUpTo(e.target.value)}
            >
              {['C0', 'C1', 'C2', 'C3'].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <fieldset className="m-0 flex flex-wrap gap-4 border-none p-0 sm:col-span-2">
          <legend className="mb-2 text-meta text-text-2">Allowed teams</legend>
          {(teams.data ?? []).map((t) => (
            <Checkbox
              key={t.id}
              checked={allowed.includes(t.name)}
              onChange={(v) => setAllowed(v ? [...allowed, t.name] : allowed.filter((x) => x !== t.name))}
              label={t.name}
            />
          ))}
        </fieldset>
      </div>
    </Dialog>
  )
}

export function PlatformTab() {
  const canChannels = useCan('connectors:manage')
  const guardrails = useGuardrails()
  const channels = useChannels()
  const [adding, setAdding] = useState(false)
  const health = useQuery({
    queryKey: ['platform-health'],
    retry: false,
    queryFn: async () => {
      const res = await fetch('/healthz', {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
      })
      if (!res.ok) throw new Error(String(res.status))
      return (await res.json()) as {
        status?: string
        version?: string
        checks?: Record<string, { ok: boolean; detail?: string }>
      }
    },
  })
  return (
    <div className="flex flex-col gap-8">
      <section aria-label="Guardrails" className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between">
          <h3 className="m-0 text-[15px] font-semibold">Guardrails</h3>
          <Link to="/settings" search={{ section: 'guardrails' }} className="text-meta text-text-2">
            Edit in Settings →
          </Link>
        </div>
        <QueryState query={guardrails} what="Guardrails" endpoint="GET /settings/guardrails" compact>
          {() => (
            <ul className="m-0 grid list-none gap-x-8 p-0 sm:grid-cols-2">
              {Object.entries(guardrails.data ?? {}).map(([k, g]) => (
                <li
                  key={k}
                  className="flex items-center justify-between gap-3 border-b border-line py-2.5 text-ui"
                >
                  <span>{g.description ?? k}</span>
                  <span className="flex items-center gap-2 font-mono text-[12.5px] text-text-2">
                    {JSON.stringify(g.value)}
                    {g.locked && <span className="font-sans text-sm text-text-3">locked</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
        <p className="m-0 text-meta text-text-3">
          Budgets, providers and the routing policy live in <Link to="/inference">Inference and usage</Link>.
        </p>
      </section>
      <section aria-label="Channels" className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h3 className="m-0 text-[15px] font-semibold">Channels</h3>
          {canChannels && !isNotServed(channels.error) && (
            <Button size="xs" icon="plus" onClick={() => setAdding(true)}>
              Add a channel
            </Button>
          )}
        </div>
        <QueryState query={channels} what="Channels" endpoint="GET /channels">
          {() => (
            <ul className="m-0 list-none p-0">
              {(channels.data ?? []).map((c) => (
                <li key={c.id} className="flex items-center gap-4 border-b border-line py-2.5 text-ui">
                  <span className="w-28 font-medium">{c.name ?? titleCase(c.kind)}</span>
                  <span className="flex-1 text-text-2">
                    default agent {c.default_agent} · teams {c.allowed_teams.join(', ') || 'all'} · identity{' '}
                    {c.identity_mapping ?? 'sso_email'}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <Dot tone={c.enabled ? 'ok' : 'idle'} />
                    {c.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </li>
              ))}
              {(channels.data ?? []).length === 0 && (
                <li className="py-2.5 text-meta text-text-3">
                  No channel. Teams, Slack, Mattermost and inbound email can start missions and carry approval
                  cards.
                </li>
              )}
            </ul>
          )}
        </QueryState>
      </section>
      <section aria-label="Version and health" className="flex flex-col gap-2">
        <h3 className="m-0 text-[15px] font-semibold">Version and health</h3>
        {health.data ? (
          <DefList
            className="text-ui"
            rows={[
              ['Status', health.data.status ?? '—'],
              [
                'Version',
                <span key="v" className="font-mono">
                  {health.data.version ?? '—'}
                </span>,
              ],
              ...Object.entries(health.data.checks ?? {}).map(
                ([k, v]) =>
                  [
                    k,
                    <span key={k} className={v.ok ? 'text-ok' : 'text-err'}>
                      {v.ok ? 'ok' : (v.detail ?? 'failing')}
                    </span>,
                  ] as [string, React.ReactNode],
              ),
            ]}
          />
        ) : (
          <NotServed what="The health summary" endpoint="GET /healthz through the UI origin" compact />
        )}
        <p className="m-0 text-meta text-text-3">
          Maintenance mode and retention per data class are set in config/z.yaml; they are not exposed by the
          API yet.
        </p>
      </section>
      {adding && <ChannelDialog onClose={() => setAdding(false)} />}
    </div>
  )
}
