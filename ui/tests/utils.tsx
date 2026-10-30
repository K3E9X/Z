import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { Me } from '@/api/types'
import { ToastProvider } from '@/components/toast'
import { MeProvider } from '@/lib/auth'
import { PrefsProvider } from '@/lib/prefs'
import { StepUpProvider } from '@/shell/stepup'

export function makeMe(permissions: string[] = ['*'], patch: Partial<Me['user']> = {}): Me {
  return {
    user: {
      id: 'usr_1',
      username: 'alice',
      name: 'Alice Martin',
      teams: [],
      status: 'active',
      effective_roles: [{ role: 'approver', scope: 'org', source: 'gitlab' }],
      ...patch,
    },
    permissions: permissions.map((p) => ({ permission: p, scope: 'org' })),
    session: { id: 's1', kind: 'web', expires_at: '2099-01-01T00:00:00Z' },
    banners: [],
    workspace: { name: 'Cyber Ops', environment: 'test' },
  }
}

export function renderWithProviders(ui: ReactNode, opts: { me?: Me } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const me = opts.me ?? makeMe()
  return {
    qc,
    ...render(
      <PrefsProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <MeProvider me={me}>
              <StepUpProvider>{ui}</StepUpProvider>
            </MeProvider>
          </ToastProvider>
        </QueryClientProvider>
      </PrefsProvider>,
    ),
  }
}

/** Render inside a minimal router (components with <Link>) plus the app providers. */
export async function renderRouted(ui: ReactNode, opts: { me?: Me } = {}) {
  const { createMemoryHistory, createRootRoute, createRouter, RouterProvider } =
    await import('@tanstack/react-router')
  const root = createRootRoute({ component: () => <>{ui}</> })
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ['/'] }) })
  const out = renderWithProviders(<RouterProvider router={router as never} />, opts)
  await router.load()
  return out
}

/**
 * Render a screen at its real route path (useSearch/useParams with `from`) inside a memory router.
 * `others` are extra paths it may navigate to; each renders `<p>route: {path}</p>`.
 */
export async function renderRoute(
  path: string,
  url: string,
  Screen: () => ReactNode,
  opts: { me?: Me; others?: string[] } = {},
) {
  const { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } =
    await import('@tanstack/react-router')
  const root = createRootRoute()
  const page = createRoute({
    getParentRoute: () => root,
    path,
    validateSearch: (s: Record<string, unknown>) => s,
    component: Screen,
  })
  const others = (opts.others ?? []).map((p) =>
    createRoute({ getParentRoute: () => root, path: p, component: () => <p>route: {p}</p> }),
  )
  const router = createRouter({
    routeTree: root.addChildren([page, ...others]),
    history: createMemoryHistory({ initialEntries: [url] }),
  })
  const out = renderWithProviders(<RouterProvider router={router as never} />, opts)
  await router.load()
  return { ...out, router }
}
