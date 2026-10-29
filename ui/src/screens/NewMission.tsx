import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useRef, useState } from 'react'
import { api, rawFetch, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { useAgents, useConnectors, usePlaybooks, useTriggers } from '@/api/queries'
import {
  toContractPlan,
  type DataClass,
  type DraftPlan,
  type Mission,
  type PlanStep,
  type Playbook,
} from '@/api/types'
import { Icon } from '@/components/Icon'
import {
  MentionTextarea,
  parseMentions,
  type MentionHandle,
  type MentionOption,
} from '@/components/MentionTextarea'
import { defaultsFor, SchemaForm, validate, type FormValue, type JsonSchema } from '@/components/SchemaForm'
import { stepTitle } from '@/components/PlanChecklist'
import { NotServed } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Checkbox, Chip, Input, Radio, Select } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { describeCron } from '@/lib/cron'
import { bytes, compactNumber } from '@/lib/format'
import { MOD } from '@/lib/hotkeys'
import { playbookTitle } from '@/lib/mission'
import { cronError, SCHEDULE_TZ_NOTE } from '@/lib/triggers'
import { normalizePlan, PlanStepsEditor } from './PlanEditor'

type Mode = 'plan_first' | 'propose' | 'read_only'
type When = 'now' | 'schedule' | 'event'

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60)
}

/** A playbook whose name words appear in the prompt, or whose connectors are all mentioned. */
function matchPlaybook(prompt: string, connectors: string[], playbooks: Playbook[]): Playbook | null {
  const p = prompt.toLowerCase()
  let best: { pb: Playbook; score: number } | null = null
  for (const pb of playbooks) {
    const words = (pb.name ?? '').split('-').filter((w) => w.length > 3)
    let score = words.filter((w) => p.includes(w)).length
    const cons = pb.connectors ?? []
    if (cons.length && cons.every((c) => connectors.includes(c))) score += 2
    if (score >= 2 && (!best || score > best.score)) best = { pb, score }
  }
  return best?.pb ?? null
}

function StepRow({
  i,
  step,
  agentLabel,
  model,
  inputs,
}: {
  i: number
  step: PlanStep
  agentLabel: (s?: string) => string
  model?: string
  inputs?: FormValue
}) {
  const writes = step.writes ?? []
  const connector = writes[0]?.split('.')[0]
  const right = writes.length
    ? { text: `${connector ?? ''} · approved write`, tone: 'text-wait' }
    : step.fanout
      ? {
          text: `${step.fanout.concurrency ? `${step.fanout.concurrency} in parallel` : 'fan-out'} × ${step.model ?? model ?? 'agent model'}`,
          tone: 'text-text-3',
        }
      : { text: `${agentLabel(step.agent)} · read`, tone: 'text-text-3' }
  return (
    <li className="grid grid-cols-[40px_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-line py-3.5">
      <span className="font-mono text-sm text-text-3">{String(i + 1).padStart(2, '0')}</span>
      <span>{stepTitle(step, inputs)}</span>
      <span className={`font-mono text-sm ${right.tone}`}>{right.text}</span>
    </li>
  )
}

export function NewMission() {
  const search = useSearch({ from: '/missions/new' })
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const canStart = useCan('missions:start')
  const canSavePlaybook = useCan('playbooks:write')
  // POST /triggers needs triggers:write: without it Schedule and Event are not offered.
  const canTriggers = useCan('triggers:write')
  const agents = useAgents()
  const connectors = useConnectors()
  const playbooks = usePlaybooks()
  const triggers = useTriggers({ enabled: canTriggers })
  const composer = useRef<MentionHandle>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const [prompt, setPrompt] = useState(() => search.prompt ?? (search.agent ? `@${search.agent} ` : ''))
  const [files, setFiles] = useState<File[]>([])
  const [model, setModel] = useState('')
  const [playbookName, setPlaybookName] = useState<string | null>(search.playbook ?? null)
  const [inputs, setInputs] = useState<FormValue | null>(null)
  const [inputErrors, setInputErrors] = useState<Record<string, string>>({})
  const [draft, setDraft] = useState<DraftPlan | null>(null)
  const [draftState, setDraftState] = useState<'idle' | 'busy' | 'unavailable'>('idle')
  const [plan, setPlan] = useState<PlanStep[] | null>(null)
  const [editing, setEditing] = useState(false)
  const [mode, setMode] = useState<Mode>('propose')
  const [when, setWhen] = useState<When>('now')
  const [cron, setCron] = useState('0 8 * * 1')
  const [eventKind, setEventKind] = useState<'jira' | 'gitlab' | 'webhook'>('jira')
  const [jiraProject, setJiraProject] = useState('')
  const [jiraLabel, setJiraLabel] = useState('z')
  const [gitlabLabel, setGitlabLabel] = useState('z')
  const [everyCall, setEveryCall] = useState(false)
  const [budget, setBudget] = useState(2_000_000)
  const [minutes, setMinutes] = useState(45)
  const [dataClass, setDataClass] = useState<DataClass>('C2')
  const [saveAs, setSaveAs] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [busy, setBusy] = useState(false)

  const pbList = useMemo(() => playbooks.data?.items ?? [], [playbooks.data])
  const playbook = pbList.find((p) => p.name === playbookName || p.id === playbookName) ?? null
  const options = useMemo<MentionOption[]>(
    () => [
      ...(agents.data?.items ?? []).map((a) => ({
        trigger: '@' as const,
        insert: a.handle ?? a.name ?? a.id,
        label: a.display_name ?? a.name ?? a.id,
        hint: a.role ? undefined : undefined,
        avatar: a.avatar ?? null,
      })),
      ...(connectors.data ?? []).map((c) => ({
        trigger: '#' as const,
        insert: c.name,
        label: c.display_name,
        hint: c.review_status === 'approved' ? undefined : 'not reviewed',
      })),
    ],
    [agents.data, connectors.data],
  )
  const mentions = parseMentions(prompt, options)
  const mentionedAgents = mentions.filter((m) => m.trigger === '@')
  const mentionedConnectors = mentions.filter((m) => m.trigger === '#')
  const agentSlugs = mentionedAgents.map(
    (m) => (agents.data?.items ?? []).find((a) => a.handle === m.insert)?.name ?? m.insert,
  )
  const connectorSlugs = mentionedConnectors.map((m) => m.insert)
  const suggested = draft?.suggested_playbook
    ? (pbList.find((p) => p.name === draft.suggested_playbook) ?? null)
    : matchPlaybook(prompt, connectorSlugs, pbList)
  const agentModel = (s?: string) => (agents.data?.items ?? []).find((a) => a.name === s)?.model
  const agentLabel = (s?: string) =>
    (agents.data?.items ?? []).find((a) => a.name === s)?.display_name ?? s ?? 'agent'
  const schema = playbook?.inputs?.schema as JsonSchema | undefined
  const formValue = inputs ?? defaultsFor(schema)
  const steps: PlanStep[] | null = plan ?? (playbook ? (playbook.steps ?? []) : null)
  const writes = [...new Set((steps ?? []).flatMap((s) => s.writes ?? []))]
  const triggersServed = !isNotServed(triggers.error)
  // A schedule or an event never silently becomes "start now": it needs a playbook and a valid source.
  const whenError =
    when === 'now'
      ? null
      : !playbook
        ? 'Schedules and events need a playbook: pick one, or choose Now.'
        : when === 'schedule'
          ? cronError(cron)
          : eventKind === 'jira'
            ? !jiraProject.trim()
              ? 'Name the Jira project to watch.'
              : !jiraLabel.trim()
                ? 'A label is needed, or every issue of the project starts a mission.'
                : null
            : eventKind === 'gitlab'
              ? !gitlabLabel.trim()
                ? 'A label is needed, or every GitLab event starts a mission.'
                : null
              : !everyCall
                ? 'Confirm that every signed call to the webhook starts a mission.'
                : null
  const triggerSource =
    when === 'schedule'
      ? { config: { cron: cron.trim() }, filter: {} }
      : eventKind === 'jira'
        ? { config: { project: jiraProject.trim(), label: jiraLabel.trim() }, filter: {} }
        : eventKind === 'gitlab'
          ? { config: {}, filter: { label: gitlabLabel.trim() } }
          : { config: {}, filter: {} }

  const choosePlaybook = (name: string | null) => {
    setPlaybookName(name)
    setInputs(null)
    setPlan(null)
    setEditing(false)
    const pb = pbList.find((p) => p.name === name)
    if (pb?.budget?.max_tokens) setBudget(pb.budget.max_tokens)
    if (pb?.budget?.timeout_s) setMinutes(Math.round(pb.budget.timeout_s / 60))
    if (pb?.data_class) setDataClass(pb.data_class)
    if (pb?.autonomy === 'read_only') setMode('read_only')
  }

  // a playbook preselected from the URL (Playbooks -> Use) gets its own budget, data class and autonomy, as if picked
  // (adjusted during render once the list has loaded: https://react.dev/learn/you-might-not-need-an-effect)
  const [preselected, setPreselected] = useState<string | null>(null)
  if (search.playbook && pbList.length && preselected !== search.playbook) {
    setPreselected(search.playbook)
    const pb = pbList.find((p) => p.name === search.playbook || p.id === search.playbook)
    if (pb) choosePlaybook(pb.name ?? null)
  }

  const draftPlan = async () => {
    if (playbook) {
      setPlan((playbook.steps ?? []).map((s) => ({ ...s })))
      return
    }
    if (!prompt.trim()) return
    setDraftState('busy')
    try {
      const d = await unwrap(
        api.POST('/missions/draft-plan', {
          body: { prompt, agents: agentSlugs, connectors: connectorSlugs },
        }),
      )
      setDraft(d)
      setPlan(d.plan.steps.map((s) => ({ ...s })))
      if (d.estimated_tokens)
        setBudget(Math.max(budget, Math.ceil((d.estimated_tokens * 1.5) / 100_000) * 100_000))
      setDraftState('idle')
    } catch (e) {
      if (isNotServed(e)) setDraftState('unavailable')
      else {
        setDraftState('idle')
        toast.error(e, 'Could not draft the plan')
      }
    }
  }

  const upload = async (missionId: string) => {
    for (const f of files) {
      const body = new FormData()
      body.append('file', f)
      body.append('name', f.name)
      await rawFetch(`/missions/${encodeURIComponent(missionId)}/files`, { method: 'POST', body })
    }
  }

  const start = async () => {
    if (playbook) {
      const errs = validate(schema, formValue)
      setInputErrors(errs)
      if (Object.keys(errs).length) {
        toast.info('Some inputs are missing or invalid.')
        return
      }
    } else if (!prompt.trim()) {
      composer.current?.focus()
      return
    }
    setBusy(true)
    try {
      const planFirst = mode === 'plan_first'
      const needStage = files.length > 0 || planFirst
      const budgetBody = { max_tokens: budget, timeout_s: minutes * 60 }
      let mission: Mission
      if (when !== 'now') {
        if (whenError || !playbook) {
          toast.info(whenError ?? 'Schedules and events need a playbook.')
          return
        }
        await unwrap(
          api.POST('/triggers', {
            body: {
              kind: when === 'schedule' ? 'schedule' : eventKind,
              name: `${playbookTitle(playbook.name)} · ${when === 'schedule' ? cron : `on ${eventKind}`}`,
              ...triggerSource,
              playbook: playbook.name ?? playbook.id,
              inputs: formValue,
              auto_start: true,
              enabled: true,
            },
          }),
        )
        await qc.invalidateQueries({ queryKey: ['triggers'] })
        toast.ok('Trigger created. Missions will start on their own and their writes still come to Review.')
        void navigate({ to: '/tasks' })
        return
      }
      if (playbook && !plan) {
        mission = await unwrap(
          api.POST('/playbooks/{id}/run', {
            params: { path: { id: playbook.id } },
            body: {
              inputs: formValue,
              data_class: dataClass,
              plan_first: needStage,
              budget: budgetBody,
              ...(prompt.trim() ? { title: prompt.trim().slice(0, 120) } : {}),
            },
          }),
        )
      } else {
        const p =
          steps && steps.length
            ? normalizePlan(model ? steps.map((s) => ({ ...s, model })) : steps)
            : undefined
        mission = await unwrap(
          api.POST('/missions', {
            body: {
              prompt: prompt.trim(),
              ...(playbook ? { playbook: playbook.name, inputs: formValue } : {}),
              agents: agentSlugs,
              connectors: connectorSlugs,
              ...(p ? { plan: toContractPlan(p) } : {}),
              data_class: dataClass,
              autonomy: mode === 'read_only' ? 'read_only' : 'propose',
              plan_first: needStage,
              budget: budgetBody,
              start: !needStage,
            },
          }),
        )
      }
      if (files.length) await upload(mission.id)
      if (files.length && !planFirst) {
        mission = await unwrap(
          api.POST('/missions/{id}/start', { params: { path: { id: mission.id } }, body: {} }),
        )
      }
      if (saveAs && canSavePlaybook) {
        try {
          await unwrap(
            api.POST('/missions/{id}/save-as-playbook', {
              params: { path: { id: mission.id } },
              body: {
                name: slug(saveName || prompt).slice(0, 60) || 'my-playbook',
                description: prompt.trim().slice(0, 300),
              },
            }),
          )
          toast.ok('Saved as a playbook')
        } catch (e) {
          toast.error(
            e,
            isNotServed(e)
              ? 'Save as playbook is not available on this server yet'
              : 'Not saved as a playbook',
          )
        }
      }
      await qc.invalidateQueries({ queryKey: ['missions'] })
      void navigate({ to: '/missions/$id', params: { id: mission.id } })
    } catch (e) {
      toast.error(e, 'The mission did not start')
    } finally {
      setBusy(false)
    }
  }

  if (!canStart) {
    return (
      <main className="mx-auto w-full max-w-[820px] px-10 py-14">
        <h1 className="m-0 font-display-face text-display">What should Z do?</h1>
        <p className="text-text-2">
          Starting missions needs the missions:start permission. Ask an admin to add you to an operator group.
        </p>
      </main>
    )
  }

  return (
    <main className="mx-auto flex w-full max-w-[900px] flex-col gap-9 px-10 pt-12 pb-16 max-md:px-5">
      <h1 className="m-0 font-display-face text-display leading-[1.05]">What should Z do?</h1>

      <section aria-label="Request" className="rounded-card border border-line-2 bg-panel">
        <div className="px-5 pt-4 pb-3">
          <MentionTextarea
            ref={composer}
            label="What should Z do?"
            value={prompt}
            onChange={setPrompt}
            options={options}
            onSubmit={() => void draftPlan()}
            placeholder="For every critical finding opened this week in #defectdojo, check whether it is exploitable, then open one #jira ticket per owning team. Hand the triage to @vuln-analyst."
          />
          {(mentions.length > 0 || suggested || playbook || files.length > 0 || draft?.parsed) && (
            <div className="flex flex-wrap gap-1.5 pt-2">
              {mentionedAgents.map((m) => (
                <Chip key={`a${m.insert}`}>
                  <span className="text-text-3">agent</span> {m.label}
                </Chip>
              ))}
              {mentionedConnectors.map((m) => (
                <Chip key={`c${m.insert}`}>
                  <span className="text-text-3">source</span> {m.label}
                </Chip>
              ))}
              {(draft?.parsed?.sources ?? []).map((s) => (
                <Chip key={`s${s}`}>
                  <span className="text-text-3">source</span> {s}
                </Chip>
              ))}
              {(draft?.parsed?.writes ?? writes).map((w) => (
                <Chip key={`w${w}`}>
                  <span className="text-text-3">writes to</span> {w}
                </Chip>
              ))}
              {files.map((f) => (
                <Chip key={`f${f.name}`}>
                  <Icon name="paperclip" size={11} />
                  {f.name} <span className="text-text-3">{bytes(f.size)}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => setFiles(files.filter((x) => x !== f))}
                    className="text-text-3 hover:text-text"
                  >
                    <Icon name="x" size={11} />
                  </button>
                </Chip>
              ))}
              {playbook ? (
                <Chip tone="run">
                  <span className="opacity-70">playbook</span> {playbookTitle(playbook.name)}
                  <button
                    type="button"
                    aria-label="Do not use the playbook"
                    onClick={() => choosePlaybook(null)}
                  >
                    <Icon name="x" size={11} />
                  </button>
                </Chip>
              ) : (
                suggested && (
                  <button
                    type="button"
                    onClick={() => choosePlaybook(suggested.name ?? null)}
                    className="rounded-[6px]"
                  >
                    <Chip>
                      <span className="text-text-3">matches playbook</span> {playbookTitle(suggested.name)} ·
                      use it
                    </Chip>
                  </button>
                )
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1 border-t border-line px-3 py-2">
          <input
            ref={fileInput}
            type="file"
            multiple
            className="sr-only"
            aria-label="Attach files"
            onChange={(e) => {
              setFiles([...files, ...Array.from(e.target.files ?? [])])
              e.target.value = ''
            }}
          />
          <button
            type="button"
            aria-label="Attach files"
            title="Attach files (they land in the workspace, after the judge scan)"
            onClick={() => fileInput.current?.click()}
            className="flex h-8 w-8 items-center justify-center rounded-btn text-text-2 hover:bg-raised"
          >
            <Icon name="paperclip" size={15} />
          </button>
          <button
            type="button"
            onClick={() => composer.current?.insertTrigger('@')}
            className="flex h-8 items-center gap-1 rounded-btn px-2.5 text-text-2 hover:bg-raised"
          >
            @ Agent
          </button>
          <button
            type="button"
            onClick={() => composer.current?.insertTrigger('#')}
            className="flex h-8 items-center gap-1 rounded-btn px-2.5 text-text-2 hover:bg-raised"
          >
            # Tool
          </button>
          <div className="flex-1" />
          <label className="flex items-center gap-2 text-meta text-text-2">
            Playbook
            <Select
              value={playbook?.name ?? ''}
              onChange={(e) => choosePlaybook(e.target.value || null)}
              className="h-8 w-[200px]"
            >
              <option value="">None (free-form)</option>
              {pbList.map((p) => (
                <option key={p.id} value={p.name}>
                  {playbookTitle(p.name)}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex items-center gap-2 text-meta text-text-2">
            Model
            <Select value={model} onChange={(e) => setModel(e.target.value)} className="h-8 w-[120px]">
              <option value="">Auto</option>
              <option value="z-heavy">z-heavy</option>
              <option value="z-fast">z-fast</option>
            </Select>
          </label>
          <Button
            variant="primary"
            size="sm"
            kbd={`${MOD}↵`}
            busy={draftState === 'busy'}
            onClick={() => void draftPlan()}
            disabled={!prompt.trim() && !playbook}
          >
            {plan ? 'Redraft the plan' : 'Draft the plan'}
          </Button>
        </div>
      </section>

      {playbook && (
        <section aria-labelledby="inputs-title" className="flex flex-col gap-4">
          <div className="flex items-baseline justify-between border-b border-line pb-2.5">
            <h2 id="inputs-title" className="m-0 text-[15px] font-semibold">
              Inputs · {playbookTitle(playbook.name)}
            </h2>
            <span className="font-mono text-sm text-text-3">v{playbook.version}</span>
          </div>
          {playbook.description && <p className="m-0 text-text-2">{playbook.description}</p>}
          <SchemaForm
            schema={schema}
            value={formValue}
            onChange={(v) => {
              setInputs(v)
              if (Object.keys(inputErrors).length) setInputErrors(validate(schema, v))
            }}
            errors={inputErrors}
          />
        </section>
      )}

      {draftState === 'unavailable' && !plan && (
        <div className="flex flex-col gap-3">
          <NotServed what="Plan drafting" endpoint="POST /missions/draft-plan" />
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() =>
                setPlan([
                  {
                    id: 'step-1',
                    title: prompt.split('\n')[0]?.slice(0, 80) ?? 'Step 1',
                    agent: agentSlugs[0] ?? 'orchestrator',
                    prompt,
                    depends_on: [],
                  },
                ])
              }
            >
              Write the plan myself
            </Button>
            <span className="self-center text-meta text-text-3">
              or start now: the Orchestrator drafts the plan as its first step.
            </span>
          </div>
        </div>
      )}

      {steps && steps.length > 0 && (
        <section aria-labelledby="plan-title" className="flex flex-col">
          <div className="flex items-baseline justify-between gap-4 border-b border-line pb-3">
            <div className="flex flex-wrap items-baseline gap-x-4">
              <h2 id="plan-title" className="m-0 text-[15px] font-semibold">
                Proposed plan
              </h2>
              <span className="font-mono text-sm text-text-3">
                {steps.length} {steps.length === 1 ? 'step' : 'steps'}
                {writes.length
                  ? ` · ${writes.length} ${writes.length === 1 ? 'kind of write' : 'kinds of writes'} to approve`
                  : ' · read only'}
                {draft?.estimated_tokens ? ` · ~${compactNumber(draft.estimated_tokens)} tokens` : ''}
              </span>
            </div>
            <button
              type="button"
              className="text-ui text-text-2 hover:text-text"
              onClick={() => {
                if (!plan) setPlan(steps.map((s) => ({ ...s })))
                setEditing((v) => !v)
              }}
            >
              {editing ? 'Done' : 'Edit plan'}
            </button>
          </div>
          {editing && plan ? (
            <div className="pt-4">
              <PlanStepsEditor steps={plan} onChange={setPlan} />
            </div>
          ) : (
            <ol className="m-0 list-none p-0">
              {steps.map((s, i) => (
                <StepRow
                  key={`${s.id ?? ''}${i}`}
                  i={i}
                  step={s}
                  agentLabel={agentLabel}
                  model={model || agentModel(s.agent)}
                  inputs={formValue}
                />
              ))}
            </ol>
          )}
        </section>
      )}

      <section aria-label="Options" className="grid gap-8 sm:grid-cols-3">
        <fieldset className="m-0 flex flex-col gap-2.5 border-none p-0">
          <legend className="mb-2.5 text-meta text-text-2">Autonomy</legend>
          <Radio
            name="mode"
            value="plan_first"
            current={mode}
            onChange={setMode}
            label="Show me the plan before it runs"
          />
          <Radio name="mode" value="propose" current={mode} onChange={setMode} label="Ask me before writes" />
          <Radio name="mode" value="read_only" current={mode} onChange={setMode} label="Read only" />
        </fieldset>
        <fieldset className="m-0 flex flex-col gap-2.5 border-none p-0">
          <legend className="mb-2.5 text-meta text-text-2">When</legend>
          <Radio name="when" value="now" current={when} onChange={setWhen} label="Now" />
          <Radio
            name="when"
            value="schedule"
            current={when}
            onChange={setWhen}
            disabled={!canTriggers || !playbook || !triggersServed}
            label={
              <span className="flex flex-wrap items-center gap-2">
                On a schedule{' '}
                {when === 'schedule' && (
                  <>
                    <Input
                      aria-label="Cron expression"
                      value={cron}
                      onChange={(e) => setCron(e.target.value)}
                      className="h-7 w-[120px] font-mono text-sm"
                    />
                    {!cronError(cron) && (
                      <span className="text-sm text-text-3">
                        {describeCron(cron)}. {SCHEDULE_TZ_NOTE}
                      </span>
                    )}
                  </>
                )}
              </span>
            }
          />
          <Radio
            name="when"
            value="event"
            current={when}
            onChange={setWhen}
            disabled={!canTriggers || !playbook || !triggersServed}
            label={
              <span className="flex flex-wrap items-center gap-2">
                On every new event
                {when === 'event' && (
                  <Select
                    aria-label="Event source"
                    value={eventKind}
                    onChange={(e) => setEventKind(e.target.value as 'jira' | 'gitlab' | 'webhook')}
                    className="h-7 w-[110px] text-sm"
                  >
                    <option value="jira">Jira</option>
                    <option value="gitlab">GitLab</option>
                    <option value="webhook">Webhook</option>
                  </Select>
                )}
                {when === 'event' && eventKind === 'jira' && (
                  <>
                    <Input
                      aria-label="Jira project"
                      placeholder="project"
                      value={jiraProject}
                      onChange={(e) => setJiraProject(e.target.value)}
                      className="h-7 w-[110px] font-mono text-sm"
                    />
                    <Input
                      aria-label="Jira label"
                      value={jiraLabel}
                      onChange={(e) => setJiraLabel(e.target.value)}
                      className="h-7 w-[90px] font-mono text-sm"
                    />
                  </>
                )}
                {when === 'event' && eventKind === 'gitlab' && (
                  <Input
                    aria-label="GitLab label"
                    value={gitlabLabel}
                    onChange={(e) => setGitlabLabel(e.target.value)}
                    className="h-7 w-[110px] font-mono text-sm"
                  />
                )}
              </span>
            }
          />
          {when === 'event' && eventKind === 'webhook' && (
            <Checkbox
              checked={everyCall}
              onChange={setEveryCall}
              label={<span className="text-sm">Every signed call to the webhook starts a mission</span>}
            />
          )}
          {whenError && (
            <span role="alert" className="text-sm text-err">
              {whenError}
            </span>
          )}
          {!canTriggers ? (
            <span className="text-sm text-text-3">
              Schedules and events need the triggers:write permission.
            </span>
          ) : !playbook && when === 'now' ? (
            <span className="text-sm text-text-3">Schedules and events need a playbook.</span>
          ) : (
            !triggersServed && (
              <span className="text-sm text-text-3">Triggers are not available on this server yet.</span>
            )
          )}
        </fieldset>
        <div className="flex flex-col gap-2.5">
          <div className="mb-0.5 text-meta text-text-2">Limits</div>
          <label className="flex items-center justify-between gap-3 whitespace-nowrap">
            Budget
            <span className="flex items-center gap-1.5">
              <Input
                type="number"
                min={10_000}
                step={100_000}
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
                className="h-7 w-[120px] text-right font-mono"
                aria-label="Token budget"
              />
              <span className="text-meta text-text-3">tokens</span>
            </span>
          </label>
          <label className="flex items-center justify-between gap-3 whitespace-nowrap">
            Max duration
            <span className="flex items-center gap-1.5">
              <Input
                type="number"
                min={1}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                className="h-7 w-[80px] text-right font-mono"
                aria-label="Max duration in minutes"
              />
              <span className="text-meta text-text-3">min</span>
            </span>
          </label>
          <label className="flex items-center justify-between gap-3 whitespace-nowrap">
            Data class
            <Select
              value={dataClass}
              onChange={(e) => setDataClass(e.target.value as DataClass)}
              className="h-7 w-[172px] text-sm"
              aria-label="Data class"
            >
              <option value="C0">C0 public</option>
              <option value="C1">C1 internal</option>
              <option value="C2">C2 confidential</option>
              <option value="C3">C3 restricted</option>
            </Select>
          </label>
          <span className="text-sm text-text-3">
            The class only goes up during the mission; C2 and above stay on internal models.
          </span>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
        {canSavePlaybook && (
          <div className="flex flex-wrap items-center gap-2.5">
            <Checkbox checked={saveAs} onChange={setSaveAs} label="Save as a playbook" />
            {saveAs && (
              <Input
                aria-label="Playbook name"
                placeholder="playbook-name"
                value={saveName}
                onChange={(e) => setSaveName(slug(e.target.value))}
                className="h-7 w-[200px] font-mono text-sm"
              />
            )}
          </div>
        )}
        <div className="flex-1" />
        <Button size="lg" onClick={() => window.history.back()}>
          Cancel
        </Button>
        <Button
          size="lg"
          variant="primary"
          busy={busy}
          disabled={!!whenError}
          title={whenError ?? undefined}
          onClick={() => void start()}
        >
          {when !== 'now'
            ? 'Create the trigger'
            : mode === 'plan_first'
              ? 'Create and review the plan'
              : 'Start mission'}
        </Button>
      </div>
    </main>
  )
}
