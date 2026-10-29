/** The status vocabulary of docs/09 §2: every state has a tone (color) and a label (text). */
export type Tone = 'run' | 'wait' | 'ok' | 'err' | 'idle' | 'budget'

export interface StatusInfo {
  tone: Tone
  label: string
}

const MISSION: Record<string, StatusInfo> = {
  draft: { tone: 'idle', label: 'Draft' },
  planned: { tone: 'idle', label: 'Planned' },
  queued: { tone: 'idle', label: 'Queued' },
  running: { tone: 'run', label: 'Running' },
  waiting: { tone: 'wait', label: 'Waiting' },
  paused: { tone: 'wait', label: 'Paused' },
  succeeded: { tone: 'ok', label: 'Succeeded' },
  failed: { tone: 'err', label: 'Failed' },
  cancelled: { tone: 'idle', label: 'Cancelled' },
  budget_exceeded: { tone: 'budget', label: 'Budget reached' },
  timed_out: { tone: 'err', label: 'Timed out' },
}

const JOB: Record<string, StatusInfo> = {
  queued: { tone: 'idle', label: 'Queued' },
  running: { tone: 'run', label: 'Running' },
  needs_attention: { tone: 'wait', label: 'Needs attention' },
  paused: { tone: 'wait', label: 'Paused' },
  succeeded: { tone: 'ok', label: 'Done' },
  failed: { tone: 'err', label: 'Failed' },
  cancelled: { tone: 'idle', label: 'Cancelled' },
  budget_exceeded: { tone: 'budget', label: 'Budget reached' },
  timed_out: { tone: 'err', label: 'Timed out' },
}

const ACTION: Record<string, StatusInfo> = {
  pending: { tone: 'wait', label: 'Waiting for approval' },
  approved: { tone: 'run', label: 'Approved' },
  applying: { tone: 'run', label: 'Applying' },
  applied: { tone: 'ok', label: 'Applied' },
  failed: { tone: 'err', label: 'Failed' },
  rejected: { tone: 'idle', label: 'Rejected' },
  superseded: { tone: 'idle', label: 'Superseded' },
}

const CONNECTOR: Record<string, StatusInfo> = {
  healthy: { tone: 'ok', label: 'Healthy' },
  degraded: { tone: 'wait', label: 'Degraded' },
  token_expiring: { tone: 'wait', label: 'Token expiring' },
  unreachable: { tone: 'err', label: 'Unreachable' },
  disabled: { tone: 'idle', label: 'Disabled' },
}

const STEP: Record<string, StatusInfo> = {
  pending: { tone: 'idle', label: 'Pending' },
  queued: { tone: 'idle', label: 'Queued' },
  running: { tone: 'run', label: 'Running' },
  waiting: { tone: 'wait', label: 'Waiting' },
  needs_attention: { tone: 'wait', label: 'Needs attention' },
  succeeded: { tone: 'ok', label: 'Done' },
  done: { tone: 'ok', label: 'Done' },
  skipped: { tone: 'idle', label: 'Skipped' },
  failed: { tone: 'err', label: 'Failed' },
  cancelled: { tone: 'idle', label: 'Cancelled' },
}

const NEEDS_ATTENTION: Record<string, string> = {
  question: 'Waiting for an answer',
  approval: 'Waiting for approval',
  invalid_output: 'Output does not match its schema',
  loop_detected: 'Loop detected',
  guardrail_confirm: 'A guardrail asks for confirmation',
  connector_down: 'A connector is down',
  judge_blocked: 'The judge blocked a tool result',
}

function pick(table: Record<string, StatusInfo>, status: string | null | undefined): StatusInfo {
  if (!status) return { tone: 'idle', label: '—' }
  return table[status] ?? { tone: 'idle', label: status.replace(/_/g, ' ') }
}

export const missionStatus = (s: string | null | undefined) => pick(MISSION, s)
export const jobStatus = (s: string | null | undefined) => pick(JOB, s)
export const actionStatus = (s: string | null | undefined) => pick(ACTION, s)
export const connectorStatus = (s: string | null | undefined) => pick(CONNECTOR, s)
export const stepStatus = (s: string | null | undefined) => pick(STEP, s)
export const needsAttentionLabel = (r: string | null | undefined) => (r ? (NEEDS_ATTENTION[r] ?? r) : '')

export const TERMINAL_MISSION = new Set(['succeeded', 'failed', 'cancelled', 'budget_exceeded', 'timed_out'])
export const ACTIVE_MISSION = new Set(['queued', 'running', 'waiting', 'paused'])

export const toneVar: Record<Tone, string> = {
  run: 'var(--run)',
  wait: 'var(--wait)',
  ok: 'var(--ok)',
  err: 'var(--err)',
  idle: 'var(--text-3)',
  budget: 'var(--err)',
}

export const toneText: Record<Tone, string> = {
  run: 'text-run',
  wait: 'text-wait',
  ok: 'text-ok',
  err: 'text-err',
  idle: 'text-text-3',
  budget: 'text-err',
}
