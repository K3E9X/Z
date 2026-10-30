import { useQueries } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { api, unwrap } from '@/api/client'
import { qk, useAgents } from '@/api/queries'
import type { Agent, Objective } from '@/api/types'
import { Sparkline } from '@/components/charts'
import { StatusText } from '@/components/StatusMark'
import { SectionHeader } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { compactNumber, relative } from '@/lib/format'

/** Agents whose objectives are read for the widget (one request each: the contract lists objectives per agent). */
const MAX_AGENTS = 50
const SHOWN = 6

const RISK = {
  off_track: { tone: 'err' as const, label: 'Off track', rank: 0 },
  at_risk: { tone: 'wait' as const, label: 'At risk', rank: 1 },
}

/**
 * Mission control widget (docs/09 §5): objectives at risk or off track across agents, each linking to its
 * agent's Objectives tab. Hidden without agents:read, and when no agent has an objective.
 */
export function ObjectivesWidget() {
  const canRead = useCan('agents:read')
  const agents = useAgents({ enabled: canRead })
  const list: Agent[] = (agents.data?.items ?? []).filter((a) => a.status !== 'disabled').slice(0, MAX_AGENTS)
  const results = useQueries({
    queries: list.map((a) => ({
      queryKey: qk.objectives(a.id),
      queryFn: () => unwrap(api.GET('/agents/{id}/objectives', { params: { path: { id: a.id } } })),
      enabled: canRead,
      staleTime: 60_000,
    })),
  })
  if (!canRead) return null
  const all: { o: Objective; agent: Agent }[] = results.flatMap((r, i) => {
    const agent = list[i]
    return agent ? (r.data ?? []).map((o) => ({ o, agent })) : []
  })
  if (all.length === 0) return null
  const risky = all
    .filter(
      (x): x is { o: Objective & { status: keyof typeof RISK }; agent: Agent } =>
        x.o.status === 'at_risk' || x.o.status === 'off_track',
    )
    .sort((a, b) => RISK[a.o.status].rank - RISK[b.o.status].rank)
  return (
    <section aria-labelledby="objectives" className="flex flex-col">
      <SectionHeader
        id="objectives"
        title="Objectives"
        right={
          <span>{risky.length === 0 ? `${all.length} on track` : `${risky.length} need attention`}</span>
        }
      />
      {risky.length === 0 ? (
        <div className="py-3.5 text-ui text-text-3">
          {all.length === 1 ? 'The objective is on track.' : `All ${all.length} objectives are on track.`}
        </div>
      ) : (
        <ul className="m-0 list-none p-0">
          {risky.slice(0, SHOWN).map(({ o, agent }) => {
            const st = RISK[o.status]
            const target = `${o.metric.direction === 'down' ? '≤' : '≥'} ${compactNumber(o.metric.target)}`
            return (
              <li key={o.id} className="border-b border-line">
                <Link
                  to="/agents"
                  search={{ agent: agent.id, tab: 'objectives' }}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 text-ui text-text no-underline hover:bg-panel"
                >
                  <StatusText tone={st.tone} label={st.label} className="w-24 flex-none" />
                  <span className="min-w-0 flex-1 truncate font-medium">{o.title}</span>
                  <span className="text-text-2">{agent.display_name}</span>
                  <span className="font-mono text-[12.5px] text-text-2">
                    {o.last_value == null ? '—' : compactNumber(o.last_value)} · target {target}
                  </span>
                  {o.trend && o.trend.length > 1 && (
                    <Sparkline
                      values={o.trend}
                      width={72}
                      height={18}
                      target={o.metric.target}
                      label={`Trend: ${o.trend.join(', ')} against target ${o.metric.target}`}
                    />
                  )}
                  <span className="w-28 text-right text-meta text-text-3">
                    {o.last_checked_at ? `checked ${relative(o.last_checked_at)}` : 'not checked yet'}
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
      {risky.length > SHOWN && (
        <span className="py-3 text-meta text-text-3">
          {risky.length - SHOWN} more in Agents and skills, on each agent's Objectives tab.
        </span>
      )}
    </section>
  )
}
