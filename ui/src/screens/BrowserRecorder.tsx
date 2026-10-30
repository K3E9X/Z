import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from '@tanstack/react-router'
import { type MouseEvent, useEffect, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk } from '@/api/queries'
import type { BrowserRecordingInput } from '@/api/types'
import { QueryState } from '@/components/states'
import { useToast } from '@/components/toast'
import { Button, Field, Input, Textarea } from '@/components/ui'
import { Dialog } from '@/components/Dialog'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'
import { TopBar } from '@/shell/TopBar'

type Mode = 'click' | 'text' | 'table' | 'link' | 'assert'
type Step = { action: string; selector?: string; value?: string; url?: string; key?: string; name?: string }

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'click', label: 'Click', hint: 'Clicks the element under the pointer and records it' },
  { id: 'text', label: 'Extract text', hint: 'Records the text of the element as an output' },
  { id: 'table', label: 'Extract table', hint: 'Records the table under the pointer as rows' },
  { id: 'link', label: 'Extract link', hint: 'Records the href of the link' },
  { id: 'assert', label: 'Check visible', hint: 'The replay fails if this element is missing' },
]

function describe(s: Step): string {
  if (s.action === 'goto' || s.action === 'wait_for_url') return `${s.action} ${s.url ?? ''}`
  if (s.action === 'press') return `press ${s.key ?? ''}`
  if (s.action === 'screenshot') return `screenshot ${s.name ?? ''}`
  const value = s.value ? ` = ${s.value}` : ''
  const name = s.name ? ` → ${s.name}` : ''
  return `${s.action} ${s.selector ?? ''}${value}${name}`
}

/** Record a browser flow by demonstration (docs/05 §6, ADR 0016): the page is live, every action is a step. */
export function BrowserRecorder() {
  const { id } = useParams({ from: '/connectors/record/$id' })
  const navigate = useNavigate()
  const toast = useToast()
  const stepUp = useStepUp()
  const qc = useQueryClient()
  const state = useQuery({
    queryKey: ['recording', id],
    queryFn: () => unwrap(api.GET('/connectors/recordings/{id}', { params: { path: { id } } })),
    retry: false,
    refetchInterval: 5000,
  })
  const [nonce, setNonce] = useState(0)
  const [mode, setMode] = useState<Mode>('click')
  const [output, setOutput] = useState('')
  const [text, setText] = useState('')
  const [arg, setArg] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<{ flow: unknown } | null>(null)
  const [flowName, setFlowName] = useState('recorded')
  const [opId, setOpId] = useState('')
  const [opDescription, setOpDescription] = useState('')
  useEffect(() => {
    const t = setInterval(() => setNonce((n) => n + 1), 5000)
    return () => clearInterval(t)
  }, [])

  const send = async (body: BrowserRecordingInput) => {
    setBusy(true)
    try {
      const next = await unwrap(
        api.POST('/connectors/recordings/{id}/input', { params: { path: { id } }, body }),
      )
      qc.setQueryData(['recording', id], next)
      setNonce((n) => n + 1)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  const onScreen = (e: MouseEvent<HTMLImageElement>) => {
    const vp = state.data?.viewport
    if (!vp || busy) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * vp.width
    const y = ((e.clientY - rect.top) / rect.height) * vp.height
    if (mode === 'click') void send({ kind: 'click', x, y })
    else if (mode === 'assert') void send({ kind: 'assert', x, y })
    else if (!output.trim()) toast.error(new Error('Name the extracted value first'))
    else void send({ kind: 'extract', what: mode, x, y, name: output.trim() })
  }
  const finish = async () => {
    setBusy(true)
    try {
      const d = await unwrap(api.POST('/connectors/recordings/{id}/finish', { params: { path: { id } } }))
      setDraft(d)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }
  const cancel = async () => {
    try {
      await unwrap(api.DELETE('/connectors/recordings/{id}', { params: { path: { id } } }))
    } catch {
      // already gone: nothing to cancel
    }
    await navigate({ to: '/connectors' })
  }
  const save = async () => {
    if (!draft || !state.data) return
    const name = state.data.connector
    setBusy(true)
    try {
      const list = await unwrap(api.GET('/connectors'))
      const con = list.find((c) => c.name === name)
      if (!con) throw new Error(`Connector ${name} not found`)
      const full = await unwrap(api.GET('/connectors/{id}', { params: { path: { id: con.id } } }))
      const manifest = full.manifest as Record<string, unknown> & {
        flows?: Record<string, unknown>
        declared?: Record<string, unknown>[]
      }
      const args = [
        ...JSON.stringify(draft.flow).matchAll(/\{\{\s*args\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g),
      ].map((m) => m[1] as string)
      const properties = Object.fromEntries([...new Set(args)].map((a) => [a, { type: 'string' }]))
      const next = {
        ...manifest,
        flows: { ...(manifest.flows ?? {}), [flowName]: draft.flow },
        declared: [
          ...(manifest.declared ?? []).filter((d) => d.id !== opId),
          {
            id: opId,
            description: opDescription || `Recorded flow ${flowName}`,
            flow: flowName,
            input_schema: { type: 'object', required: Object.keys(properties), properties },
          },
        ],
      }
      await stepUp.run(
        () =>
          unwrap(
            api.PATCH('/connectors/{id}', {
              params: { path: { id: con.id } },
              body: { manifest: next as never },
            }),
          ),
        { what: 'Adding a recorded flow', detail: `${name}.${opId} (needs an exposure review)` },
      )
      await qc.invalidateQueries({ queryKey: qk.connectors })
      toast.ok(`${name}.${opId} added; review its exposure before agents can use it`)
      await navigate({ to: '/connectors', search: { connector: con.id } })
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'Flow not saved')
    } finally {
      setBusy(false)
    }
  }

  const steps = (state.data?.steps ?? []) as Step[]
  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        crumbs={[{ label: 'Connectors', to: '/connectors' }, { label: state.data?.name ?? 'Record a flow' }]}
        right={
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void cancel()}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              busy={busy}
              disabled={steps.length === 0}
              onClick={() => void finish()}
            >
              Finish
            </Button>
          </div>
        }
      />
      <QueryState query={state} what="Recording" endpoint="GET /connectors/recordings/{id}">
        {() =>
          state.data && (
            <div className="grid flex-1 grid-cols-[1fr_340px] gap-0 max-lg:grid-cols-1">
              <section aria-label="Live page" className="flex flex-col gap-3 p-5">
                <div className="flex flex-wrap items-center gap-2 text-meta text-text-2">
                  <span className="font-mono text-text">{state.data.url || '/'}</span>
                  <span className="text-text-3">· {state.data.title}</span>
                  <span className="text-text-3">
                    · signed in with the {state.data.purpose} account; actions happen in the real application
                  </span>
                </div>
                <div
                  role="radiogroup"
                  aria-label="What a click on the page does"
                  className="flex flex-wrap gap-1.5"
                >
                  {MODES.map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      role="radio"
                      aria-checked={mode === m.id}
                      title={m.hint}
                      onClick={() => setMode(m.id)}
                      className={`h-8 rounded-btn border px-3 ${mode === m.id ? 'border-text' : 'border-line-2 text-text-2 hover:text-text'}`}
                    >
                      {m.label}
                    </button>
                  ))}
                  {mode !== 'click' && mode !== 'assert' && (
                    <Input
                      aria-label="Output name"
                      placeholder="output name, e.g. rules"
                      value={output}
                      onChange={(e) => setOutput(e.target.value)}
                      className="h-8 w-[200px] font-mono text-sm"
                    />
                  )}
                </div>
                <img
                  src={`/api/v1/connectors/recordings/${id}/screen?n=${nonce}`}
                  alt={`Live page ${state.data.url}. Click to ${MODES.find((m) => m.id === mode)?.label.toLowerCase()}.`}
                  onClick={onScreen}
                  className={`w-full max-w-[1280px] cursor-crosshair rounded-card border border-line ${busy ? 'opacity-60' : ''}`}
                />
                {state.data.blocked_hosts.length > 0 && (
                  <p className="m-0 text-meta text-wait">
                    Blocked hosts: {state.data.blocked_hosts.join(', ')}. Add them to the flow's allowed_hosts
                    if the page needs them.
                  </p>
                )}
              </section>
              <aside aria-label="Recording controls" className="flex flex-col gap-4 border-l border-line p-5">
                <form
                  className="flex flex-col gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    void send({ kind: 'type', text, ...(arg.trim() ? { arg: arg.trim() } : {}) }).then(() =>
                      setText(''),
                    )
                  }}
                >
                  <Field
                    label="Type into the focused field"
                    hint="Give an argument name to make it a parameter"
                  >
                    {(fid, d) => (
                      <Input
                        id={fid}
                        aria-describedby={d}
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                      />
                    )}
                  </Field>
                  <div className="flex gap-2">
                    <Input
                      aria-label="Argument name"
                      placeholder="argument (optional)"
                      value={arg}
                      onChange={(e) => setArg(e.target.value)}
                      className="h-8 font-mono text-sm"
                    />
                    <Button size="sm" type="submit" disabled={busy}>
                      Type
                    </Button>
                  </div>
                </form>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => void send({ kind: 'credential', field: 'username' })}
                  >
                    Type the user name
                  </Button>
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => void send({ kind: 'credential', field: 'password' })}
                  >
                    Type the password
                  </Button>
                  {(['Enter', 'Tab', 'Escape'] as const).map((key) => (
                    <Button
                      key={key}
                      size="sm"
                      disabled={busy}
                      onClick={() => void send({ kind: 'press', key })}
                    >
                      {key}
                    </Button>
                  ))}
                </div>
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (url.trim()) void send({ kind: 'goto', url: url.trim() })
                  }}
                >
                  <Input
                    aria-label="Go to"
                    placeholder="rules?q=deny"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="h-8 font-mono text-sm"
                  />
                  <Button size="sm" type="submit" disabled={busy}>
                    Go
                  </Button>
                </form>
                <div className="flex gap-2">
                  <Button size="sm" disabled={busy} onClick={() => void send({ kind: 'screenshot' })}>
                    Screenshot
                  </Button>
                  <Button
                    size="sm"
                    disabled={busy || steps.length === 0}
                    onClick={() => void send({ kind: 'undo' })}
                  >
                    Undo last step
                  </Button>
                </div>
                <section aria-label="Recorded steps" className="flex flex-col">
                  <h2 className="m-0 border-b border-line pb-2 text-ui font-medium">
                    Steps ({steps.length})
                  </h2>
                  <ol className="m-0 flex flex-col gap-1 py-2 pl-5 font-mono text-[12px] text-text-2">
                    {steps.map((s, i) => (
                      <li key={i} className="break-all">
                        {describe(s)}
                      </li>
                    ))}
                    {steps.length === 0 && (
                      <li className="list-none text-text-3">
                        Click the page to start. Credentials are typed by Z and recorded as placeholders.
                      </li>
                    )}
                  </ol>
                </section>
              </aside>
            </div>
          )
        }
      </QueryState>
      {draft && (
        <Dialog
          open
          onClose={() => setDraft(null)}
          title="Save the recorded flow"
          width={720}
          eyebrow="It is added to the connector and stays unexposed until an admin reviews it"
          footer={
            <>
              <Button onClick={() => setDraft(null)}>Back</Button>
              <Button
                variant="primary"
                busy={busy}
                disabled={
                  !/^[a-z0-9_]+(\.[a-z0-9_]+)+$/.test(opId) || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(flowName)
                }
                onClick={() => void save()}
              >
                Add to the connector
              </Button>
            </>
          }
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Operation id" hint="e.g. rules.search (the verb sets the class: search is a read)">
              {(fid, d) => (
                <Input
                  id={fid}
                  aria-describedby={d}
                  value={opId}
                  onChange={(e) => setOpId(e.target.value)}
                  className="font-mono text-sm"
                />
              )}
            </Field>
            <Field label="Flow name">
              {(fid) => (
                <Input
                  id={fid}
                  value={flowName}
                  onChange={(e) => setFlowName(e.target.value)}
                  className="font-mono text-sm"
                />
              )}
            </Field>
            <Field label="Description" className="sm:col-span-2">
              {(fid) => (
                <Input id={fid} value={opDescription} onChange={(e) => setOpDescription(e.target.value)} />
              )}
            </Field>
            <Field label="Flow" className="sm:col-span-2">
              {(fid) => (
                <Textarea
                  id={fid}
                  readOnly
                  rows={12}
                  value={JSON.stringify(draft.flow, null, 2)}
                  className="font-mono text-[12px]"
                />
              )}
            </Field>
          </div>
        </Dialog>
      )}
    </div>
  )
}
