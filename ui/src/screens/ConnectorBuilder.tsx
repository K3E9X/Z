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
/** Starting point for a browser connector: one read flow behind a form login (docs/05 §1). */
const FLOW_TEMPLATE = JSON.stringify(
  {
    declared: [
      {
        id: 'records.search',
        description: 'Records whose name contains a text',
        flow: 'search',
        input_schema: {
          type: 'object',
          required: ['q'],
          properties: { q: { type: 'string', maxLength: 64 } },
        },
      },
    ],
    flows: {
      search: {
        start_url: 'login',
        steps: [
          { action: 'fill', selector: '#username', value: '{{auth.username}}' },
          { action: 'fill', selector: '#password', value: '{{auth.password}}' },
          { action: 'click', selector: 'button[type=submit]' },
          { action: 'wait_for_url', url: '**/home' },
          { action: 'goto', url: 'records?q={{args.q}}' },
          { action: 'extract_table', selector: 'table', name: 'records' },
        ],
      },
    },
  },
  null,
  2,
)

/** Starting point for a cli connector (ADR 0025): one read command; arguments never start with '-'. */
const CLI_TEMPLATE = JSON.stringify(
  {
    cli: {
      image: 'registry.example.internal/tools/glab@sha256:<64 hex digits>',
      env: { GITLAB_HOST: 'gitlab.example.internal', GITLAB_TOKEN: '{{auth.secret}}' },
      output: 'json',
      timeout_s: 30,
    },
    declared: [
      {
        id: 'merge_requests.list',
        description: 'Open merge requests of a project',
        command: ['glab', 'api', 'projects/{{args.project_id}}/merge_requests?state=opened'],
        input_schema: {
          type: 'object',
          required: ['project_id'],
          properties: { project_id: { type: 'integer', minimum: 1 } },
        },
      },
    ],
  },
  null,
  2,
)

function Stepper({ step }: { step: Step }) {
  const idx = STEPS.findIndex((s) => s.id === step)
  return (
    <ol className="m-0 flex list-none flex-wrap gap-6 p-0" aria-label="Steps">
      {STEPS.map((s, i) => (
        <li
          key={s.id}
          aria-current={s.id === step ? 'step' : undefined}
          className={`flex items-center gap-2 ${i <= idx ? 'text-text' : 'text-text-3'}`}
        >
          <span
            className={`flex h-6 w-6 items-center justify-center rounded-full border font-mono text-sm ${i < idx ? 'border-ok text-ok' : i === idx ? 'border-text' : 'border-line-2'}`}
          >
            {i < idx ? <Icon name="check" size={12} /> : i + 1}
          </span>
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
  const [flowsJson, setFlowsJson] = useState(FLOW_TEMPLATE)
  const [cliJson, setCliJson] = useState(CLI_TEMPLATE)
  const [readUser, setReadUser] = useState('')
  const [writeUser, setWriteUser] = useState('')
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

  const source =
    kind === 'browser' || kind === 'cli'
      ? kind
      : kind === 'mcp'
        ? 'mcp'
        : kind === 'graphql'
          ? 'graphql'
          : spec || specUrl
            ? 'openapi'
            : 'docs'

  const browserBody = (): { flows: never; declared: never } | null => {
    try {
      const parsed = JSON.parse(flowsJson) as { flows?: unknown; declared?: unknown }
      if (!parsed.flows || !Array.isArray(parsed.declared)) throw new Error('missing')
      return { flows: parsed.flows as never, declared: parsed.declared as never }
    } catch {
      toast.error(new Error('The flows must be JSON with "declared" (a list) and "flows" (an object)'))
      return null
    }
  }

  const cliBody = (): { cli: never; declared: never } | null => {
    try {
      const parsed = JSON.parse(cliJson) as { cli?: unknown; declared?: unknown }
      if (!parsed.cli || !Array.isArray(parsed.declared)) throw new Error('missing')
      return { cli: parsed.cli as never, declared: parsed.declared as never }
    } catch {
      toast.error(new Error('The tool must be JSON with "cli" (an object) and "declared" (a list)'))
      return null
    }
  }

  const importDraft = async () => {
    const flows = kind === 'browser' ? browserBody() : kind === 'cli' ? cliBody() : null
    if ((kind === 'browser' || kind === 'cli') && !flows) return
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
            ...(kind === 'mcp' && mcpCmd
              ? {
                  mcp: (mcpCmd.startsWith('http')
                    ? { transport: 'http', url: mcpCmd }
                    : { transport: 'stdio', command: mcpCmd }) as never,
                }
              : {}),
            ...(flows ?? {}),
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

  const credential = (secret: string, username: string) =>
    kind === 'browser'
      ? { type: 'basic', secret, ...(username ? { username } : {}) }
      : { type: authType, secret }

  const save = async () => {
    if (!draft) return
    setBusy(true)
    try {
      const manifest = {
        ...draft.manifest,
        // nothing entered: keep the references the draft proposes (env:<NAME>_..._RO / _RW)
        auth:
          readSecret || writeSecret
            ? {
                ...(readSecret ? { read: credential(readSecret, readUser) } : {}),
                ...(writeSecret ? { write: credential(writeSecret, writeUser) } : {}),
              }
            : draft.manifest.auth,
      }
      const c = await stepUp.run(() => unwrap(api.POST('/connectors', { body: manifest as never })), {
        what: 'Adding a connector',
        detail: `${draft.manifest.name} · ${ops.filter((o) => o.exposed).length} operations to expose`,
      })
      setCreated(c)
      await unwrap(
        api.POST('/connectors/{id}/approve-exposure', {
          params: { path: { id: c.id } },
          body: { operations: ops.map((o) => ({ id: o.id, exposed: o.exposed })) },
        }),
      )
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
        <PageTitle
          title="Add a connector"
          lead="Adding connectors needs the connectors:manage permission (admin)."
        />
      </main>
    )
  }
  return (
    <main className="mx-auto flex w-full max-w-[980px] flex-col gap-8 px-10 pt-12 pb-16 max-md:px-5">
      <PageTitle
        title="Add a connector"
        lead={`${kindLabel}. Agents get read operations only; writes stay proposals executed with a separate credential.`}
      />
      <div className="flex flex-wrap gap-1.5">
        {ADD_KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            aria-pressed={k.kind === kind}
            onClick={() => void navigate({ to: '/connectors/new', search: { kind: k.kind }, replace: true })}
            className={`h-8 rounded-btn border px-3 ${k.kind === kind ? 'border-text' : 'border-line-2 text-text-2 hover:text-text'}`}
          >
            {k.label}
          </button>
        ))}
      </div>
      <Stepper step={step === 'done' ? 'test' : step} />
      {notServed && <NotServed what="This step" endpoint={notServed} />}

      {step === 'source' && (
        <section
          aria-label="Source"
          className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5"
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name (slug)">
              {(id) => (
                <Input
                  id={id}
                  value={name}
                  onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                  className="font-mono"
                  placeholder="logos"
                />
              )}
            </Field>
            <Field label="Display name">
              {(id) => (
                <Input
                  id={id}
                  value={display}
                  onChange={(e) => setDisplay(e.target.value)}
                  placeholder="Logos"
                />
              )}
            </Field>
            <Field label="Data class" hint="Of what the tool returns">
              {(id, d) => (
                <Select
                  id={id}
                  aria-describedby={d}
                  value={dataClass}
                  onChange={(e) => setDataClass(e.target.value as DataClass)}
                >
                  {['C0', 'C1', 'C2', 'C3'].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          {kind !== 'cli' && (
            <Field
              label={kind === 'browser' ? 'Base URL of the web app' : 'Base URL'}
              hint="Admin-entered URLs are checked against loopback, link-local and cluster ranges by the server"
            >
              {(id, d) => (
                <Input
                  id={id}
                  aria-describedby={d}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  className="font-mono text-sm"
                  placeholder="https://logos.example.internal"
                />
              )}
            </Field>
          )}
          {(kind === 'openapi' || kind === 'graphql') && (
            <>
              <Field label={kind === 'graphql' ? 'GraphQL endpoint (introspection)' : 'Spec URL'}>
                {(id) => (
                  <Input
                    id={id}
                    value={specUrl}
                    onChange={(e) => setSpecUrl(e.target.value)}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              {kind === 'openapi' && (
                <Field label="…or paste the spec (JSON or YAML)">
                  {(id) => (
                    <Textarea
                      id={id}
                      rows={6}
                      value={spec}
                      onChange={(e) => setSpec(e.target.value)}
                      className="font-mono text-[12px]"
                    />
                  )}
                </Field>
              )}
            </>
          )}
          {kind === 'mcp' && (
            <Field label="MCP server" hint="A command (stdio sidecar) or a streamable HTTP URL">
              {(id, d) => (
                <Input
                  id={id}
                  aria-describedby={d}
                  value={mcpCmd}
                  onChange={(e) => setMcpCmd(e.target.value)}
                  className="font-mono text-sm"
                  placeholder="npx -y @example/mcp-server   or   https://mcp.example.internal/mcp"
                />
              )}
            </Field>
          )}
          {(kind === 'database' || kind === 'webhook') && (
            <Field
              label="API documentation or notes"
              hint="The builder agent drafts the declared operations from it; you review every one"
            >
              {(id, d) => (
                <Textarea
                  id={id}
                  aria-describedby={d}
                  rows={6}
                  value={docs}
                  onChange={(e) => setDocs(e.target.value)}
                />
              )}
            </Field>
          )}
          {kind === 'cli' && (
            <Field
              label="Tool image and commands (JSON)"
              hint="The image is pinned by digest and allowed in executor.tool_images. Each operation's command is an argv (no shell); {{args.x}} fills a value that can never start with '-'. Credentials only reach the tool through cli.env ({{auth.secret}}, {{auth.username}}, {{auth.password}})."
            >
              {(id, d) => (
                <Textarea
                  id={id}
                  aria-describedby={d}
                  rows={16}
                  value={cliJson}
                  onChange={(e) => setCliJson(e.target.value)}
                  className="font-mono text-[12px]"
                  spellCheck={false}
                />
              )}
            </Field>
          )}
          {kind === 'browser' && (
            <Field
              label="Flows and operations (JSON)"
              hint="Each operation names a flow. Arguments ({{args.x}}) only fill values and URLs; {{auth.username}} and {{auth.password}} are typed by the worker. Steps: goto, click, fill, select, check, press, wait_for, wait_for_url, extract_text, extract_table, extract_attribute, assert_text, assert_visible, screenshot."
            >
              {(id, d) => (
                <Textarea
                  id={id}
                  aria-describedby={d}
                  rows={16}
                  value={flowsJson}
                  onChange={(e) => setFlowsJson(e.target.value)}
                  className="font-mono text-[12px]"
                  spellCheck={false}
                />
              )}
            </Field>
          )}
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              busy={busy}
              disabled={!name || (kind === 'browser' && !baseUrl)}
              onClick={() => void importDraft()}
            >
              List operations
            </Button>
            {kind === 'browser' && (
              <Button disabled title="Record the flow in a sandboxed browser instead of writing it">
                Record in a browser · soon
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
          <p className="m-0 text-text-2">
            Proposed classes come from the platform classifier. You can raise a class, never lower it. Only
            reads can be exposed to agents; writes become proposals.
          </p>
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
                      <Select
                        aria-label={`Class of ${o.id}`}
                        value={o.class}
                        onChange={(e) =>
                          setOps(
                            ops.map((x, j) =>
                              j === i
                                ? {
                                    ...x,
                                    class: e.target.value as Operation['class'],
                                    exposed: e.target.value === 'read' ? x.exposed : x.exposed,
                                  }
                                : x,
                            ),
                          )
                        }
                        className="h-8 w-[140px]"
                      >
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
                      <input
                        type="checkbox"
                        aria-label={`Expose ${o.id}`}
                        checked={o.exposed}
                        onChange={(e) =>
                          setOps(ops.map((x, j) => (j === i ? { ...x, exposed: e.target.checked } : x)))
                        }
                      />
                      {o.exposed && o.class === 'destructive' && (
                        <span className="ml-2 text-sm text-err">two approvers</span>
                      )}
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
        <section
          aria-label="Credentials"
          className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5"
        >
          <p className="m-0 text-text-2">
            References to your secrets backend, never values. The read credential goes to the tool gateway;
            the write credential only to the action executor.
          </p>
          {kind === 'browser' ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Read account (user name)" hint="A read-only account of the web app">
                {(id, d) => (
                  <Input
                    id={id}
                    aria-describedby={d}
                    value={readUser}
                    onChange={(e) => setReadUser(e.target.value)}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Read password reference">
                {(id) => (
                  <Input
                    id={id}
                    value={readSecret}
                    onChange={(e) => setReadSecret(e.target.value)}
                    placeholder="env:FWCONSOLE_READ_PASSWORD"
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Write account (user name)" hint="Used only by the action executor">
                {(id, d) => (
                  <Input
                    id={id}
                    aria-describedby={d}
                    value={writeUser}
                    onChange={(e) => setWriteUser(e.target.value)}
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Write password reference">
                {(id) => (
                  <Input
                    id={id}
                    value={writeSecret}
                    onChange={(e) => setWriteSecret(e.target.value)}
                    placeholder="vault:kv/z/fwconsole#rw"
                    className="font-mono text-sm"
                  />
                )}
              </Field>
            </div>
          ) : (
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
              <Field label="Read secret reference">
                {(id) => (
                  <Input
                    id={id}
                    value={readSecret}
                    onChange={(e) => setReadSecret(e.target.value)}
                    placeholder="env:LOGOS_TOKEN_RO"
                    className="font-mono text-sm"
                  />
                )}
              </Field>
              <Field label="Write secret reference">
                {(id) => (
                  <Input
                    id={id}
                    value={writeSecret}
                    onChange={(e) => setWriteSecret(e.target.value)}
                    placeholder="vault:kv/z/logos#rw"
                    className="font-mono text-sm"
                  />
                )}
              </Field>
            </div>
          )}
          {readSecret && writeSecret && readSecret === writeSecret && (
            <div className="text-meta text-err">Read and write must be different credentials.</div>
          )}
          <div className="flex gap-2">
            <Button onClick={() => setStep('operations')}>Back</Button>
            <Button
              variant="primary"
              disabled={!!readSecret && readSecret === writeSecret}
              onClick={() => setStep('test')}
            >
              Continue
            </Button>
          </div>
        </section>
      )}

      {step === 'test' && (
        <section
          aria-label="Test and save"
          className="flex flex-col gap-4 rounded-card border border-line bg-panel px-6 py-5"
        >
          <p className="m-0 text-text-2">
            Saving creates <span className="font-mono">{draft?.manifest.name}</span>, confirms the exposure of{' '}
            {ops.filter((o) => o.exposed).length} operations, then runs a test call with the read credential.
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
        <section
          aria-label="Saved"
          className="flex flex-col gap-3 rounded-card border border-line bg-panel px-6 py-5"
        >
          <span className="flex items-center gap-2 font-medium text-ok">
            <Icon name="check" size={15} /> {created.display_name} saved
          </span>
          {test ? (
            <span className={test.ok ? 'text-ok' : 'text-err'}>
              Test {test.ok ? 'passed' : 'failed'} · {ms(test.latency_ms)} · {test.detail}
            </span>
          ) : (
            <span className="text-meta text-text-3">
              Connection tests are not available on this server yet.
            </span>
          )}
          <Button
            className="self-start"
            onClick={() => void navigate({ to: '/connectors', search: { connector: created.id } })}
          >
            Open the connector
          </Button>
        </section>
      )}
    </main>
  )
}
