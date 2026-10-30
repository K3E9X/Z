import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { AuditEvent } from '@/api/types'
import { History } from '@/screens/History'
import { emptyApi } from './fixtures'
import { renderRoute } from './utils'

function event(seq: number, title: string): AuditEvent {
  return {
    seq,
    ts: '2026-03-01T23:30:05Z',
    actor: 'alice',
    action: 'mission.created',
    target_type: 'mission',
    target_id: `msn_${seq}`,
    result: 'success',
    after: { title },
    prev_hash: 'a'.repeat(64),
    hash: 'b'.repeat(64),
  }
}

const PAGES: Record<string, { items: AuditEvent[]; next_cursor: string | null }> = {
  first: { items: [event(300, 'Newest page')], next_cursor: 'c1' },
  c1: { items: [event(200, 'Second page')], next_cursor: 'c2' },
  c2: { items: [event(100, 'Oldest page')], next_cursor: null },
}
const cursors: (string | null)[] = []
const server = setupServer(
  http.get('*/api/v1/audit', ({ request }) => {
    const cursor = new URL(request.url).searchParams.get('cursor')
    cursors.push(cursor)
    return HttpResponse.json(PAGES[cursor ?? 'first'])
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  cursors.length = 0
})
afterAll(() => {
  server.close()
})

describe('History', () => {
  it('follows the cursor of the last page loaded when loading older events', async () => {
    const user = userEvent.setup()
    await renderRoute('/history', '/history?range=all', History, { others: ['/missions/$id/replay'] })
    await screen.findAllByText(/Newest page/)
    await user.click(screen.getByRole('button', { name: 'Load older events' }))
    await screen.findByText(/Second page/)
    await user.click(screen.getByRole('button', { name: 'Load older events' }))
    await screen.findByText(/Oldest page/)
    expect(cursors.filter((c) => c !== null)).toEqual(['c1', 'c2'])
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Load older events' })).toBeNull()
    })
  })
})
