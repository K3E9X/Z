import {
  useAgents,
  useBudgets,
  useConnectors,
  useInferenceHealth,
  useMissions,
  useProposals,
  useReview,
} from '@/api/queries'
import { useCan } from '@/lib/auth'

/** Counts shown next to the navigation (docs/09 §3: the Review count is visible everywhere). */
export function useShellData() {
  const canMissions = useCan('missions:read')
  const review = useReview('all')
  const running = useMissions({ status: 'running,waiting,paused,queued' }, { live: canMissions })
  const agents = useAgents()
  const connectors = useConnectors({ live: true })
  const proposals = useProposals('ready')
  const health = useInferenceHealth()
  const budgets = useBudgets()

  const reviewCount = review.data?.counts.all ?? null
  const runningCount = running.data
    ? running.data.items.filter((m) => m.status === 'running' || m.status === 'waiting').length
    : null
  const agentCount = agents.data?.items.length ?? null
  const unhealthy = connectors.data
    ? connectors.data.filter((c) => c.enabled && c.health.status !== 'healthy').length
    : 0
  const connectorAlert: 'err' | 'wait' | null = connectors.data?.some(
    (c) => c.enabled && c.health.status === 'unreachable',
  )
    ? 'err'
    : unhealthy > 0
      ? 'wait'
      : null
  const learningCount = proposals.data?.items.length ?? null
  const orgBudget =
    budgets.data?.find((b) => b.scope === 'org' && b.period === 'month') ??
    budgets.data?.find((b) => b.scope === 'org')
  const budgetPct = orgBudget && orgBudget.limit > 0 ? orgBudget.used / orgBudget.limit : null

  return {
    reviewCount,
    reviewCounts: review.data?.counts ?? null,
    runningCount,
    agentCount,
    connectorAlert,
    learningCount,
    health: health.data ?? null,
    healthError: health.error,
    budgetPct,
    orgBudget: orgBudget ?? null,
  }
}
