import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk, useAgents } from '@/api/queries'
import { toContractPlan, type Mission, type MissionPlan, type PlanStep } from '@/api/types'
import { Dialog } from '@/components/Dialog'
import { IconButton, Button, Input, Select, Textarea } from '@/components/ui'
import { useToast } from '@/components/toast'
import { planSteps } from '@/lib/mission'

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 40) || 'step'
  )
}

/** Inline editor for plan steps: title, agent, prompt, writes; add, remove and reorder. */
export function PlanStepsEditor({
  steps,
  onChange,
}: {
  steps: PlanStep[]
  onChange: (s: PlanStep[]) => void
}) {
  const agents = useAgents()
  const list = agents.data?.items ?? []
  const set = (i: number, patch: Partial<PlanStep>) => {
    onChange(steps.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  }
  const move = (i: number, d: -1 | 1) => {
    const next = [...steps]
    const [x] = next.splice(i, 1)
    if (!x) return
    next.splice(i + d, 0, x)
    onChange(next)
  }
  return (
    <ol className="m-0 flex list-none flex-col gap-3 p-0">
      {steps.map((s, i) => (
        <li
          key={`${s.id ?? 'new'}-${i}`}
          className="flex flex-col gap-2.5 rounded-panel border border-line p-3.5"
        >
          <div className="flex items-center gap-2">
            <span className="w-6 font-mono text-sm text-text-3">{String(i + 1).padStart(2, '0')}</span>
            <Input
              aria-label={`Step ${i + 1} title`}
              value={s.title ?? ''}
              placeholder="What this step does"
              onChange={(e) => {
                set(i, { title: e.target.value })
              }}
              className="flex-1"
            />
            <Select
              aria-label={`Step ${i + 1} agent`}
              value={s.agent ?? ''}
              onChange={(e) => {
                set(i, { agent: e.target.value })
              }}
              className="w-[190px]"
            >
              <option value="">Agent…</option>
              {list.map((a) => (
                <option key={a.id} value={a.name}>
                  {a.display_name} (@{a.handle})
                </option>
              ))}
            </Select>
            <IconButton
              icon="chevronDown"
              label="Move down"
              disabled={i === steps.length - 1}
              onClick={() => move(i, 1)}
              className="rotate-0"
            />
            <IconButton icon="arrowUp" label="Move up" disabled={i === 0} onClick={() => move(i, -1)} />
            <IconButton
              icon="x"
              label={`Remove step ${i + 1}`}
              onClick={() => onChange(steps.filter((_, j) => j !== i))}
            />
          </div>
          <Textarea
            aria-label={`Step ${i + 1} instructions`}
            rows={2}
            value={s.prompt ?? ''}
            placeholder="Instructions for the agent"
            onChange={(e) => {
              set(i, { prompt: e.target.value })
            }}
          />
          <Input
            aria-label={`Step ${i + 1} writes`}
            value={(s.writes ?? []).join(', ')}
            placeholder="Writes (full operation ids, e.g. jira.issues.create) — leave empty for read only"
            onChange={(e) => {
              set(i, { writes: e.target.value.split(/[\s,]+/).filter(Boolean) })
            }}
            className="font-mono text-sm"
          />
        </li>
      ))}
      <li>
        <Button
          size="sm"
          icon="plus"
          onClick={() => {
            const title = `Step ${steps.length + 1}`
            onChange([
              ...steps,
              {
                id: slugify(title),
                title,
                agent: steps[steps.length - 1]?.agent ?? '',
                prompt: '',
                depends_on: steps.length ? [steps[steps.length - 1]?.id ?? ''] : [],
              },
            ])
          }}
        >
          Add a step
        </Button>
      </li>
    </ol>
  )
}

/** Ensure ids are unique slugs and dependencies point to existing steps before saving. */
export function normalizePlan(steps: PlanStep[], base?: MissionPlan | null): MissionPlan {
  const seen = new Set<string>()
  const out = steps.map((s) => {
    let id = s.id || slugify(s.title ?? 'step')
    while (seen.has(id)) id = `${id}-2`
    seen.add(id)
    const { status: _s, jobs: _j, ...rest } = s
    return { ...rest, id }
  })
  const ids = new Set(out.map((s) => s.id))
  const fixed = out.map((s) => ({ ...s, depends_on: (s.depends_on ?? []).filter((d) => ids.has(d)) }))
  const writes = [...new Set(fixed.flatMap((s) => s.writes ?? []))]
  return { ...(base ?? {}), steps: fixed, writes }
}

export function PlanEditor({
  mission,
  open,
  onClose,
}: {
  mission: Mission
  open: boolean
  onClose: () => void
}) {
  const [steps, setSteps] = useState<PlanStep[]>(() => planSteps(mission).map((s) => ({ ...s })))
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()
  const save = async () => {
    setBusy(true)
    try {
      const plan = normalizePlan(steps, mission.plan)
      await unwrap(
        api.PATCH('/missions/{id}/plan', {
          params: { path: { id: mission.id } },
          body: { plan: toContractPlan(plan) },
        }),
      )
      await qc.invalidateQueries({ queryKey: qk.mission(mission.id) })
      toast.ok('Plan saved')
      onClose()
    } catch (e) {
      toast.error(e, 'Plan not saved')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Edit the plan"
      width={820}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={busy} disabled={steps.length === 0} onClick={() => void save()}>
            Save plan
          </Button>
        </>
      }
    >
      <PlanStepsEditor steps={steps} onChange={setSteps} />
    </Dialog>
  )
}
