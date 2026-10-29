import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useAgents, useEvalRuns, useSkills } from '@/api/queries'
import type { EvalRun } from '@/api/types'
import { Legend, Sparkline } from '@/components/charts'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Checkbox, PageTitle, Select, Skeleton } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { compactNumber, relative } from '@/lib/format'

function score(r: EvalRun): string {
  return r.scores ? `${r.scores.cases_passed} / ${r.scores.cases_total}` : r.status
}

function RunDetail({ id }: { id: string }) {
  const q = useQuery({
    queryKey: qk.evalRun(id),
    queryFn: () => unwrap(api.GET('/evals/{run_id}', { params: { path: { run_id: id } } })),
    refetchInterval: (query) =>
      query.state.data && ['queued', 'running'].includes(query.state.data.status) ? 3000 : false,
  })
  if (!q.data)
    return q.error ? (
      <QueryState query={q} what="This eval run" endpoint="GET /evals/{run_id}">
        {() => null}
      </QueryState>
    ) : (
      <Skeleton className="h-60" />
    )
  const r = q.data
  return (
    <section
      aria-label="Eval run"
      className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="m-0 text-[15px] font-semibold">
          {r.target}@{r.target_version} on <span className="font-mono">{r.model}</span>
        </h2>
        <span
          className={`text-meta ${r.passed ? 'text-ok' : r.passed === false ? 'text-err' : 'text-text-3'}`}
        >
          {r.status} · {score(r)}
          {r.scores?.rubric_avg !== null && r.scores?.rubric_avg !== undefined
            ? ` · rubric ${r.scores.rubric_avg.toFixed(2)} / 2`
            : ''}
          {r.scores ? ` · ${compactNumber(r.scores.tokens)} tokens` : ''}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-ui">
          <thead>
            <tr className="border-b border-line text-sm text-text-3">
              <th className="py-2 pr-3 font-normal">Case</th>
              <th className="py-2 pr-3 font-normal">Result</th>
              <th className="py-2 pr-3 font-normal">Checks (expected vs got)</th>
              <th className="py-2 font-normal">Rubric</th>
            </tr>
          </thead>
          <tbody>
            {r.cases.map((c) => (
              <tr key={c.id} className="border-b border-line align-top">
                <td className="py-2.5 pr-3 font-mono text-[12.5px]">{c.id}</td>
                <td className={`py-2.5 pr-3 ${c.passed ? 'text-ok' : 'text-err'}`}>
                  {c.passed ? 'pass' : 'fail'}
                </td>
                <td className="py-2.5 pr-3">
                  <ul className="m-0 list-none p-0">
                    {c.checks.map((k, i) => (
                      <li key={i} className={k.ok ? 'text-text-2' : 'text-err'}>
                        <span className="font-mono text-[12.5px]">{k.type}</span>
                        {k.detail ? ` · ${k.detail}` : ''}
                      </li>
                    ))}
                  </ul>
                </td>
                <td className="py-2.5">
                  <ul className="m-0 list-none p-0">
                    {c.rubric.map((x, i) => (
                      <li key={i} className="text-text-2" title={x.justification}>
                        {x.score}/2 · {x.item}
                      </li>
                    ))}
                  </ul>
                </td>
              </tr>
            ))}
            {r.cases.length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-meta text-text-3">
                  {['queued', 'running'].includes(r.status) ? 'Running…' : 'No case result.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Evals (docs/09 §5): score history by model, cases expected vs got, run the suite. */
export function Evals() {
  const search = useSearch({ from: '/evals' })
  const navigate = useNavigate()
  const agents = useAgents()
  const skills = useSkills()
  const canRun = useCan('learning:apply')
  const qc = useQueryClient()
  const toast = useToast()
  const target = search.target ?? agents.data?.items[0]?.name ?? ''
  const runs = useEvalRuns(target || undefined)
  const [models, setModels] = useState<string[]>(['z-fast', 'z-heavy'])
  const [busy, setBusy] = useState(false)
  const list = useMemo(
    () => [...(runs.data?.items ?? [])].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)),
    [runs.data],
  )
  const byModel = new Map<string, EvalRun[]>()
  for (const r of list) byModel.set(r.model, [...(byModel.get(r.model) ?? []), r])
  const run = async () => {
    setBusy(true)
    try {
      const started = await unwrap(api.POST('/evals', { body: { target, models } }))
      await qc.invalidateQueries({ queryKey: qk.evals(target) })
      toast.ok(`Eval run started on ${models.join(', ')}`)
      const first = started[0]
      if (first) void navigate({ to: '/evals', search: { target, run: first.id } })
    } catch (e) {
      toast.error(
        e,
        isNotServed(e) ? 'Evals are not available on this server yet' : 'Could not start the evals',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="flex w-full max-w-[1320px] flex-col gap-7 px-[72px] pt-11 pb-14 max-lg:px-8 max-md:px-5">
      <PageTitle
        title="Evals"
        lead="Each agent and skill has a suite of cases with deterministic checks and a judged rubric. Runs compare models and gate every learning proposal."
      />
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1.5 text-meta text-text-2">
          Target
          <Select
            value={target}
            onChange={(e) => void navigate({ to: '/evals', search: { target: e.target.value } })}
            className="w-[260px]"
          >
            <optgroup label="Agents">
              {(agents.data?.items ?? []).map((a) => (
                <option key={a.id} value={a.name}>
                  {a.display_name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Skills">
              {(skills.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.name}>
                  {s.name}
                </option>
              ))}
            </optgroup>
          </Select>
        </label>
        {canRun && (
          <div className="flex items-center gap-4">
            {['z-fast', 'z-heavy'].map((m) => (
              <Checkbox
                key={m}
                checked={models.includes(m)}
                onChange={(v) => setModels(v ? [...models, m] : models.filter((x) => x !== m))}
                label={<span className="font-mono">{m}</span>}
              />
            ))}
            <Button
              variant="primary"
              busy={busy}
              disabled={!target || models.length === 0}
              onClick={() => void run()}
            >
              Run evals
            </Button>
          </div>
        )}
      </div>
      <QueryState query={runs} what="Eval runs" endpoint="GET /evals">
        {() =>
          list.length === 0 ? (
            <EmptyState icon="chart" title="No run yet for this target">
              Run the suite to see the score by model.
            </EmptyState>
          ) : (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
              <section
                aria-label="Score history"
                className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5"
              >
                <h2 className="m-0 text-[15px] font-semibold">Score history by model</h2>
                <Legend items={[...byModel.keys()].map((m) => ({ label: m }))} />
                {[...byModel.entries()].map(([m, rs]) => (
                  <div key={m} className="flex items-center gap-4">
                    <span className="w-20 font-mono">{m}</span>
                    <Sparkline
                      values={rs.map((r) =>
                        r.scores ? r.scores.cases_passed / Math.max(1, r.scores.cases_total) : 0,
                      )}
                      label={`${m} pass rate over ${rs.length} runs`}
                      width={160}
                    />
                    <span className="font-mono text-[12.5px]">
                      {score(rs[rs.length - 1] ?? rs[0] ?? ({} as EvalRun))}
                    </span>
                  </div>
                ))}
                <ul className="m-0 flex list-none flex-col p-0">
                  {[...list].reverse().map((r) => (
                    <li key={r.id}>
                      <button
                        type="button"
                        onClick={() => void navigate({ to: '/evals', search: { target, run: r.id } })}
                        aria-current={search.run === r.id ? 'true' : undefined}
                        className={`flex w-full items-center gap-3 rounded-ctl px-2 py-2 text-left ${search.run === r.id ? 'bg-raised' : 'hover:bg-raised'}`}
                      >
                        <span className="w-20 font-mono">{r.model}</span>
                        <span
                          className={r.passed ? 'text-ok' : r.passed === false ? 'text-err' : 'text-text-2'}
                        >
                          {score(r)}
                        </span>
                        <span className="ml-auto text-meta text-text-3">
                          v{r.target_version} · {relative(r.created_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              {search.run ? (
                <RunDetail id={search.run} />
              ) : list.at(-1) ? (
                <RunDetail id={list.at(-1)?.id ?? ''} />
              ) : null}
            </div>
          )
        }
      </QueryState>
    </main>
  )
}
