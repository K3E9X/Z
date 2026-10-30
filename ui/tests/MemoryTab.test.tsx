import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Agent, MemoryFact } from '@/api/types'
import { MemoryTab } from '@/screens/Agents'
import { makeMe, renderWithProviders } from './utils'

const fact = (patch: Partial<MemoryFact> = {}): MemoryFact => ({
  id: 'mem_1',
  scope: 'agent',
  scope_ref: 'summarizer',
  text: 'Release notes are in notes.md.',
  source: 'proposal:prp_1',
  approved_by: 'alice',
  version: 1,
  data_class: 'C2',
  active: true,
  created_at: '2026-10-01T10:00:00Z',
  ...patch,
})
let current = fact()
let sent: { path: string; body: unknown } | null = null
const server = setupServer(
  http.get('*/api/v1/memory', () => HttpResponse.json({ items: [current], next_cursor: null })),
  http.post('*/api/v1/memory/:id/:verb', async ({ request, params }) => {
    sent = { path: `${String(params.id)}/${String(params.verb)}`, body: await request.json() }
    current = fact({ active: params.verb === 'restore', version: current.version + 1 })
    return HttpResponse.json(current)
  }),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  current = fact()
  sent = null
})
afterAll(() => {
  server.close()
})

const agent = { name: 'summarizer', memory: { scopes: ['agent'], top_k: 8 } } as unknown as Agent

describe('Memory tab', () => {
  it('lists the facts and deactivates one with a reason', async () => {
    const user = userEvent.setup()
    renderWithProviders(<MemoryTab agent={agent} />)
    expect(await screen.findByText('Release notes are in notes.md.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Deactivate' }))
    const confirm = screen.getAllByRole('button', { name: 'Deactivate' }).at(-1)
    expect(confirm).toBeDisabled() // a reason is required
    await user.type(screen.getByLabelText('Reason (kept in the audit log)'), 'outdated')
    await user.click(screen.getAllByRole('button', { name: 'Deactivate' }).at(-1) as HTMLElement)
    await waitFor(() => {
      expect(sent).toEqual({ path: 'mem_1/deactivate', body: { reason: 'outdated' } })
    })
    expect(await screen.findByRole('button', { name: 'Restore' })).toBeInTheDocument()
  })

  it('hides the change buttons without learning:apply', async () => {
    renderWithProviders(<MemoryTab agent={agent} />, { me: makeMe(['learning:read']) })
    expect(await screen.findByText('Release notes are in notes.md.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deactivate' })).not.toBeInTheDocument()
  })
})
