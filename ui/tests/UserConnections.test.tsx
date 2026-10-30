import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { User, UserDetail } from '@/api/types'
import { Admin } from '@/screens/admin/Admin'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

const bob: User = {
  id: 'usr_2',
  username: 'bob',
  name: 'Bob Durand',
  teams: [],
  status: 'active',
  effective_roles: [],
}
let detail: UserDetail = {
  ...bob,
  bindings: [],
  sessions: [],
  tokens: [],
  recent_activity: [],
  connections: [
    {
      id: 'pc_1',
      connector: 'outlook',
      status: 'active',
      read_scopes: ['Mail.Read'],
      write_scopes: ['Mail.Send'],
      last_used_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
      created_at: '2026-10-01T10:00:00Z',
    },
    {
      id: 'pc_2',
      connector: 'gdrive',
      status: 'expired',
      read_scopes: ['drive.readonly'],
      write_scopes: [],
      last_used_at: null,
      created_at: '2026-10-01T10:00:00Z',
    },
  ],
}
const server = setupServer(
  http.get('*/api/v1/admin/users', () => HttpResponse.json({ items: [bob], next_cursor: null })),
  http.get('*/api/v1/admin/users/:id', () => HttpResponse.json(detail)),
  http.get('*/api/v1/admin/group-mapping', () => HttpResponse.json({ allowed_groups: [] })),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
})
afterAll(() => {
  server.close()
})

describe('Admin → Users → user detail', () => {
  it("lists the user's personal connections read-only", async () => {
    await renderRoute('/admin', '/admin?tab=users&user=usr_2', Admin, {
      me: makeMe(['users:manage']),
      others: ['/history', '/settings'],
    })
    const section = await screen.findByRole('region', { name: 'Personal connections' })
    expect(within(section).getByText('outlook')).toBeInTheDocument()
    expect(within(section).getByText('Active')).toBeInTheDocument()
    expect(within(section).getByText('Mail.Read')).toBeInTheDocument()
    expect(within(section).getByText('Mail.Send')).toBeInTheDocument()
    expect(within(section).getByText(/last used 2 h ago/)).toBeInTheDocument()
    expect(within(section).getByText('gdrive')).toBeInTheDocument()
    expect(within(section).getByText('Expired')).toBeInTheDocument()
    expect(within(section).getByText(/last used never/)).toBeInTheDocument()
    expect(within(section).getByText(/Suspending the user revokes them/)).toBeInTheDocument()
    expect(within(section).queryByRole('button')).toBeNull()
  })

  it('says when the user has no personal connection', async () => {
    detail = { ...detail, connections: [] }
    await renderRoute('/admin', '/admin?tab=users&user=usr_2', Admin, {
      me: makeMe(['users:manage']),
      others: ['/history', '/settings'],
    })
    const section = await screen.findByRole('region', { name: 'Personal connections' })
    expect(within(section).getByText('No personal connection.')).toBeInTheDocument()
  })
})
