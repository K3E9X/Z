import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk } from '@/api/queries'
import type { NotificationSettings, NotificationTarget } from '@/api/types'
import { Dialog } from '@/components/Dialog'
import { QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Checkbox, Field, Input, Select } from '@/components/ui'
import { relative } from '@/lib/format'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'

type Kind = NotificationTarget['kind']
type Event = NotificationTarget['events'][number]

const KINDS: { id: Kind; label: string; hint: string }[] = [
  { id: 'slack', label: 'Slack', hint: 'Incoming webhook URL' },
  { id: 'mattermost', label: 'Mattermost', hint: 'Incoming webhook URL' },
  { id: 'teams', label: 'Microsoft Teams', hint: 'Workflows webhook URL (Adaptive Card)' },
  { id: 'webhook', label: 'Webhook', hint: 'Any HTTPS endpoint; JSON signed with HMAC-SHA256' },
  { id: 'email', label: 'Email', hint: 'Sent through the SMTP server below' },
]
export const EVENTS: { id: Event; label: string }[] = [
  { id: 'approval_requested', label: 'Approval needed' },
  { id: 'question_asked', label: 'Agent question' },
  { id: 'needs_attention', label: 'Job needs attention' },
  { id: 'mission_succeeded', label: 'Mission succeeded' },
  { id: 'mission_failed', label: 'Mission ended without succeeding' },
  { id: 'budget_alert', label: 'Budget alert' },
  { id: 'connector_unhealthy', label: 'Connector unreachable' },
  { id: 'breakglass_used', label: 'Break-glass sign-in' },
]
const REF_HINT = 'A secret reference (env:, file:, vault:, kubernetes:): the URL carries its own token'

function strip(t: NotificationTarget): NotificationTarget {
  const { status: _status, ...rest } = t
  return rest
}

function TargetDialog({
  initial,
  taken,
  onClose,
  onSave,
}: {
  initial: NotificationTarget | null
  taken: string[]
  onClose: () => void
  onSave: (t: NotificationTarget) => Promise<void>
}) {
  const [t, setT] = useState<NotificationTarget>(
    initial ?? { id: '', kind: 'slack', events: ['approval_requested'], enabled: true, content_up_to: 'C1' },
  )
  const [busy, setBusy] = useState(false)
  const idError = !/^[a-z0-9][a-z0-9-]{0,39}$/.test(t.id)
    ? 'Lowercase letters, digits and -'
    : !initial && taken.includes(t.id)
      ? 'Already used'
      : null
  const email = t.kind === 'email'
  const invalid =
    !!idError || t.events.length === 0 || (email ? !(t.to ?? []).length : !t.url_ref?.trim())
  return (
    <Dialog
      open
      onClose={onClose}
      title={initial ? `Edit ${initial.name ?? initial.id}` : 'Add a notification target'}
      eyebrow="Notifications link to Z; nothing is ever approved from a notification"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={invalid}
            onClick={() => {
              setBusy(true)
              void onSave(t).finally(() => setBusy(false))
            }}
          >
            Save target
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Id" error={t.id ? idError : null}>
          {(id) => (
            <Input
              id={id}
              value={t.id}
              disabled={!!initial}
              onChange={(e) => setT({ ...t, id: e.target.value })}
              className="font-mono text-sm"
              placeholder="secops-slack"
            />
          )}
        </Field>
        <Field label="Name">
          {(id) => <Input id={id} value={t.name ?? ''} onChange={(e) => setT({ ...t, name: e.target.value })} />}
        </Field>
        <Field label="Kind" hint={KINDS.find((k) => k.id === t.kind)?.hint}>
          {(id, d) => (
            <Select
              id={id}
              aria-describedby={d}
              value={t.kind}
              onChange={(e) => setT({ ...t, kind: e.target.value as Kind })}
            >
              {KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Content sent up to" hint="Above this data class the message only links to Z">
          {(id, d) => (
            <Select
              id={id}
              aria-describedby={d}
              value={t.content_up_to ?? 'C1'}
              onChange={(e) => setT({ ...t, content_up_to: e.target.value as NotificationTarget['content_up_to'] })}
            >
              {['C0', 'C1', 'C2', 'C3'].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {email ? (
          <Field label="Recipients" hint="Comma-separated addresses">
            {(id, d) => (
              <Input
                id={id}
                aria-describedby={d}
                value={(t.to ?? []).join(', ')}
                onChange={(e) =>
                  setT({
                    ...t,
                    to: e.target.value
                      .split(',')
                      .map((a) => a.trim())
                      .filter(Boolean),
                  })
                }
              />
            )}
          </Field>
        ) : (
          <Field label="Webhook URL reference" hint={REF_HINT}>
            {(id, d) => (
              <Input
                id={id}
                aria-describedby={d}
                value={t.url_ref ?? ''}
                onChange={(e) => setT({ ...t, url_ref: e.target.value })}
                className="font-mono text-sm"
                placeholder="env:Z_SLACK_SECOPS_WEBHOOK"
              />
            )}
          </Field>
        )}
        {t.kind === 'webhook' && (
          <Field label="Signing key reference" hint="X-Z-Signature: sha256=HMAC(key, timestamp.body)">
            {(id, d) => (
              <Input
                id={id}
                aria-describedby={d}
                value={t.secret_ref ?? ''}
                onChange={(e) => setT({ ...t, secret_ref: e.target.value || undefined })}
                className="font-mono text-sm"
                placeholder="vault:secret/z/notify#key"
              />
            )}
          </Field>
        )}
        <fieldset className="m-0 flex flex-col gap-2 border-none p-0 sm:col-span-2">
          <legend className="mb-2 text-meta text-text-2">Events</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {EVENTS.map((ev) => (
              <Checkbox
                key={ev.id}
                checked={t.events.includes(ev.id)}
                onChange={(v) =>
                  setT({ ...t, events: v ? [...t.events, ev.id] : t.events.filter((x) => x !== ev.id) })
                }
                label={ev.label}
              />
            ))}
          </div>
        </fieldset>
      </div>
    </Dialog>
  )
}

export function NotificationsTab() {
  const q = useQuery({
    queryKey: qk.admin.notifications,
    queryFn: () => unwrap(api.GET('/admin/notifications')),
  })
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [editing, setEditing] = useState<NotificationTarget | 'new' | null>(null)
  const [smtp, setSmtp] = useState<NotificationSettings['smtp'] | undefined>(undefined)
  const [testing, setTesting] = useState<string | null>(null)

  const save = async (next: NotificationSettings, what: string) => {
    try {
      await stepUp.run(
        () =>
          unwrap(
            api.PUT('/admin/notifications', {
              body: { targets: next.targets.map(strip), smtp: next.smtp ?? null },
            }),
          ),
        { what, detail: 'Notification targets' },
      )
      await qc.invalidateQueries({ queryKey: qk.admin.notifications })
      toast.ok('Notifications saved')
      return true
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'Not saved')
      return false
    }
  }
  const test = async (id: string) => {
    setTesting(id)
    try {
      await unwrap(api.POST('/admin/notifications:test', { body: { target_id: id } }))
      toast.ok('Test notification sent')
    } catch (e) {
      toast.error(e, 'The test notification failed')
    } finally {
      setTesting(null)
      await qc.invalidateQueries({ queryKey: qk.admin.notifications })
    }
  }
  return (
    <QueryState query={q} what="Notifications" endpoint="GET /admin/notifications">
      {() => {
        const data = q.data
        if (!data) return null
        const mail = smtp === undefined ? data.smtp : smtp
        return (
          <div className="flex flex-col gap-7">
            <section aria-labelledby="n-targets" className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <h2 id="n-targets" className="m-0 flex-1 text-[17px] font-semibold">
                  Targets
                </h2>
                <Button variant="primary" onClick={() => setEditing('new')}>
                  Add target
                </Button>
              </div>
              <p className="m-0 text-text-2">
                Approvals, questions, jobs needing attention, mission outcomes, budget alerts, unreachable connectors
                and break-glass sign-ins, delivered at least once with retries. Links open Z; approving always
                happens in Z.
              </p>
              {data.targets.length === 0 && (
                <p className="m-0 rounded-panel border border-dashed border-line-2 px-4 py-6 text-center text-text-3">
                  No target yet. Add a Slack, Mattermost or Teams webhook, a signed webhook or an email list.
                </p>
              )}
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {data.targets.map((t) => (
                  <li
                    key={t.id}
                    className="flex flex-wrap items-center gap-3 rounded-panel border border-line bg-panel px-4 py-3"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="font-medium">
                        {t.name || t.id}{' '}
                        <span className="text-meta text-text-3">
                          {KINDS.find((k) => k.id === t.kind)?.label} · up to {t.content_up_to ?? 'C1'}
                          {t.enabled === false ? ' · disabled' : ''}
                        </span>
                      </span>
                      <span className="text-sm text-text-2">
                        {t.events.map((e) => EVENTS.find((x) => x.id === e)?.label ?? e).join(' · ')}
                      </span>
                      {t.status?.last_error ? (
                        <span className="text-meta text-err">Last delivery failed: {t.status.last_error}</span>
                      ) : t.status?.last_sent_at ? (
                        <span className="text-meta text-ok">Last delivered {relative(t.status.last_sent_at)}</span>
                      ) : null}
                    </div>
                    <Button busy={testing === t.id} onClick={() => void test(t.id)}>
                      Send test
                    </Button>
                    <Button onClick={() => setEditing(t)}>Edit</Button>
                    <Button
                      onClick={() =>
                        void save(
                          { ...data, targets: data.targets.filter((x) => x.id !== t.id) },
                          `Removing ${t.name || t.id}`,
                        )
                      }
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
            <section aria-labelledby="n-smtp" className="flex flex-col gap-3">
              <h2 id="n-smtp" className="m-0 text-[17px] font-semibold">
                Email (SMTP)
              </h2>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="Host">
                  {(id) => (
                    <Input
                      id={id}
                      value={mail?.host ?? ''}
                      onChange={(e) => setSmtp({ from: '', ...mail, host: e.target.value })}
                      placeholder="smtp.example.internal"
                    />
                  )}
                </Field>
                <Field label="Port" hint="587 STARTTLS, 465 TLS">
                  {(id, d) => (
                    <Input
                      id={id}
                      aria-describedby={d}
                      type="number"
                      value={mail?.port ?? 587}
                      onChange={(e) => setSmtp({ host: '', from: '', ...mail, port: Number(e.target.value) })}
                    />
                  )}
                </Field>
                <Field label="From">
                  {(id) => (
                    <Input
                      id={id}
                      value={mail?.from ?? ''}
                      onChange={(e) => setSmtp({ host: '', ...mail, from: e.target.value })}
                      placeholder="z@example.internal"
                    />
                  )}
                </Field>
                <Field label="Username">
                  {(id) => (
                    <Input
                      id={id}
                      value={mail?.username ?? ''}
                      onChange={(e) =>
                        setSmtp({ host: '', from: '', ...mail, username: e.target.value || undefined })
                      }
                    />
                  )}
                </Field>
                <Field label="Password reference" hint="env:, file:, vault:, kubernetes:">
                  {(id, d) => (
                    <Input
                      id={id}
                      aria-describedby={d}
                      value={mail?.password_ref ?? ''}
                      onChange={(e) =>
                        setSmtp({ host: '', from: '', ...mail, password_ref: e.target.value || undefined })
                      }
                      className="font-mono text-sm"
                    />
                  )}
                </Field>
              </div>
              {smtp !== undefined && (
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    onClick={() =>
                      void save({ ...data, smtp: smtp?.host ? smtp : null }, 'Changing the SMTP server').then(
                        (ok) => ok && setSmtp(undefined),
                      )
                    }
                  >
                    Save SMTP
                  </Button>
                  <Button onClick={() => setSmtp(undefined)}>Discard</Button>
                </div>
              )}
            </section>
            {editing && (
              <TargetDialog
                initial={editing === 'new' ? null : editing}
                taken={data.targets.map((t) => t.id)}
                onClose={() => setEditing(null)}
                onSave={async (t) => {
                  const targets =
                    editing === 'new'
                      ? [...data.targets, t]
                      : data.targets.map((x) => (x.id === t.id ? t : x))
                  if (await save({ ...data, targets }, editing === 'new' ? 'Adding a target' : 'Editing a target'))
                    setEditing(null)
                }}
              />
            )}
          </div>
        )
      }}
    </QueryState>
  )
}
