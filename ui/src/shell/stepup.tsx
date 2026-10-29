import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, unwrap } from '@/api/client'
import { isStepUpRequired, problemMessage } from '@/api/problem'
import { qk } from '@/api/queries'
import { Dialog } from '@/components/Dialog'
import { Button, Field, Input } from '@/components/ui'
import { stepUpRemaining, useOptionalMe } from '@/lib/auth'

export interface StepUpContext {
  /** "Approving a destructive action" — completes "… needs a fresh sign-in". */
  what: string
  /** Exactly what will run, shown in the box (operation × count, diff summary...). */
  detail?: ReactNode
  destructive?: boolean
}

export class StepUpCancelled extends Error {
  constructor() {
    super('Re-authentication cancelled')
    this.name = 'StepUpCancelled'
  }
}

interface Pending {
  ctx: StepUpContext
  resolve: () => void
  reject: (e: unknown) => void
}

interface StepUpApi {
  /** Run `fn`; when the server answers step-up-required, re-authenticate and retry once. */
  run: <T>(fn: () => Promise<T>, ctx: StepUpContext) => Promise<T>
  /** Re-authenticate first unless the step-up window is still open (admin saves). */
  ensure: (ctx: StepUpContext) => Promise<void>
  fresh: boolean
}

const Ctx = createContext<StepUpApi | null>(null)

export function StepUpProvider({ children, maxAgeS = 300 }: { children: ReactNode; maxAgeS?: number }) {
  const me = useOptionalMe()
  const qc = useQueryClient()
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const pendingRef = useRef<Pending | null>(null)

  const ask = useCallback((ctx: StepUpContext) => {
    return new Promise<void>((resolve, reject) => {
      const p = { ctx, resolve, reject }
      pendingRef.current = p
      setError(null)
      setPassword('')
      setTotp('')
      setPending(p)
    })
  }, [])

  const run = useCallback(
    async <T,>(fn: () => Promise<T>, ctx: StepUpContext): Promise<T> => {
      try {
        return await fn()
      } catch (e) {
        if (!isStepUpRequired(e)) throw e
        await ask(ctx)
        return fn()
      }
    },
    [ask],
  )

  const ensure = useCallback(
    async (ctx: StepUpContext) => {
      if (me?.user.local_kind === 'dev') return
      if (stepUpRemaining(me, maxAgeS) > 20) return
      await ask(ctx)
    },
    [ask, me, maxAgeS],
  )

  const local = me?.user.local_kind === 'bootstrap' || me?.user.local_kind === 'test' || me?.user.local_kind === 'breakglass'
  const breakglass = me?.user.local_kind === 'breakglass'

  const confirm = async () => {
    const p = pendingRef.current
    if (!p) return
    setBusy(true)
    setError(null)
    try {
      const res = await unwrap(
        api.POST('/auth/step-up', {
          body: {
            return_to: window.location.pathname + window.location.search,
            ...(local ? { password } : {}),
            ...(breakglass ? { totp } : {}),
          },
        }),
      )
      if (res.redirect_url) {
        // GitLab re-authentication (prompt=login, max_age): the page comes back to return_to.
        window.location.assign(res.redirect_url)
        return
      }
      await qc.invalidateQueries({ queryKey: qk.me })
      pendingRef.current = null
      setPending(null)
      p.resolve()
    } catch (e) {
      setError(problemMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const cancel = () => {
    const p = pendingRef.current
    pendingRef.current = null
    setPending(null)
    p?.reject(new StepUpCancelled())
  }

  const fresh = stepUpRemaining(me, maxAgeS) > 20 || me?.user.local_kind === 'dev'
  const value = useMemo(() => ({ run, ensure, fresh }), [run, ensure, fresh])

  return (
    <Ctx.Provider value={value}>
      {children}
      <Dialog open={!!pending} onClose={cancel} title="Confirm it's you" eyebrow="Step-up" width={440}>
        {pending && (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault()
              void confirm()
            }}
          >
            <p className="m-0 text-text-2">
              {pending.ctx.what} needs a fresh sign-in
              {local ? ' with your local account password' : ' with GitLab'} (less than {Math.round(maxAgeS / 60)} minutes
              old).
            </p>
            {pending.ctx.detail && (
              <div
                className={`rounded-btn border px-3.5 py-3 text-ui ${pending.ctx.destructive ? 'border-err' : 'border-line-2'}`}
              >
                {pending.ctx.destructive && <span className="font-medium text-err">Destructive · </span>}
                {pending.ctx.detail}
              </div>
            )}
            {local && (
              <Field label="Password">
                {(id) => (
                  <Input
                    id={id}
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value)
                    }}
                    required
                    autoFocus
                    data-autofocus
                  />
                )}
              </Field>
            )}
            {breakglass && (
              <Field label="TOTP code">
                {(id) => (
                  <Input
                    id={id}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={totp}
                    onChange={(e) => {
                      setTotp(e.target.value)
                    }}
                    required
                  />
                )}
              </Field>
            )}
            {error && (
              <div role="alert" className="text-meta text-err">
                {error}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Button onClick={cancel}>Cancel</Button>
              <Button type="submit" variant="primary" busy={busy} autoFocus={!local} data-autofocus={!local ? true : undefined}>
                Re-authenticate
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </Ctx.Provider>
  )
}

export function useStepUp(): StepUpApi {
  const c = useContext(Ctx)
  if (!c) throw new Error('useStepUp outside StepUpProvider')
  return c
}
