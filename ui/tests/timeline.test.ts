import { describe, expect, it } from 'vitest'
import type { ZEvent } from '@/api/events.gen'
import { activityText, emptyTimeline, parseEvent, reduceEvents, stepState } from '@/lib/timeline'

const base = { mission_id: 'msn_1', job_id: 'job_1', ts: '2026-10-28T10:21:00Z', agent: 'dev-engineer', step_id: 'bump' }
let seq = 0
function ev<T extends ZEvent['type']>(type: T, payload: Extract<ZEvent, { type: T }>['payload'], s?: number): ZEvent {
  seq = s ?? seq + 1
  return { ...base, seq, type, payload } as ZEvent
}

describe('event timeline reducer', () => {
  it('merges a tool call with its result and counts judge flags', () => {
    const t = reduceEvents(emptyTimeline(), [
      ev('tool_call', { call_id: 'c1', tool: 'gateway', operation: 'gitlab.projects.list', args: { group: 'infra' } }, 1),
      ev('tool_result', { call_id: 'c1', ok: true, summary: '34 projects', duration_ms: 120, judge: 'flagged' }, 2),
    ])
    expect(t.items).toHaveLength(1)
    const item = t.items[0]
    expect(item?.kind).toBe('tool')
    if (item?.kind === 'tool') expect(item.result?.summary).toBe('34 projects')
    expect(t.flagged).toBe(1)
    expect(t.lastSeq).toBe(2)
  })

  it('is idempotent: replaying events after an SSE resume changes nothing', () => {
    const events = [ev('message', { role: 'agent', text: 'hello' }, 1), ev('denied', { what: 'curl https://repo1.maven.org', rule: 'egress' }, 2)]
    const once = reduceEvents(emptyTimeline(), events)
    const twice = reduceEvents(once, events)
    expect(twice).toBe(once)
    expect(twice.items).toHaveLength(2)
    expect(twice.denied).toBe(1)
  })

  it('sorts events that arrive out of order', () => {
    const t = reduceEvents(emptyTimeline(), [ev('message', { role: 'agent', text: 'second' }, 5), ev('message', { role: 'agent', text: 'first' }, 3)])
    const t2 = reduceEvents(t, [ev('message', { role: 'agent', text: 'between' }, 4)])
    expect(t2.items.map((i) => i.seq)).toEqual([3, 4, 5])
  })

  it('tracks questions, answers, proposed actions and their status', () => {
    let t = reduceEvents(emptyTimeline(), [
      ev('question', { question_id: 'q1', text: 'Mark it failed?', options: ['Yes', 'No'] }, 1),
      ev('action_proposed', { action_id: 'a1', connector: 'gitlab', operation: 'gitlab.merge_requests.create', class: 'write', summary: 'MR on auth-service' }, 2),
    ])
    expect(t.questions.get('q1')?.answer).toBeNull()
    expect(t.actions.get('a1')?.status).toBe('pending')
    t = reduceEvents(t, [ev('question_answered', { question_id: 'q1', answer: 'Yes', answered_by: 'alice' }, 3), ev('action_status', { action_id: 'a1', from: 'pending', to: 'applied' }, 4)])
    expect(t.questions.get('q1')?.answer).toBe('Yes')
    expect(t.actions.get('a1')?.status).toBe('applied')
    expect(t.items.filter((i) => i.kind === 'question')).toHaveLength(1)
  })

  it('sums usage, follows the plan, mission status and data class', () => {
    const t = reduceEvents(emptyTimeline(), [
      ev('plan', { steps: [{ id: 'list', title: 'List' }, { id: 'bump', title: 'Bump' }] }, 1),
      ev('status', { entity: 'step', id: 'list', from: 'running', to: 'succeeded' }, 2),
      ev('status', { entity: 'mission', from: 'queued', to: 'running' }, 3),
      ev('usage', { model: 'z-heavy', tokens_in: 1000, tokens_out: 200, cached_tokens: 600 }, 4),
      ev('usage', { model: 'z-fast', tokens_in: 10, tokens_out: 5 }, 5),
      ev('data_class', { from: 'C1', to: 'C2', cause: 'tool_result' }, 6),
    ])
    expect(t.plan?.[0]?.status).toBe('succeeded')
    expect(t.plan && stepState(t.plan[0] ?? {})).toBe('done')
    expect(t.missionStatus).toBe('running')
    expect(t.tokens).toEqual({ in: 1010, out: 205, cached: 600 })
    expect([...t.models]).toEqual(['z-heavy', 'z-fast'])
    expect(t.dataClass).toBe('C2')
  })

  it('keeps reasoning out of the activity list', () => {
    const t = reduceEvents(emptyTimeline(), [ev('thought', { text: 'The repo uses Maven.' }, 1)])
    expect(t.items).toHaveLength(0)
    expect(t.thoughts[0]?.text).toBe('The repo uses Maven.')
  })

  it('parses SSE data and rejects what is not an event', () => {
    const e = parseEvent('message', JSON.stringify({ ...base, seq: 7, type: 'message', payload: { role: 'agent', text: 'x' } }))
    expect(e?.seq).toBe(7)
    expect(parseEvent('message', 'not json')).toBeNull()
    expect(parseEvent('bogus', JSON.stringify({ seq: 1, type: 'bogus', payload: {} }))).toBeNull()
    expect(activityText(ev('terminal', { command: 'mvn test' }, 9))).toBe('mvn test')
  })

  it('derives step state from fan-out job counts', () => {
    expect(stepState({ id: 's', jobs: { total: 12, succeeded: 7, running: 2 } })).toBe('current')
    expect(stepState({ id: 's', jobs: { total: 3, succeeded: 2, failed: 1 } })).toBe('failed')
    expect(stepState({ id: 's' })).toBe('pending')
  })
})
