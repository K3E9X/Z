import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk, useAudit, useAuditSearches } from '@/api/queries'
import type { AuditSearch } from '@/api/types'
import { EmptyState, QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Field, Input, Select } from '@/components/ui'
import { dateTime } from '@/lib/format'

type Filters = AuditSearch['filters']

const TEXT_FIELDS: {
  key: 'actor' | 'action' | 'target_type' | 'target_id' | 'ip'
  label: string
  hint: string
}[] = [
  { key: 'actor', label: 'Actor', hint: 'alice' },
  { key: 'action', label: 'Action', hint: 'action.approved' },
  { key: 'target_type', label: 'Target type', hint: 'connector' },
  { key: 'target_id', label: 'Target id', hint: 'con_…' },
  { key: 'ip', label: 'IP', hint: '10.0.0.12' },
]

const LABEL: Record<keyof Filters, string> = {
  actor: 'actor',
  action: 'action',
  target_type: 'target type',
  target_id: 'target',
  result: 'result',
  ip: 'IP',
  from: 'from',
  to: 'to',
}

/** Only the filters that are set (the API and the saved search take no empty strings). */
function cleanFilters(f: Filters): Filters {
  const out: Filters = {}
  for (const [k, v] of Object.entries(f) as [keyof Filters, string | undefined][]) {
    const t = v?.trim()
    if (t) out[k] = t
  }
  return out
}

function summary(f: Filters): string {
  return (
    (Object.entries(f) as [keyof Filters, string][])
      .map(([k, v]) => `${LABEL[k]} ${k === 'from' || k === 'to' ? dateTime(v) : v}`)
      .join(' · ') || 'no filter'
  )
}

/** ISO timestamp → value of a datetime-local input (local time, minutes). */
function toLocalInput(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function fromLocalInput(v: string): string | undefined {
  if (!v) return undefined
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString()
}

/** Admin audit search (docs/12 §3): server-side filters and the user's saved searches. Needs audit:read. */
export function AuditSearchPanel() {
  const qc = useQueryClient()
  const toast = useToast()
  const saved = useAuditSearches()
  const [draft, setDraft] = useState<Filters>({})
  const [applied, setApplied] = useState<Filters>({})
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const audit = useAudit(applied)
  const current = cleanFilters(draft)
  const set = (patch: Filters) => setDraft((d) => ({ ...d, ...patch }))

  const save = async () => {
    setBusy('save')
    try {
      await unwrap(api.POST('/me/audit-searches', { body: { name: name.trim(), filters: current } }))
      await qc.invalidateQueries({ queryKey: qk.auditSearches })
      setApplied(current)
      setName('')
      toast.ok(`Search “${name.trim()}” saved`)
    } catch (e) {
      toast.error(e, 'Search not saved')
    } finally {
      setBusy(null)
    }
  }
  const remove = async (s: AuditSearch) => {
    setBusy(s.id)
    try {
      await unwrap(api.DELETE('/me/audit-searches/{id}', { params: { path: { id: s.id } } }))
      await qc.invalidateQueries({ queryKey: qk.auditSearches })
      toast.ok(`Search “${s.name}” deleted`)
    } catch (e) {
      toast.error(e, 'Search not deleted')
    } finally {
      setBusy(null)
    }
  }
  const apply = (f: Filters) => {
    setDraft(f)
    setApplied(cleanFilters(f))
  }

  return (
    <section aria-label="Search the audit log" className="flex flex-col gap-4">
      <h3 className="m-0 text-[15px] font-semibold">Search</h3>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          setApplied(current)
        }}
      >
        <div className="grid gap-3 sm:grid-cols-4">
          {TEXT_FIELDS.map((f) => (
            <Field key={f.key} label={f.label}>
              {(id) => (
                <Input
                  id={id}
                  value={draft[f.key] ?? ''}
                  onChange={(e) => set({ [f.key]: e.target.value })}
                  placeholder={f.hint}
                  className="font-mono text-sm"
                />
              )}
            </Field>
          ))}
          <Field label="Result">
            {(id) => (
              <Select
                id={id}
                value={draft.result ?? ''}
                onChange={(e) => set({ result: e.target.value || undefined })}
              >
                <option value="">Any</option>
                <option value="success">Success</option>
                <option value="denied">Denied</option>
                <option value="error">Error</option>
              </Select>
            )}
          </Field>
          <Field label="From">
            {(id) => (
              <Input
                id={id}
                type="datetime-local"
                value={toLocalInput(draft.from)}
                onChange={(e) => set({ from: fromLocalInput(e.target.value) })}
              />
            )}
          </Field>
          <Field label="To">
            {(id) => (
              <Input
                id={id}
                type="datetime-local"
                value={toLocalInput(draft.to)}
                onChange={(e) => set({ to: fromLocalInput(e.target.value) })}
              />
            )}
          </Field>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Button type="submit" variant="primary">
            Search
          </Button>
          <Button onClick={() => apply({})}>Clear</Button>
          <span className="flex-1" />
          <Input
            aria-label="Name of this search"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this search"
            className="w-[220px]"
          />
          <Button
            busy={busy === 'save'}
            disabled={!name.trim() || Object.keys(current).length === 0}
            onClick={() => void save()}
          >
            Save this search
          </Button>
        </div>
      </form>

      <div className="flex flex-col gap-1.5">
        <h4 className="m-0 text-meta font-normal text-text-3">Saved searches</h4>
        <QueryState query={saved} what="Saved searches" endpoint="GET /me/audit-searches" compact>
          {() =>
            (saved.data ?? []).length === 0 ? (
              <span className="text-meta text-text-3">
                None yet. Set filters, name the search and save it.
              </span>
            ) : (
              <ul className="m-0 list-none p-0">
                {(saved.data ?? []).map((s) => (
                  <li key={s.id} className="flex items-center gap-3 border-b border-line py-2 text-ui">
                    <span className="font-medium">{s.name}</span>
                    <span className="min-w-0 flex-1 truncate text-meta text-text-2">
                      {summary(s.filters)}
                    </span>
                    <Button size="xs" aria-label={`Apply ${s.name}`} onClick={() => apply(s.filters)}>
                      Apply
                    </Button>
                    <Button
                      size="xs"
                      variant="danger"
                      aria-label={`Delete ${s.name}`}
                      busy={busy === s.id}
                      onClick={() => void remove(s)}
                    >
                      Delete
                    </Button>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryState>
      </div>

      <QueryState query={audit} what="Audit events" endpoint="GET /audit">
        {() => {
          const items = audit.data?.items ?? []
          if (items.length === 0)
            return (
              <EmptyState icon="history" title="No event matches">
                {summary(applied)}
              </EmptyState>
            )
          return (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-ui" aria-label="Matching events">
                <thead>
                  <tr className="border-y border-line text-left text-sm text-text-3">
                    <th className="px-3 py-2 font-normal">#</th>
                    <th className="py-2 pr-3 font-normal">Time (UTC)</th>
                    <th className="py-2 pr-3 font-normal">Actor</th>
                    <th className="py-2 pr-3 font-normal">Action</th>
                    <th className="py-2 pr-3 font-normal">Target</th>
                    <th className="py-2 pr-3 font-normal">Result</th>
                    <th className="py-2 pr-3 font-normal">IP</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((e) => (
                    <tr key={e.seq} className="border-b border-line align-top">
                      <td className="px-3 py-2 font-mono text-[12.5px] text-text-3">{e.seq}</td>
                      <td className="py-2 pr-3 font-mono text-[12.5px] whitespace-nowrap">
                        {dateTime(e.ts, { utc: true })}
                      </td>
                      <td className="py-2 pr-3">{e.actor}</td>
                      <td className="py-2 pr-3 font-mono text-[12.5px]">{e.action}</td>
                      <td className="py-2 pr-3 font-mono text-[12.5px] break-all">
                        {e.target_id ? `${e.target_type}/${e.target_id}` : e.target_type}
                      </td>
                      <td className={`py-2 pr-3 ${e.result === 'success' ? '' : 'text-err'}`}>{e.result}</td>
                      <td className="py-2 pr-3 font-mono text-[12.5px]">{e.actor_ip ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {audit.data?.next_cursor && (
                <p className="m-0 mt-2 text-meta text-text-3">
                  Showing the latest {items.length}. Narrow the filters to see older events.
                </p>
              )}
            </div>
          )
        }}
      </QueryState>
    </section>
  )
}
