import { useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo } from 'react'
import { useAnalytics } from '@/api/queries'
import { HBars } from '@/components/charts'
import { KpiStrip } from '@/components/KpiStrip'
import { QueryState } from '@/components/states'
import { PageTitle, Tabs } from '@/components/ui'
import { compactNumber, grouped, percent } from '@/lib/format'
import { playbookTitle } from '@/lib/mission'
import { missionStatus } from '@/lib/status'
import { useNow } from '@/lib/useMedia'

type Range = '7d' | '30d' | '90d'
const DAYS: Record<Range, number> = { '7d': 7, '30d': 30, '90d': 90 }

/** Analytics (docs/09 §5): hours saved by playbook, approval and edit rates, failures by connector, tokens by team. */
export function Analytics() {
  const search = useSearch({ from: '/analytics' })
  const navigate = useNavigate()
  const range = (search.range && search.range in DAYS ? search.range : '30d') as Range
  const day = useNow(3600_000)
  const { from, to } = useMemo(() => ({ from: new Date(day - DAYS[range] * 86400_000).toISOString(), to: new Date(day).toISOString() }), [day, range])
  const q = useAnalytics(from, to)
  const a = q.data
  const hours = (a?.hours_saved ?? []).reduce((n, h) => n + h.hours, 0)
  const completed = (a?.hours_saved ?? []).reduce((n, h) => n + h.completed, 0)
  const statuses = Object.entries(a?.missions_by_status ?? {})
  const total = statuses.reduce((n, [, v]) => n + v, 0)
  return (
    <main className="flex w-full max-w-[1320px] flex-col gap-7 px-[72px] pt-11 pb-14 max-lg:px-8 max-md:px-5">
      <PageTitle
        title="Analytics"
        lead="What the agents are worth: time saved by playbook, how often their writes are approved or edited, where they fail, and what they cost."
        right={<Tabs<Range> label="Range" variant="pill" className="rounded-btn border border-line-2 p-1" value={range} onChange={(r) => void navigate({ to: '/analytics', search: { range: r }, replace: true })} items={(['7d', '30d', '90d'] as Range[]).map((r) => ({ id: r, label: r }))} />}
      />
      <QueryState query={q} what="Analytics" endpoint="GET /analytics">
        {() =>
          !a ? null : (
            <>
              <KpiStrip
                items={[
                  { label: 'Hours saved', value: hours.toFixed(hours < 10 ? 1 : 0), sub: `${grouped(completed)} missions × baseline minutes` },
                  { label: 'Approval rate', value: percent(a.approval_rate), sub: 'writes approved without edit or reject' },
                  { label: 'Edit rate', value: percent(a.edit_rate), sub: 'writes changed before approval' },
                  { label: 'Missions', value: grouped(total), sub: statuses.map(([k, v]) => `${v} ${missionStatus(k).label.toLowerCase()}`).slice(0, 3).join(', ') },
                ]}
              />
              <div className="grid gap-6 lg:grid-cols-2">
                <section aria-label="Hours saved by playbook" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
                  <h2 className="m-0 text-[15px] font-semibold">Hours saved by playbook</h2>
                  {a.hours_saved.length ? (
                    <HBars rows={[...a.hours_saved].sort((x, y) => y.hours - x.hours).map((h) => ({ label: playbookTitle(h.playbook), value: Math.round(h.hours * 10) / 10, sub: `${h.completed} × ${h.baseline_minutes} min` }))} />
                  ) : (
                    <span className="text-meta text-text-3">No playbook with a baseline completed in this range.</span>
                  )}
                </section>
                <section aria-label="Failures by connector" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
                  <h2 className="m-0 text-[15px] font-semibold">Failures by connector</h2>
                  {a.failures_by_connector.length ? <HBars rows={a.failures_by_connector.map((f) => ({ label: f.connector, value: f.failures }))} /> : <span className="text-meta text-text-3">No connector failure.</span>}
                </section>
                <section aria-label="Tokens by team" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
                  <h2 className="m-0 text-[15px] font-semibold">Tokens and cost by team</h2>
                  <table className="w-full border-collapse text-ui">
                    <thead>
                      <tr className="text-sm text-text-3">
                        <th className="py-1.5 font-normal">Team</th>
                        <th className="py-1.5 text-right font-normal">Tokens</th>
                        <th className="py-1.5 text-right font-normal">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.tokens_by_team.map((t) => (
                        <tr key={t.team} className="border-t border-line">
                          <td className="py-2">{t.team}</td>
                          <td className="py-2 text-right font-mono">{compactNumber(t.tokens)}</td>
                          <td className="py-2 text-right font-mono">{t.cost !== null && t.cost !== undefined ? t.cost.toFixed(2) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
                <section aria-label="Missions by status" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
                  <h2 className="m-0 text-[15px] font-semibold">Missions by status</h2>
                  <HBars rows={statuses.sort((x, y) => y[1] - x[1]).map(([k, v]) => ({ label: missionStatus(k).label, value: v }))} />
                </section>
              </div>
            </>
          )
        }
      </QueryState>
    </main>
  )
}
