/**
 * Folds the mission event stream (contracts/events.schema.json) into what the mission screen shows:
 * the activity timeline, plan, sub-agents, terminal, files, browser, reasoning, open questions and
 * proposed actions. Pure and idempotent: replaying an event (SSE resume, replay endpoint) is a no-op.
 */
import { isEventType, type ZEvent, type ZEventOf } from '@/api/events.gen'
import type { PlanStep } from '@/api/types'

export type TimelineItem =
  | {
      kind: 'tool'
      key: string
      seq: number
      ts: string
      agent: string | null
      jobId: string | null
      call: ZEventOf<'tool_call'>['payload']
      result: ZEventOf<'tool_result'>['payload'] | null
    }
  | { kind: 'file_edit'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; edit: ZEventOf<'file_edit'>['payload'] }
  | { kind: 'denied'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; denied: ZEventOf<'denied'>['payload'] }
  | { kind: 'question'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; questionId: string }
  | { kind: 'action'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; actionId: string }
  | { kind: 'message'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; message: ZEventOf<'message'>['payload'] }
  | { kind: 'validation'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; error: ZEventOf<'validation_error'>['payload'] }
  | { kind: 'status'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; status: ZEventOf<'status'>['payload'] }
  | { kind: 'subagent'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; sub: ZEventOf<'subagent'>['payload'] }
  | { kind: 'artifact'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; artifact: ZEventOf<'artifact'>['payload'] }
  | { kind: 'data_class'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; change: ZEventOf<'data_class'>['payload'] }
  | { kind: 'judge'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; judge: ZEventOf<'judge'>['payload'] }
  | { kind: 'terminal'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; term: ZEventOf<'terminal'>['payload'] }
  | { kind: 'browser'; key: string; seq: number; ts: string; agent: string | null; jobId: string | null; browser: ZEventOf<'browser'>['payload'] }

export interface QuestionState {
  id: string
  text: string
  options: string[]
  agent: string | null
  jobId: string | null
  ts: string
  answer: string | null
  answeredBy: string | null
}

export interface ActionState {
  id: string
  summary: string
  connector: string
  operation: string
  cls: 'write' | 'destructive'
  status: string
  agent: string | null
  jobId: string | null
  ts: string
}

export interface JobState {
  id: string
  status: string
  agent: string | null
  stepId: string | null
  reason: string | null
}

export interface TimelineState {
  lastSeq: number
  seqs: Set<number>
  items: TimelineItem[]
  plan: PlanStep[] | null
  missionStatus: string | null
  jobs: Map<string, JobState>
  subagents: ZEventOf<'subagent'>['payload'][]
  tokens: { in: number; out: number; cached: number }
  models: Set<string>
  thoughts: { seq: number; ts: string; agent: string | null; text: string }[]
  questions: Map<string, QuestionState>
  actions: Map<string, ActionState>
  dataClass: string | null
  denied: number
  flagged: number
  lastActivity: { ts: string; text: string } | null
}

export function emptyTimeline(): TimelineState {
  return {
    lastSeq: 0,
    seqs: new Set(),
    items: [],
    plan: null,
    missionStatus: null,
    jobs: new Map(),
    subagents: [],
    tokens: { in: 0, out: 0, cached: 0 },
    models: new Set(),
    thoughts: [],
    questions: new Map(),
    actions: new Map(),
    dataClass: null,
    denied: 0,
    flagged: 0,
    lastActivity: null,
  }
}

/** Parse one SSE message (`event: <type>`, JSON data) into a typed event; null when not an event. */
export function parseEvent(type: string, data: string): ZEvent | null {
  let obj: unknown
  try {
    obj = JSON.parse(data) as unknown
  } catch {
    return null
  }
  if (typeof obj !== 'object' || obj === null) return null
  const o = obj as Record<string, unknown>
  const t = typeof o.type === 'string' ? o.type : type
  if (!isEventType(t) || typeof o.seq !== 'number' || typeof o.payload !== 'object' || o.payload === null) return null
  return { ...(o as unknown as ZEvent), type: t } as ZEvent
}

function short(v: unknown, max = 80): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** A one-line summary of what the agent is doing now (Mission control "Now" column). */
export function activityText(e: ZEvent): string | null {
  switch (e.type) {
    case 'tool_call': {
      const args = Object.values(e.payload.args).filter((v) => typeof v === 'string' || typeof v === 'number')
      return [e.payload.operation ?? e.payload.tool, args[0] !== undefined ? short(args[0], 40) : null]
        .filter(Boolean)
        .join(' · ')
    }
    case 'terminal':
      return e.payload.command
    case 'file_edit':
      return `edit ${e.payload.path}`
    case 'browser':
      return `${e.payload.action} · ${e.payload.url}`
    case 'question':
      return 'Waiting for your answer'
    case 'action_proposed':
      return 'Waiting for approval'
    case 'subagent':
      return `sub-agent ${e.payload.agent}`
    case 'denied':
      return `blocked: ${e.payload.what}`
    default:
      return null
  }
}

/** Fold events into the state. Returns a new state object (Maps/arrays copied when touched). */
export function reduceEvents(prev: TimelineState, events: readonly ZEvent[]): TimelineState {
  if (events.length === 0) return prev
  const fresh = events.filter((e) => !prev.seqs.has(e.seq))
  if (fresh.length === 0) return prev
  fresh.sort((a, b) => a.seq - b.seq)

  const s: TimelineState = {
    ...prev,
    seqs: new Set(prev.seqs),
    items: [...prev.items],
    jobs: new Map(prev.jobs),
    subagents: [...prev.subagents],
    tokens: { ...prev.tokens },
    models: new Set(prev.models),
    thoughts: [...prev.thoughts],
    questions: new Map(prev.questions),
    actions: new Map(prev.actions),
  }
  let outOfOrder = false
  const toolIndex = new Map<string, number>()
  s.items.forEach((it, i) => {
    if (it.kind === 'tool') toolIndex.set(it.call.call_id, i)
  })

  for (const e of fresh) {
    s.seqs.add(e.seq)
    if (e.seq < s.lastSeq) outOfOrder = true
    s.lastSeq = Math.max(s.lastSeq, e.seq)
    const base = { seq: e.seq, ts: e.ts, agent: e.agent ?? null, jobId: e.job_id }
    const act = activityText(e)
    if (act) s.lastActivity = { ts: e.ts, text: act }

    switch (e.type) {
      case 'status': {
        const p = e.payload
        if (p.entity === 'mission') s.missionStatus = p.to
        if (p.entity === 'job') {
          const id = p.id ?? e.job_id
          if (id) {
            const cur = s.jobs.get(id)
            s.jobs.set(id, {
              id,
              status: p.to,
              agent: e.agent ?? cur?.agent ?? null,
              stepId: e.step_id ?? cur?.stepId ?? null,
              reason: p.reason ?? null,
            })
          }
        }
        if (p.entity === 'step' && s.plan) {
          const stepId = p.id ?? e.step_id
          s.plan = s.plan.map((st) => (st.id === stepId ? { ...st, status: p.to } : st))
        }
        // Mission-level transitions and failures are worth a line; routine job starts are not.
        if (p.entity === 'mission' || ['failed', 'needs_attention', 'budget_exceeded', 'timed_out', 'paused'].includes(p.to)) {
          s.items.push({ kind: 'status', key: `s${e.seq}`, ...base, status: p })
        }
        break
      }
      case 'plan':
        s.plan = e.payload.steps.map((st) => ({ ...st }))
        break
      case 'message':
        s.items.push({ kind: 'message', key: `m${e.seq}`, ...base, message: e.payload })
        break
      case 'thought':
        s.thoughts.push({ seq: e.seq, ts: e.ts, agent: e.agent ?? null, text: e.payload.text })
        break
      case 'tool_call': {
        const idx = toolIndex.get(e.payload.call_id)
        if (idx === undefined) {
          toolIndex.set(e.payload.call_id, s.items.length)
          s.items.push({ kind: 'tool', key: `t${e.payload.call_id}`, ...base, call: e.payload, result: null })
        }
        break
      }
      case 'tool_result': {
        const idx = toolIndex.get(e.payload.call_id)
        const item = idx === undefined ? undefined : s.items[idx]
        if (item?.kind === 'tool') {
          s.items[idx as number] = { ...item, result: e.payload }
        } else {
          // Result without its call (stream resumed in between): show it on its own.
          toolIndex.set(e.payload.call_id, s.items.length)
          s.items.push({
            kind: 'tool',
            key: `t${e.payload.call_id}`,
            ...base,
            call: { call_id: e.payload.call_id, tool: 'tool', args: {} },
            result: e.payload,
          })
        }
        if (e.payload.judge !== 'clean') s.flagged += 1
        break
      }
      case 'terminal':
        s.items.push({ kind: 'terminal', key: `x${e.seq}`, ...base, term: e.payload })
        break
      case 'file_edit':
        s.items.push({ kind: 'file_edit', key: `f${e.seq}`, ...base, edit: e.payload })
        break
      case 'browser':
        s.items.push({ kind: 'browser', key: `b${e.seq}`, ...base, browser: e.payload })
        break
      case 'denied':
        s.denied += 1
        s.items.push({ kind: 'denied', key: `d${e.seq}`, ...base, denied: e.payload })
        break
      case 'validation_error':
        s.items.push({ kind: 'validation', key: `v${e.seq}`, ...base, error: e.payload })
        break
      case 'usage':
        s.tokens.in += e.payload.tokens_in
        s.tokens.out += e.payload.tokens_out
        s.tokens.cached += e.payload.cached_tokens ?? 0
        s.models.add(e.payload.model)
        break
      case 'artifact':
        s.items.push({ kind: 'artifact', key: `a${e.seq}`, ...base, artifact: e.payload })
        break
      case 'question': {
        const q = e.payload
        const cur = s.questions.get(q.question_id)
        s.questions.set(q.question_id, {
          id: q.question_id,
          text: q.text,
          options: q.options ?? [],
          agent: e.agent ?? null,
          jobId: e.job_id,
          ts: e.ts,
          answer: cur?.answer ?? null,
          answeredBy: cur?.answeredBy ?? null,
        })
        if (!cur) s.items.push({ kind: 'question', key: `q${q.question_id}`, ...base, questionId: q.question_id })
        break
      }
      case 'question_answered': {
        const q = s.questions.get(e.payload.question_id)
        s.questions.set(e.payload.question_id, {
          id: e.payload.question_id,
          text: q?.text ?? '',
          options: q?.options ?? [],
          agent: q?.agent ?? e.agent ?? null,
          jobId: q?.jobId ?? e.job_id,
          ts: q?.ts ?? e.ts,
          answer: e.payload.answer,
          answeredBy: e.payload.answered_by,
        })
        break
      }
      case 'action_proposed': {
        const a = e.payload
        const cur = s.actions.get(a.action_id)
        s.actions.set(a.action_id, {
          id: a.action_id,
          summary: a.summary,
          connector: a.connector,
          operation: a.operation,
          cls: a.class,
          status: cur?.status ?? 'pending',
          agent: e.agent ?? null,
          jobId: e.job_id,
          ts: e.ts,
        })
        if (!cur) s.items.push({ kind: 'action', key: `p${a.action_id}`, ...base, actionId: a.action_id })
        break
      }
      case 'action_status': {
        const cur = s.actions.get(e.payload.action_id)
        if (cur) s.actions.set(cur.id, { ...cur, status: e.payload.to })
        else
          s.actions.set(e.payload.action_id, {
            id: e.payload.action_id,
            summary: '',
            connector: '',
            operation: '',
            cls: 'write',
            status: e.payload.to,
            agent: e.agent ?? null,
            jobId: e.job_id,
            ts: e.ts,
          })
        break
      }
      case 'subagent':
        s.subagents.push(e.payload)
        if (!s.jobs.has(e.payload.child_job_id)) {
          s.jobs.set(e.payload.child_job_id, {
            id: e.payload.child_job_id,
            status: 'queued',
            agent: e.payload.agent,
            stepId: e.step_id ?? null,
            reason: null,
          })
        }
        s.items.push({ kind: 'subagent', key: `g${e.seq}`, ...base, sub: e.payload })
        break
      case 'data_class':
        s.dataClass = e.payload.to
        s.items.push({ kind: 'data_class', key: `c${e.seq}`, ...base, change: e.payload })
        break
      case 'judge':
        if (e.payload.verdict !== 'clean') {
          s.flagged += 1
          s.items.push({ kind: 'judge', key: `j${e.seq}`, ...base, judge: e.payload })
        }
        break
      case 'checkpoint':
      case 'learning_signal':
      case 'objective_update':
        break
    }
  }
  if (outOfOrder) s.items.sort((a, b) => a.seq - b.seq)
  return s
}

/** Plan step progress for the checklist: done / current / pending, from statuses and job counts. */
export function stepState(step: PlanStep): 'done' | 'current' | 'failed' | 'waiting' | 'pending' {
  const st = step.status
  if (st === 'succeeded' || st === 'done' || st === 'skipped') return 'done'
  if (st === 'failed' || st === 'cancelled') return 'failed'
  if (st === 'waiting' || st === 'needs_attention') return 'waiting'
  if (st === 'running') return 'current'
  const j = step.jobs
  if (j?.total && j.total > 0) {
    if ((j.succeeded ?? 0) + (j.failed ?? 0) >= j.total) return (j.failed ?? 0) > 0 ? 'failed' : 'done'
    if ((j.running ?? 0) > 0 || (j.succeeded ?? 0) > 0) return 'current'
  }
  return 'pending'
}
