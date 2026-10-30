import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { isNotServed } from '@/api/problem'
import { qk, useInferenceCredential } from '@/api/queries'
import type { InferenceCredential } from '@/api/types'
import { NotServed } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { Button, Field, Input } from '@/components/ui'
import { shortDate } from '@/lib/format'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'

const KEY = /^ixk_[0-9a-fA-F]{64}$/

function source(c: InferenceCredential): string {
  if (c.source === 'ui')
    return `Set from Z${c.updated_by ? ` by ${c.updated_by}` : ''}${c.updated_at ? ` · ${shortDate(c.updated_at)}` : ''}`
  if (c.source === 'env') return 'From the deployment (INFERENCE_API_KEY)'
  if (c.source === 'none') return 'No key: the models cannot be called'
  return 'The model gateway does not answer: showing what Z recorded'
}

/** The inferX key the model gateway uses (ADR 0027): set or replace it, see its fingerprint and the last check.
 * The key is typed here once and sent to the gateway; it is never shown again, by Z or by this page. */
export function InferenceKeyPanel() {
  const credential = useInferenceCredential()
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState<'save' | 'test' | null>(null)
  const valid = KEY.test(key.trim())

  const done = async (out: InferenceCredential, what: string) => {
    qc.setQueryData(qk.inferenceCredential, out)
    await qc.invalidateQueries({ queryKey: qk.inferenceHealth })
    if (out.check && !out.check.ok)
      toast.error(new Error(`${what}, but the check failed: ${out.check.detail}`))
    else toast.ok(what)
  }

  const save = async () => {
    if (!valid) return
    setBusy('save')
    try {
      const out = await stepUp.run(
        () => unwrap(api.PUT('/inference/credential', { body: { api_key: key.trim(), test: true } })),
        { what: 'Replacing the inferX key', detail: 'Every model call uses it from the next request' },
      )
      setKey('') // never kept on the page once sent
      await done(out, 'inferX key saved')
    } catch (e) {
      if (!(e instanceof StepUpCancelled)) toast.error(e, 'inferX key not saved')
    } finally {
      setBusy(null)
    }
  }

  const test = async () => {
    setBusy('test')
    try {
      await done(await unwrap(api.POST('/inference/credential:test')), 'The inferX key works')
    } catch (e) {
      toast.error(e, 'Check failed')
    } finally {
      setBusy(null)
    }
  }

  if (isNotServed(credential.error))
    return <NotServed what="The inferX key" endpoint="GET /inference/credential" compact />
  const c = credential.data
  return (
    <div className="flex flex-col gap-3 rounded-panel bg-raised px-4 py-3.5" aria-label="inferX key">
      <span className="font-medium">inferX key</span>
      {c ? (
        <div className="flex flex-col gap-1 text-meta text-text-2">
          <span>{source(c)}</span>
          {c.fingerprint && (
            <span>
              Fingerprint <span className="font-mono">{c.fingerprint}</span>
            </span>
          )}
          {c.check && (
            <span className="flex items-center gap-1.5">
              <Dot tone={c.check.ok ? 'ok' : 'err'} />
              {c.check.ok ? 'Answered' : 'Failed'} ({c.check.model}) · {shortDate(c.check.checked_at)}
              {!c.check.ok && <span className="text-text-3"> · {c.check.detail}</span>}
            </span>
          )}
        </div>
      ) : (
        <span className="text-meta text-text-3">{credential.isLoading ? 'Loading…' : 'Unavailable'}</span>
      )}
      <Field
        label="New inferX key"
        hint="A project key (ixk_…) limited to the models Z uses. It goes to the model gateway only, encrypted there; Z never shows it again."
        error={key && !valid ? 'An inferX key is ixk_ followed by 64 hexadecimal characters' : null}
      >
        {(id, d) => (
          <Input
            id={id}
            aria-describedby={d}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="ixk_…"
            className="font-mono text-sm"
          />
        )}
      </Field>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={!valid}
          busy={busy === 'save'}
          onClick={() => void save()}
        >
          Save and check
        </Button>
        {c && c.source !== 'none' && (
          <Button size="sm" busy={busy === 'test'} onClick={() => void test()}>
            Check now
          </Button>
        )}
      </div>
    </div>
  )
}
