import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useMissions, usePlaybooks, useReview, useTasks, useTriggers } from '@/api/queries'
import type { Mission, Task, Trigger } from '@/api/types'
import { useAgentIndex, type AgentIndex } from '@/components/Avatar'
import { Dialog } from '@/components/Dialog'
import { reviewItemId } from '@/components/DecisionRow'
import { NotServed } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Field, Input, PageTitle, ProgressBar, Select } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { describeCron } from '@/lib/cron'
import { clock, isToday, relative, shortAge, titleCase } from '@/lib/format'
import { missionAgent, playbookTitle, progress } from '@/lib/mission'
import { missionStatus } from '@/lib/status'

const SOURCE_LABEL: Record<string, string> = {
  jira: 'Jira',
  gitlab: 'GitLab',
  webhook: 'Webhook',
  email: 'Email',
  chat: 'Chat',
  schedule: 'Schedule',
  manual: 'Manual',
}

function triggerLabel(t: Trigger): string {
  const f = t.filter
  if (t.kind === 'schedule')
    return `${t.name ?? playbookTitle(t.playbook)} · ${describeCron(typeof t.config.cron === 'string' ? t.config.cron : null)}`
  if (t.kind === 'jira') return `Jira · ${typeof f.label === 'string' ? `label ${f.label}` : 'issues'}`
  if (t.kind === 'gitlab')
    return `GitLab · ${typeof f.assignee === 'string' ? `issues assigned to @${f.assignee}` : typeof f.label === 'string' ? `label ${f.label}` : 'events'}`
  return `${t.name ?? titleCase(t.kind)} · ${t.kind}`
}

function Column({
  title,
  count,
  tone,
  children,
}: {
  title: string
  count: number | null
  tone?: 'wait'
  children: ReactNode
}) {
  return (
    <section aria-label={title} className="flex min-w-[240px] flex-1 flex-col gap-2.5">
      <h2 className="m-0 flex items-baseline gap-2 border-b border-line pb-2.5 text-ui font-medium">
        {title}
        {count !== null && (
          <span className={tone === 'wait' && count > 0 ? 'text-wait' : 'text-text-3'}>{count}</span>
        )}
      </h2>
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  )
}

function Card({
  children,
  to,
  params,
  search,
  muted,
}: {
  children: ReactNode
  to?: string
  params?: Record<string, string>
  search?: Record<string, string>
  muted?: boolean
}) {
  const cls = `flex flex-col gap-1.5 rounded-panel border border-line bg-panel px-3.5 py-3 no-underline ${muted ? 'text-text-2' : ''}`
  return to ? (
    <Link to={to} params={params} search={search} className={`${cls} hover:border-line-2`}>
      {children}
    </Link>
  ) : (
    <div className={cls}>{children}</div>
  )
}

function TaskCard({ t, agents }: { t: Task; agents: AgentIndex }) {
  const qc = useQueryClient()
  const toast = useToast()
  const canWrite = useCan('tasks:write')
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (action: 'start' | 'dismiss') => {
    setBusy(action)
    try {
      await unwrap(
        api.PATCH('/tasks/{id}', {
          params: { path: { id: t.id } },
          body: {
            action,
            ...(t.suggested_playbook ? { playbook: t.suggested_playbook } : {}),
            ...(t.suggested_agent ? { agent: t.suggested_agent } : {}),
          },
        }),
      )
      await qc.invalidateQueries({ queryKey: qk.tasks })
      await qc.invalidateQueries({ queryKey: ['missions'] })
      toast.ok(action === 'start' ? 'Mission started from the task' : 'Task dismissed')
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <Card>
      <span className="font-mono text-sm text-text-3">
        {t.external_ref ? `${t.external_ref} · ` : ''}
        {SOURCE_LABEL[t.source] ?? titleCase(t.source)}
      </span>
      <span className="font-medium">{t.title}</span>
      <span className="text-meta text-text-3">
        {t.suggested_agent ? `Suggested: ${agents.label(t.suggested_agent)}` : 'No agent suggested yet'}
        {t.suggested_playbook ? ` · ${playbookTitle(t.suggested_playbook)}` : ''}
      </span>
      {canWrite && t.status === 'incoming' && (
        <div className="flex gap-2 pt-1">
          <Button size="xs" variant="primary" busy={busy === 'start'} onClick={() => void act('start')}>
            Start
          </Button>
          <Button size="xs" busy={busy === 'dismiss'} onClick={() => void act('dismiss')}>
            Dismiss
          </Button>
        </div>
      )}
    </Card>
  )
}

function MissionCard({ m, agents, line }: { m: Mission; agents: AgentIndex; line?: ReactNode }) {
  const p = progress(m)
  const st = missionStatus(m.status)
  return (
    <Card to="/missions/$id" params={{ id: m.id }}>
      <span
        className={`flex items-center gap-1.5 text-meta ${st.tone === 'run' ? 'text-run' : st.tone === 'wait' ? 'text-wait' : st.tone === 'ok' ? 'text-ok' : st.tone === 'err' || st.tone === 'budget' ? 'text-err' : 'text-text-3'}`}
      >
        <Dot tone={st.tone} />
        {line ?? `${agents.label(missionAgent(m))} · ${p.done} of ${p.total}`}
      </span>
      <span className="font-medium">{m.title}</span>
      {m.status === 'running' && (
        <ProgressBar
          value={p.done}
          max={p.total || 1}
          tone="run"
          className="mt-1"
          label={`${p.done} of ${p.total}`}
        />
      )}
    </Card>
  )
}

function AddSource({ open, onClose }: { open: boolean; onClose: () => void }) {
  const playbooks = usePlaybooks()
  const qc = useQueryClient()
  const toast = useToast()
  const [kind, setKind] = useState<'jira' | 'gitlab' | 'webhook' | 'schedule' | 'email'>('jira')
  const [value, setValue] = useState('z')
  const [playbook, setPlaybook] = useState('')
  const [autoStart, setAutoStart] = useState(false)
  const [busy, setBusy] = useState(false)
  const [created, setCreated] = useState<Trigger | null>(null)
  const save = async () => {
    setBusy(true)
    try {
      const t = await unwrap(
        api.POST('/triggers', {
          body: {
            kind,
            name: `${titleCase(kind)} intake`,
            config: kind === 'schedule' ? { cron: value } : {},
            filter: kind === 'jira' ? { label: value } : kind === 'gitlab' ? { assignee: value } : {},
            ...(playbook ? { playbook } : {}),
            auto_start: autoStart,
            enabled: true,
          },
        }),
      )
      await qc.invalidateQueries({ queryKey: qk.triggers })
      if (t.webhook_url || t.secret) setCreated(t)
      else {
        toast.ok('Source added')
        onClose()
      }
    } catch (e) {
      toast.error(e, isNotServed(e) ? 'Triggers are not available on this server yet' : 'Source not added')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a source"
      eyebrow="Work that comes in lands as an incoming task with a suggested agent"
      width={560}
      footer={
        created ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" busy={busy} onClick={() => void save()}>
              Add source
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="flex flex-col gap-3">
          <p className="m-0 text-text-2">
            Point the tool at this URL. The secret is shown once; requests are checked with HMAC.
          </p>
          <Field label="Webhook URL">
            {(id) => (
              <Input id={id} readOnly value={created.webhook_url ?? ''} className="font-mono text-sm" />
            )}
          </Field>
          {created.secret && (
            <Field label="Signing secret (shown once)">
              {(id) => <Input id={id} readOnly value={created.secret ?? ''} className="font-mono text-sm" />}
            </Field>
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Source">
            {(id) => (
              <Select id={id} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="jira">Jira label</option>
                <option value="gitlab">GitLab assignee</option>
                <option value="webhook">Inbound webhook</option>
                <option value="email">Mail to Z</option>
                <option value="schedule">Schedule</option>
              </Select>
            )}
          </Field>
          {(kind === 'jira' || kind === 'gitlab' || kind === 'schedule') && (
            <Field label={kind === 'jira' ? 'Label' : kind === 'gitlab' ? 'Assignee' : 'Cron'}>
              {(id) => (
                <Input
                  id={id}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  className="font-mono"
                />
              )}
            </Field>
          )}
          <Field label="Playbook" className="sm:col-span-2">
            {(id) => (
              <Select id={id} value={playbook} onChange={(e) => setPlaybook(e.target.value)}>
                <option value="">Let Z suggest one per task</option>
                {(playbooks.data?.items ?? []).map((p) => (
                  <option key={p.id} value={p.name}>
                    {playbookTitle(p.name)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <label className="flex items-center gap-2 sm:col-span-2">
            <input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} />
            Start the mission automatically (writes still wait for approval)
          </label>
        </div>
      )}
    </Dialog>
  )
}

/** Tasks board (docs/09 screen 5): intake sources, then Incoming / Scheduled / Running / Needs review / Done. */
export function Tasks() {
  const tasks = useTasks()
  const triggers = useTriggers()
  const running = useMissions({ status: 'queued,running,paused' }, { live: true })
  const waiting = useMissions({ status: 'waiting' }, { live: true })
  const done = useMissions({ status: 'succeeded,failed,cancelled,budget_exceeded,timed_out', limit: 50 })
  const review = useReview('all')
  const agents = useAgentIndex()
  const canStart = useCan('missions:start')
  const canTriggers = useCan('triggers:write')
  const [adding, setAdding] = useState(false)
  const taskList = tasks.data?.items ?? []
  const incoming = taskList.filter((t) => t.status === 'incoming')
  const scheduledTasks = taskList.filter((t) => t.status === 'scheduled')
  const schedules = (triggers.data ?? []).filter((t) => t.kind === 'schedule' && t.enabled)
  const intake = (triggers.data ?? []).filter((t) => t.enabled)
  const finished = (done.data?.items ?? []).filter(
    (m) =>
      isToday(m.ended_at) ||
      (m.ended_at && Date.parse(m.ended_at) > Date.parse(new Date().toDateString()) - 86400_000 * 2),
  )
  const reviewByMission = new Map<string, number>()
  for (const it of review.data?.items ?? []) {
    const mid = it.action?.mission_id ?? it.question?.mission_id
    if (mid) reviewByMission.set(mid, (reviewByMission.get(mid) ?? 0) + 1)
  }
  const tasksNotServed = isNotServed(tasks.error)

  return (
    <main className="flex w-full max-w-[1440px] flex-col gap-7 px-10 pt-11 pb-14 max-md:px-5">
      <PageTitle
        title="Tasks"
        lead="Work comes in from your tools and schedules. Z picks it up, routes it to the right agent and brings it back to you for review."
        right={
          canStart ? (
            <Link
              to="/missions/new"
              className="flex h-[34px] items-center rounded-btn bg-inv-bg px-3.5 font-medium text-inv-text no-underline"
            >
              New mission
            </Link>
          ) : undefined
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-meta text-text-3">Intake</span>
        {isNotServed(triggers.error) ? (
          <NotServed what="Intake sources" endpoint="GET /triggers" compact />
        ) : (
          intake.map((t) => (
            <span
              key={t.id}
              className="flex h-[30px] items-center gap-2 rounded-ctl border border-line-2 px-3 text-ui"
            >
              <Dot tone="ok" />
              {triggerLabel(t)}
            </span>
          ))
        )}
        {!isNotServed(triggers.error) && schedules.length > 1 && (
          <span className="text-meta text-text-3">Schedules · {schedules.length}</span>
        )}
        {canTriggers && !isNotServed(triggers.error) && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="flex h-[30px] items-center rounded-ctl border border-dashed border-line-2 px-3 text-text-2 hover:text-text"
          >
            Add a source
          </button>
        )}
      </div>
      <div className="flex gap-4 overflow-x-auto pb-2">
        <Column title="Incoming" count={tasksNotServed ? null : incoming.length}>
          {tasksNotServed ? (
            <NotServed what="The task inbox" endpoint="GET /tasks" />
          ) : (
            incoming.map((t) => <TaskCard key={t.id} t={t} agents={agents} />)
          )}
          {!tasksNotServed && tasks.data && incoming.length === 0 && (
            <span className="text-meta text-text-3">Nothing new.</span>
          )}
        </Column>
        <Column
          title="Scheduled"
          count={
            tasksNotServed && isNotServed(triggers.error) ? null : scheduledTasks.length + schedules.length
          }
        >
          {schedules.map((t) => (
            <Card key={t.id} muted>
              <span className="font-mono text-sm text-text-3">
                {describeCron(typeof t.config.cron === 'string' ? t.config.cron : null)} · playbook
              </span>
              <span className="font-medium text-text">{playbookTitle(t.playbook) || t.name}</span>
              <span className="text-meta text-text-3">
                {t.last_fired_at ? `last run ${relative(t.last_fired_at)}` : 'not run yet'}
              </span>
            </Card>
          ))}
          {scheduledTasks.map((t) => (
            <TaskCard key={t.id} t={t} agents={agents} />
          ))}
          {isNotServed(triggers.error) && schedules.length === 0 && (
            <span className="text-meta text-text-3">Schedules appear once triggers are served.</span>
          )}
        </Column>
        <Column title="Running" count={running.data?.items.length ?? null}>
          {(running.data?.items ?? []).map((m) => (
            <MissionCard key={m.id} m={m} agents={agents} />
          ))}
        </Column>
        <Column title="Needs review" count={waiting.data?.items.length ?? null} tone="wait">
          {(waiting.data?.items ?? []).map((m) => {
            const n = reviewByMission.get(m.id) ?? 0
            const first = (review.data?.items ?? []).find(
              (i) => (i.action?.mission_id ?? i.question?.mission_id) === m.id,
            )
            return (
              <Card key={m.id} to="/review" search={first ? { item: reviewItemId(first) } : {}}>
                <span className="flex items-center gap-1.5 text-meta text-wait">
                  <Dot tone="wait" />
                  {m.waiting_reason === 'question'
                    ? 'Question'
                    : n > 1
                      ? `Approve ${n} writes`
                      : 'Approve writes'}
                </span>
                <span className="font-medium">{m.title}</span>
                <span className="text-meta text-text-3">
                  {agents.label(missionAgent(m))} ·{' '}
                  {first ? `${shortAge(first.age_s)} ago` : relative(m.started_at)}
                </span>
              </Card>
            )
          })}
        </Column>
        <Column title="Done" count={finished.length}>
          {finished.map((m) => {
            const st = missionStatus(m.status)
            return (
              <MissionCard key={m.id} m={m} agents={agents} line={`${st.label} · ${clock(m.ended_at)}`} />
            )
          })}
        </Column>
      </div>
      {adding && <AddSource open onClose={() => setAdding(false)} />}
    </main>
  )
}
