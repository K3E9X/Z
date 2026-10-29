import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk } from '@/api/queries'
import type { Connector, ConnectorDraft, ConnectorTestResult, DataClass, Operation } from '@/api/types'
import { Icon } from '@/components/Icon'
import { NotServed } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Field, Input, PageTitle, Select, Textarea } from '@/components/ui'
import { useCan } from '@/lib/auth'
import { ms } from '@/lib/format'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'
import { ADD_KINDS } from './Connectors'

type Step = 'source' | 'operations' | 'credentials' | 'test' | 'done'
const STEPS: { id: Step; label: string }[] = [
  { id: 'source', label: 'Source' },
  { id: 'operations', label: 'Operations' },
  { id: 'credentials', label: 'Credentials' },
  { id: 'test', label: 'Test and save' },
]
const RANK: Record<string, number> = { read: 0, write: 1, destructive: 2 }

function Stepper({ step }: { step: Step }) {
  const idx = STEPS.findIndex((s) => s.id === step)
  return (
    <ol className="m-0 flex list-none flex-wrap gap-6 p-0" aria-label="Steps">
      {STEPS.map((s, i) => (
        <li key={s.id} aria-current={s.id === step ? 'step' : undefined} className={`flex items-center gap-2 ${i <= idx ? 'text-text' : 'text-text-3'}`}>
          <span className={`flex h-6 w-6 items-center justify-center rounded-full border font-mono text-sm ${i < idx ? 'border-ok text-ok' : i === idx ? 'border-text' : 'border-line-2'}`}>{i < idx ? <Icon name="check" size={12} /> : i + 1}</span>
          {s.label}
        </li>
      ))}
    </ol>
  )
}

/** Connector builder (docs/09 §5, 05 §6): source → operations with proposed classes → credentials → test → save. */
export function ConnectorBuilder() {
  const search = useSearch({ from: '/connectors/new' })
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const canManage = useCan('connectors:manage')
  const kind = search.kind ?? 'openapi'
  const kindLabel = ADD_KINDS.find((k) => k.kind === kind)?.label ?? kind
  const [step, setStep] = useState<Step>('source')
  const [name, setName] = useState('')
  const [display, setDisplay] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [specUrl, setSpecUrl] = useState('')
  const [spec, setSpec] = useState('')
  const [docs, setDocs] = useState('')
  const [mcpCmd, setMcpCmd] = useState('')
  const [dataClass, setDataClass] = useState<DataClass>('C2')
  const [draft, setDraft] = useState<ConnectorDraft | null>(null)
  const [ops, setOps] = useState<Operation[]>([])
  const [readSecret, setReadSecret] = useState('')
  const [writeSecret, setWriteSecret] = useState('')
  const [authType, setAuthType] = useState('bearer')
  const [created, setCreated] = useState<Connector | null>(null)
  const [test, setTest] = useState<ConnectorTestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [notServed, setNotServed] = useState<string | null>(null)

  const source = kind === 'mcp' ? 'mcp' : kind === 'graphql' ? 'graphql' : spec || specUrl ? 'openapi' : 'docs'

  const importDraft = async () => {
    setBusy(true)
    setNotServed(null)
    try {
      const d = await unwrap(
        api.POST('/connectors/import', {
          body: {
            source,
            name,
            display_name: display || name,
            ...(baseUrl ? { base_url: baseUrl } : {}),
            ...(specUrl ? { spec_url: specUrl } : {}),
            ...(spec ? { spec } : {}),
            ...(docs ? { docs } : {}),
            ...(kind === 'mcp' && mcpCmd ? { mcp: (mcpCmd.startsWith('http') ? { transport: 'http', url: mcpCmd } : { transport: 'stdio', command: mcpCmd }) as never } : {}),
            data_class: dataClass,
          },
        }),
      )
      setDraft(d)
      setOps(d.operations.map((o) => ({ ...o, exposed: o.class === 'read' && o.proposed_exposed })))
      setStep('operations')
    } catch (e) {
      if (isNotServed(e)) setNotServed('POST /connectors/import')
      else toast.error(e, 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  const record = async () => {
    setBusy(true)
    try {
      const s = await unwrap(api.POST('/connectors/record-browser', { body: { connector: name, start_url: baseUrl, name: display || name } }))
      window.open(s.viewer_url, '_blank', 'noopener,noreferrer')
      toast.info('Recording in a sandboxed browser. The flow becomes a named operation when you stop.')
    } catch (e) {
      if (isNotServed(e)) setNotServed('POST /connectors/record-browser')
      else toast.error(e)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!draft) return
    setBusy(true)
    try {
      const manifest = {
        ...draft.manifest,
        auth: {
          ...(readSecret ? { read: { type: authType, secret: readSecret } } : {}),
          ...(writeSecret ? { write: { type: authType, secret: writeSecret } } : {}),
        },
      }
      const c = await stepUp.run(() => unwrap(api.POST('/connectors', { body: manifest as never })), { what: 'Adding a connector', detail: `${draft.manifest.name} · ${ops.filter((o) => o.exposed).length} operations to expose` })
      setCreated(c)
      await unwrap(api.POST('/connectors/{id}/approve-exposure', { params: { path: { id: c.id } }, body: { operations: ops.map((o) => ({ id: o.id, exposed: o.exposed })) } }))
      await qc.invalidateQueries({ queryKey: qk.connectors })
      try {
        setTest(await unwrap(api.POST('/connectors/{id}/test', { params: { path: { id: c.id } }, body: {} })))
      } catch (e) {
        if (!isNotServed(e)) toast.error(e, 'Connection test failed')
      }
      setStep('done')
    } catch (e) {
      if (isNotServed(e)) setNotServed('POST /connectors')
      else if (!(e instanceof StepUpCancelled)) toast.error(e, 'Connector not saved')
    } finally {
      setBusy(false)
    }
  }

  if (!canManage) {
    return (
      <main className="mx-auto w-full max-w-[860px] px-10 py-12">
        <PageTitle title="Add a connector" lead="Adding connectors needs the connectors:manage permission (admin)." />
      </main>
    )
  }
  return (
    <main className="mx-auto flex w-full max-w-[980px] flex-col gap-8 px-10 pt-12 pb-16 max-md:px-5">
      <PageTitle title="Add a connector" lead={`${kindLabel}. Agents get read operations only; writes stay proposals executed with a separate credential.`} />
      <div className="flex flex-wrap gap-1.5">
        {ADD_KINDS.map((k) => (
          <button key={k.kind} type="button" aria-pressed={k.kind === kind} onClick={() => void navigate({ to: '/connectors/new', search: { kind: k.kind }, replace: true })} className={`h-8 rounded-btn border px-3 ${k.kind === kind ? 'border-text' : 'border-line-2 text-text-2 hover:text-text'}`}>
            {k.label}
          </button>
        ))}
      </div>
      <Stepper step={step === 'done' ? 'test' : step} />
      {notServed && <NotServed what="This step" endpoint={notServed} />}

      {step === 'source' && (
        <section aria-label="Source" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name (slug)">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))} className="font-mono" placeholder="logos" />}</Field>
            <Field label="Display name">{(id) => <Input id={id} value={display} onChange={(e) => setDisplay(e.target.value)} placeholder="Logos" />}</Field>
            <Field label="Data class" hint="Of what the tool returns">
              {(id, d) => (
                <Select id={id} aria-describedby={d} value={dataClass} onChange={(e) => setDataClass(e.target.value as DataClass)}>
                  {['C0', 'C1', 'C2', 'C3'].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <Field label={kind === 'browser' ? 'Start URL of the web app' : 'Base URL'} hint="Admin-entered URLs are checked against loopback, link-local and cluster ranges by the server">
            {(id, d) => <Input id={id} aria-describedby={d} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="font-mono text-sm" placeholder="https://logos.example.internal" />}
          </Field>
          {(kind === 'openapi' || kind === 'graphql') && (
            <>
              <Field label={kind === 'graphql' ? 'GraphQL endpoint (introspection)' : 'Spec URL'}>{(id) => <Input id={id} value={specUrl} onChange={(e) => setSpecUrl(e.target.value)} className="font-mono text-sm" />}</Field>
              {kind === 'openapi' && <Field label="…or paste the spec (JSON or YAML)">{(id) => <Textarea id={id} rows={6} value={spec} onChange={(e) => setSpec(e.target.value)} className="font-mono text-[12px]" />}</Field>}
            </>
          )}
          {kind === 'mcp' && <Field label="MCP server" hint="A command (stdio sidecar) or a streamable HTTP URL">{(id, d) => <Input id={id} aria-describedby={d} value={mcpCmd} onChange={(e) => setMcpCmd(e.target.value)} className="font-mono text-sm" placeholder="npx -y @example/mcp-server   or   https://mcp.example.internal/mcp" />}</Field>}
          {(kind === 'cli' || kind === 'database' || kind === 'webhook') && (
            <Field label="API documentation or notes" hint="The builder agent drafts the declared operations from it; you review every one">
              {(id, d) => <Textarea id={id} aria-describedby={d} rows={6} value={docs} onChange={(e) => setDocs(e.target.value)} />}
            </Field>
          )}
          <div className="flex gap-2">
            {kind === 'browser' ? (
              <Button variant="primary" busy={busy} disabled={!name || !baseUrl} onClick={() => void record()}>
                Record a flow
              </Button>
            ) : (
              <Button variant="primary" busy={busy} disabled={!name} onClick={() => void importDraft()}>
                List operations
              </Button>
            )}
          </div>
        </section>
      )}

      {step === 'operations' && draft && (
        <section aria-label="Operations" className="flex flex-col gap-4">
          {draft.warnings?.map((w) => (
            <div key={w} className="rounded-panel bg-wait-bg px-4 py-2.5 text-meta text-wait">
              {w}
            </div>
          ))}
          <p className="m-0 text-text-2">Proposed classes come from the platform classifier. You can raise a class, never lower it. Only reads can be exposed to agents; writes become proposals.</p>
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full min-w-[700px] border-collapse text-ui">
              <thead>
                <tr className="border-b border-line text-sm text-text-3">
                  <th className="px-4 py-2.5 font-normal">Operation</th>
                  <th className="py-2.5 pr-3 font-normal">Source</th>
                  <th className="py-2.5 pr-3 font-normal">Class</th>
                  <th className="py-2.5 pr-4 font-normal">Exposed</th>
                </tr>
              </thead>
              <tbody>
                {ops.map((o, i) => (
                  <tr key={o.id} className="border-b border-line last:border-b-0">
                    <td className="px-4 py-2.5 font-mono text-[12.5px]">{o.id}</td>
                    <td className="pr-3 font-mono text-[12.5px] text-text-2">{o.source_ref}</td>
                    <td className="pr-3">
                      <Select aria-label={`Class of ${o.id}`} value={o.class} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, class: e.target.value as Operation['class'], exposed: e.target.value === 'read' ? x.exposed : x.exposed } : x)))} className="h-8 w-[140px]">
                        {(['read', 'write', 'destructive'] as const)
                          .filter((c) => (RANK[c] ?? 0) >= (RANK[o.classifier_class] ?? 0))
                          .map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                      </Select>
                    </td>
                    <td className="pr-4">
                      <input type="checkbox" aria-label={`Expose ${o.id}`} checked={o.exposed} onChange={(e) => setOps(ops.map((x, j) => (j === i ? { ...x, exposed: e.target.checked } : x)))} />
                      {o.exposed && o.class === 'destructive' && <span className="ml-2 text-sm text-err">two approvers</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => setStep('source')}>Back</Button>
            <Button variant="primary" onClick={() => setStep('credentials')}>
              Continue
            </Button>
          </div>
        </section>
      )}

      {step === 'credentials' && (
        <section aria-label="Credentials" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
          <p className="m-0 text-text-2">References to your secrets backend, never values. The read credential goes to the tool gateway; the write credential only to the action executor.</p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Auth type">
              {(id) => (
                <Select id={id} value={authType} onChange={(e) => setAuthType(e.target.value)}>
                  {['bearer', 'api_key', 'basic', 'oauth', 'mtls', 'none'].map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Read secret reference">{(id) => <Input id={id} value={readSecret} onChange={(e) => setReadSecret(e.target.value)} placeholder="env:LOGOS_TOKEN_RO" className="font-mono text-sm" />}</Field>
            <Field label="Write secret reference">{(id) => <Input id={id} value={writeSecret} onChange={(e) => setWriteSecret(e.target.value)} placeholder="vault:kv/z/logos#rw" className="font-mono text-sm" />}</Field>
          </div>
          {readSecret && writeSecret && readSecret === writeSecret && <div className="text-meta text-err">Read and write must be different credentials.</div>}
          <div className="flex gap-2">
            <Button onClick={() => setStep('operations')}>Back</Button>
            <Button variant="primary" disabled={!!readSecret && readSecret === writeSecret} onClick={() => setStep('test')}>
              Continue
            </Button>
          </div>
        </section>
      )}

      {step === 'test' && (
        <section aria-label="Test and save" className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5">
          <p className="m-0 text-text-2">
            Saving creates <span className="font-mono">{draft?.manifest.name}</span>, confirms the exposure of {ops.filter((o) => o.exposed).length} operations, then runs a test call with the read credential.
          </p>
          <div className="flex gap-2">
            <Button onClick={() => setStep('credentials')}>Back</Button>
            <Button variant="primary" busy={busy} onClick={() => void save()}>
              Re-authenticate, save and test
            </Button>
          </div>
        </section>
      )}

      {step === 'done' && created && (
        <section aria-label="Saved" className="flex flex-col gap-3 rounded-card border border-line bg-panel px-6 py-5">
          <span className="flex items-center gap-2 font-medium text-ok">
            <Icon name="check" size={15} /> {created.display_name} saved
          </span>
          {test ? (
            <span className={test.ok ? 'text-ok' : 'text-err'}>
              Test {test.ok ? 'passed' : 'failed'} · {ms(test.latency_ms)} · {test.detail}
            </span>
          ) : (
            <span className="text-meta text-text-3">Connection tests are not available on this server yet.</span>
          )}
          <Button className="self-start" onClick={() => void navigate({ to: '/connectors', search: { connector: created.id } })}>
            Open the connector
          </Button>
        </section>
      )}
    </main>
  )
}
