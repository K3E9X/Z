import type { Mission, MissionPlan, PlanStep } from '@/api/types'
import { stepTitle } from '@/components/PlanChecklist'
import { stepState } from './timeline'

export function planSteps(m: Pick<Mission, 'plan'> | null | undefined): PlanStep[] {
  const plan = m?.plan as MissionPlan | null | undefined
  return plan?.steps ?? []
}

export function currentStep(m: Mission): PlanStep | null {
  const steps = planSteps(m)
  return (
    steps.find((s) => ['current', 'waiting'].includes(stepState(s))) ??
    steps.find((s) => stepState(s) === 'pending') ??
    steps[steps.length - 1] ??
    null
  )
}

/** The agent shown for a mission row: the agent of the step in progress, else the first step's. */
export function missionAgent(m: Mission): string | null {
  return currentStep(m)?.agent ?? planSteps(m)[0]?.agent ?? null
}

export function missionAgents(m: Mission): string[] {
  return [...new Set(planSteps(m).map((s) => s.agent).filter((a): a is string => !!a))]
}

export function progress(m: Mission): { done: number; total: number } {
  const j = m.jobs
  if (j?.total) return { done: (j.succeeded ?? 0) + (j.failed ?? 0) + (j.cancelled ?? 0), total: j.total }
  const steps = planSteps(m)
  return { done: steps.filter((s) => stepState(s) === 'done').length, total: steps.length }
}

export function nowLine(m: Mission): { text: string; tone: 'wait' | 'mono' | 'plain' } {
  if (m.status === 'waiting') {
    return {
      text: m.waiting_reason === 'question' ? 'Waiting for your answer' : 'Waiting for approval',
      tone: 'wait',
    }
  }
  if (m.status === 'paused') return { text: 'Paused', tone: 'wait' }
  if (m.status === 'queued') return { text: 'Queued', tone: 'plain' }
  const s = currentStep(m)
  return { text: s ? stepTitle(s) : 'Starting', tone: 'mono' }
}

export function playbookTitle(name: string | null | undefined): string {
  if (!name) return ''
  return name.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
