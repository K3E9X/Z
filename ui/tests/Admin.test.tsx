import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { BUILTIN_ROLES } from '@/api/permissions.gen'
import { Admin } from '@/screens/admin/Admin'
import { NAV, visibleNav } from '@/shell/nav'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

const requested: string[] = []
const server = setupServer(
  http.get('*/api/v1/teams', () =>
    HttpResponse.json([{ id: 'team_1', name: 'secops', gitlab_groups: [], members_count: 3 }]),
  ),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
  server.events.on('request:start', ({ request }) => {
    requested.push(`${request.method} ${new URL(request.url).pathname}`)
  })
})
afterEach(() => {
  server.resetHandlers()
  requested.length = 0
})
afterAll(() => {
  server.close()
})

const tabs = () => screen.getAllByRole('tab').map((t) => t.textContent)
const auditor = () => makeMe([...BUILTIN_ROLES.auditor])

describe('Admin', () => {
  it('lets an auditor reach the audit chain verification, and nothing that needs users:manage', async () => {
    await renderRoute('/admin', '/admin?tab=audit', Admin, {
      me: auditor(),
      others: ['/history', '/settings'],
    })
    expect(await screen.findByRole('button', { name: 'Verify the chain' })).toBeInTheDocument()
    expect(tabs()).toEqual(['Teams', 'Audit log'])
    await new Promise((r) => setTimeout(r, 30))
    expect(requested.filter((r) => r.includes('/api/v1/admin/'))).toEqual([])
    expect(visibleNav(NAV, auditor()).map((n) => n.label)).toContain('Admin')
  })

  it('shows Notifications and Platform to guardrails:manage, and Teams read-only without teams:manage', async () => {
    const me = makeMe(['guardrails:manage', 'teams:read'])
    await renderRoute('/admin', '/admin?tab=teams', Admin, { me, others: ['/history', '/settings'] })
    expect(await screen.findByText('secops')).toBeInTheDocument()
    expect(tabs()).toEqual(['Teams', 'Notifications', 'Platform'])
    expect(screen.queryByRole('button', { name: 'New team' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('offers team edits with teams:manage', async () => {
    const me = makeMe(['teams:read', 'teams:manage'])
    await renderRoute('/admin', '/admin?tab=teams', Admin, { me, others: ['/history', '/settings'] })
    await screen.findByText('secops')
    expect(screen.getByRole('button', { name: 'New team' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('keeps a viewer out of the admin area', async () => {
    const viewer = makeMe([...BUILTIN_ROLES.viewer])
    await renderRoute('/admin', '/admin', Admin, { me: viewer })
    await waitFor(() => {
      expect(screen.queryByRole('tab')).toBeNull()
    })
    expect(visibleNav(NAV, viewer).map((n) => n.label)).not.toContain('Admin')
  })
})
