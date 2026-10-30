import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { BUILTIN_ROLES } from '@/api/permissions.gen'
import type { AuditSearch } from '@/api/types'
import { Admin } from '@/screens/admin/Admin'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

const initial = (): AuditSearch[] => [
  {
    id: 'as_1',
    name: 'Denied for bob',
    filters: { actor: 'bob', result: 'denied' },
    created_at: '2026-10-01T10:00:00Z',
  },
]
let searches = initial()
const audits: Record<string, string>[] = []
const posts: unknown[] = []
const deletes: string[] = []
const requested: string[] = []
const server = setupServer(
  http.get('*/api/v1/me/audit-searches', () => HttpResponse.json(searches)),
  http.post('*/api/v1/me/audit-searches', async ({ request }) => {
    const body = (await request.json()) as Pick<AuditSearch, 'name' | 'filters'>
    posts.push(body)
    searches = [...searches, { id: 'as_2', created_at: new Date().toISOString(), ...body }]
    return HttpResponse.json(searches.at(-1), { status: 201 })
  }),
  http.delete('*/api/v1/me/audit-searches/:id', ({ params }) => {
    deletes.push(String(params.id))
    searches = searches.filter((s) => s.id !== params.id)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get('*/api/v1/audit', ({ request }) => {
    const q = Object.fromEntries(new URL(request.url).searchParams)
    delete q.limit
    audits.push(q)
    return HttpResponse.json({
      items: [
        {
          seq: 41,
          ts: '2026-10-01T10:00:00Z',
          actor: q.actor ?? 'alice',
          action: 'connector.updated',
          target_type: 'connector',
          target_id: 'con_1',
          result: q.result ?? 'success',
          actor_ip: '10.0.0.12',
          prev_hash: 'a'.repeat(64),
          hash: 'b'.repeat(64),
        },
      ],
      next_cursor: null,
    })
  }),
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
  searches = initial()
  audits.length = 0
  posts.length = 0
  deletes.length = 0
  requested.length = 0
})
afterAll(() => {
  server.close()
})

const auditor = () => makeMe([...BUILTIN_ROLES.auditor])
const others = ['/history', '/settings']

describe('Admin → Audit log → saved searches', () => {
  it('applies a saved search to the audit filters', async () => {
    const user = userEvent.setup()
    await renderRoute('/admin', '/admin?tab=audit', Admin, { me: auditor(), others })
    await user.click(await screen.findByRole('button', { name: 'Apply Denied for bob' }))
    await waitFor(() => {
      expect(audits).toContainEqual({ actor: 'bob', result: 'denied' })
    })
    expect(screen.getByLabelText('Actor')).toHaveValue('bob')
    expect(screen.getByLabelText('Result')).toHaveValue('denied')
    const table = await screen.findByRole('table', { name: 'Matching events' })
    expect(within(table).getByText('denied')).toBeInTheDocument()
  })

  it('saves the current filters under a name', async () => {
    const user = userEvent.setup()
    await renderRoute('/admin', '/admin?tab=audit', Admin, { me: auditor(), others })
    await screen.findByText('Denied for bob')
    const save = screen.getByRole('button', { name: 'Save this search' })
    await user.type(screen.getByLabelText('Name of this search'), 'Carol on connectors')
    expect(save).toBeDisabled() // no filter set yet
    await user.type(screen.getByLabelText('Actor'), 'carol')
    await user.type(screen.getByLabelText('Target type'), 'connector')
    await user.click(save)
    await waitFor(() => {
      expect(posts).toEqual([
        { name: 'Carol on connectors', filters: { actor: 'carol', target_type: 'connector' } },
      ])
    })
    expect(await screen.findByRole('button', { name: 'Apply Carol on connectors' })).toBeInTheDocument()
    expect(audits).toContainEqual({ actor: 'carol', target_type: 'connector' })
  })

  it('deletes a saved search', async () => {
    const user = userEvent.setup()
    await renderRoute('/admin', '/admin?tab=audit', Admin, { me: auditor(), others })
    await user.click(await screen.findByRole('button', { name: 'Delete Denied for bob' }))
    await waitFor(() => {
      expect(deletes).toEqual(['as_1'])
    })
    await waitFor(() => {
      expect(screen.queryByText('Denied for bob')).toBeNull()
    })
  })

  it('is not offered without audit:read', async () => {
    await renderRoute('/admin', '/admin?tab=audit', Admin, {
      me: makeMe(['audit:export', 'audit:read_own']),
      others,
    })
    expect(await screen.findByRole('button', { name: 'Verify the chain' })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByRole('region', { name: 'Search the audit log' })).toBeNull()
    expect(requested).not.toContain('GET /api/v1/me/audit-searches')
  })
})
