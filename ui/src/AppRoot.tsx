import { useQueryClient } from '@tanstack/react-query'
import { Navigate, useRouterState } from '@tanstack/react-router'
import { useEffect } from 'react'
import { onApiError, setCsrfToken } from './api/client'
import { isUnauthenticated, problemMessage } from './api/problem'
import { qk, useMe } from './api/queries'
import { ZMark } from './components/Icon'
import { Button, Spinner } from './components/ui'
import { MeProvider } from './lib/auth'
import { SignIn } from './screens/SignIn'
import { AppShell } from './shell/AppShell'
import { StepUpProvider } from './shell/stepup'

function Splash({ children }: { children?: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg text-text">
      <ZMark size={30} />
      {children ?? <Spinner label="Loading Z" />}
    </main>
  )
}

/** Auth gate: every screen needs a signed-in principal from GET /me; a 401 anywhere shows sign-in. */
export function AppRoot() {
  const qc = useQueryClient()
  const me = useMe()
  const location = useRouterState({ select: (s) => s.location })

  useEffect(
    () =>
      onApiError((e) => {
        if (isUnauthenticated(e)) void qc.invalidateQueries({ queryKey: qk.me })
      }),
    [qc],
  )

  useEffect(() => {
    setCsrfToken(me.data?.csrf_token)
  }, [me.data?.csrf_token])

  const onSignin = location.pathname === '/signin'
  const search = location.search as { error?: string; redirect?: string }

  if (me.isPending) return <Splash />
  if (me.error || !me.data) {
    if (isUnauthenticated(me.error) || onSignin) {
      const redirect = onSignin ? search.redirect : location.href
      return <SignIn error={search.error} redirect={redirect} />
    }
    return (
      <Splash>
        <div role="alert" className="flex max-w-[420px] flex-col items-center gap-3 text-center">
          <div className="font-medium">Z is not reachable</div>
          <div className="text-meta text-text-2">{problemMessage(me.error)}</div>
          <Button
            onClick={() => {
              void me.refetch()
            }}
          >
            Try again
          </Button>
        </div>
      </Splash>
    )
  }
  if (onSignin) {
    const to =
      search.redirect && search.redirect.startsWith('/') && !search.redirect.startsWith('//')
        ? search.redirect
        : '/'
    return <Navigate to={to} replace />
  }
  return (
    <MeProvider me={me.data}>
      <StepUpProvider>
        <AppShell />
      </StepUpProvider>
    </MeProvider>
  )
}
