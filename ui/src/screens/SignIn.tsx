import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { problemMessage } from '@/api/problem'
import { qk } from '@/api/queries'
import { Icon, ZMark } from '@/components/Icon'
import { Button, Field, Input } from '@/components/ui'

const REASONS: Record<string, { title: string; body: string; access?: boolean }> = {
  not_in_allowed_group: {
    title: "You don't have access yet",
    body: "You're signed in to GitLab, but you're not in a group that can use Z.",
    access: true,
  },
  no_mapped_group: {
    title: "You don't have access yet",
    body: "You're signed in to GitLab, but none of your groups is mapped to a Z role.",
    access: true,
  },
  account_suspended: {
    title: 'Your account is suspended',
    body: 'An admin suspended your Z account. Sessions and CLI tokens are revoked. Ask an admin to reactivate it.',
  },
  provider_error: { title: 'GitLab refused the sign-in', body: 'GitLab answered with an error. Try again.' },
  provider_unreachable: { title: 'GitLab is not reachable', body: 'Z could not reach GitLab. Try again in a moment.' },
  invalid_state: { title: 'Sign-in expired', body: 'The sign-in link was not started here or was already used.' },
  expired_state: { title: 'Sign-in expired', body: 'You took too long to come back from GitLab. Start again.' },
  stale_authentication: {
    title: 'Re-authentication too old',
    body: 'GitLab did not ask for your password again. Sign in again to confirm it is you.',
  },
  step_up_mismatch: { title: 'Different account', body: 'You re-authenticated as another GitLab user.' },
}

function genericReason(code: string): { title: string; body: string; access?: boolean } {
  return {
    title: 'Sign-in failed',
    body: `GitLab sign-in was refused (${code.replace(/_/g, ' ')}). Try again or ask an admin.`,
  }
}

export function SignIn({ error, redirect }: { error?: string; redirect?: string }) {
  const qc = useQueryClient()
  const [local, setLocal] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const cli = useQuery({
    queryKey: ['auth', 'cli-config'],
    queryFn: () => unwrap(api.GET('/auth/cli/config')),
    retry: false,
    staleTime: Infinity,
  })
  const target = redirect && redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/'
  const loginHref = `/api/v1/auth/login?redirect=${encodeURIComponent(target)}`
  const reason = error ? (REASONS[error] ?? genericReason(error)) : null
  const issuer = cli.data?.issuer_url

  const submitLocal = async () => {
    setBusy(true)
    setLocalError(null)
    try {
      await unwrap(
        api.POST('/auth/local/login', { body: { username, password, ...(totp ? { totp } : {}) } }),
      )
      await qc.invalidateQueries({ queryKey: qk.me })
      window.location.assign(target)
    } catch (e) {
      setLocalError(problemMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 py-12">
      <div className="flex w-full max-w-[860px] flex-wrap items-start justify-center gap-7">
        <section
          aria-labelledby="signin-title"
          className="flex w-full max-w-[400px] flex-col gap-6 rounded-card border border-line-2 bg-panel px-8 py-9"
        >
          <div className="flex flex-col gap-3">
            <ZMark size={34} />
            <div>
              <h1 id="signin-title" className="m-0 font-display-face text-[30px] leading-tight">
                Sign in to Z
              </h1>
              {issuer && <div className="mt-1 text-text-2">GitLab · {issuer.replace(/^https?:\/\//, '')}</div>}
            </div>
          </div>
          <a
            href={loginHref}
            className="flex h-[42px] items-center justify-center gap-2.5 rounded-btn bg-inv-bg font-medium text-inv-text no-underline hover:opacity-90"
          >
            <Icon name="key" size={15} />
            Continue with GitLab
          </a>
          <div className="flex items-center gap-3 text-meta text-text-3" aria-hidden="true">
            <span className="h-px flex-1 bg-line" />
            or
            <span className="h-px flex-1 bg-line" />
          </div>
          {!local ? (
            <div className="flex flex-col gap-2">
              <Button
                size="lg"
                onClick={() => {
                  setLocal(true)
                }}
              >
                Use a local account
              </Button>
              <p className="m-0 text-meta text-wait">
                Only works while the bootstrap account, test accounts or break-glass are enabled on this server.
              </p>
            </div>
          ) : (
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault()
                void submitLocal()
              }}
            >
              <Field label="Local account">
                {(id) => (
                  <Input
                    id={id}
                    autoComplete="username"
                    value={username}
                    onChange={(e) => {
                      setUsername(e.target.value)
                    }}
                    placeholder="z-bootstrap"
                    required
                    autoFocus
                  />
                )}
              </Field>
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
                  />
                )}
              </Field>
              <Field label="TOTP code" hint="Break-glass only">
                {(id, d) => (
                  <Input
                    id={id}
                    aria-describedby={d}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={totp}
                    onChange={(e) => {
                      setTotp(e.target.value)
                    }}
                  />
                )}
              </Field>
              {localError && (
                <div role="alert" className="text-meta text-err">
                  {localError}
                </div>
              )}
              <div className="flex gap-2">
                <Button
                  onClick={() => {
                    setLocal(false)
                  }}
                >
                  Back
                </Button>
                <Button type="submit" variant="primary" busy={busy} className="flex-1">
                  Sign in
                </Button>
              </div>
              <p className="m-0 text-meta text-text-3">
                Local sign-ins are rate-limited, locked after 5 failures and always recorded with your IP address.
              </p>
            </form>
          )}
          <p className="m-0 text-meta text-text-2">
            Your access comes from your GitLab groups. Every action in Z, including what agents do on your behalf, is
            recorded in the audit log.
          </p>
          <div className="border-t border-line pt-4 text-meta text-text-3">
            From a terminal: <code className="font-mono text-text">z login</code>
          </div>
        </section>

        {reason && (
          <section
            role="alert"
            aria-labelledby="signin-error"
            className="flex w-full max-w-[400px] flex-col gap-4 rounded-card border border-line-2 bg-panel px-8 py-9"
          >
            <div className="text-meta text-text-3">Sign-in problem</div>
            <h2 id="signin-error" className="m-0 text-[19px] font-semibold">
              {reason.title}
            </h2>
            <p className="m-0 text-text-2">{reason.body}</p>
            {reason.access === true && (
              <div className="rounded-btn bg-raised px-3.5 py-3 text-meta text-text-2">
                Ask an admin to add you to one of the Z groups (for example{' '}
                <code className="font-mono text-text">cyber/z/z-operators</code>) or to the group of your team. Access is
                checked again at your next sign-in.
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <a
                href={loginHref}
                className="flex h-8 items-center justify-center rounded-btn border border-line-2 no-underline hover:bg-raised"
              >
                Try again
              </a>
              {issuer ? (
                <a
                  href={`${issuer.replace(/\/$/, '')}/users/sign_out`}
                  rel="noopener noreferrer"
                  className="flex h-8 items-center justify-center rounded-btn border border-line-2 no-underline hover:bg-raised"
                >
                  Sign out of GitLab
                </a>
              ) : (
                <span />
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  )
}
