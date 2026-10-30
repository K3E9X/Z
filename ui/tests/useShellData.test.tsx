import { waitFor } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { LOCAL_ACCOUNT_LIMITS } from '@/api/permissions.gen'
import { useShellData } from '@/shell/useShellData'
import { emptyApi } from './fixtures'
import { makeMe, renderWithProviders } from './utils'

const requested: string[] = []
const server = setupServer(...emptyApi())
beforeAll(() => {
  server.listen()
  server.events.on('request:start', ({ request }) => {
    requested.push(new URL(request.url).pathname.replace('/api/v1', ''))
  })
})
afterEach(() => {
  server.resetHandlers()
  requested.length = 0
})
afterAll(() => {
  server.close()
})

function Probe() {
  useShellData()
  return null
}

describe('useShellData', () => {
  it('does not request what the bootstrap account cannot read', async () => {
    renderWithProviders(<Probe />, {
      me: makeMe([...LOCAL_ACCOUNT_LIMITS.bootstrap], { local_kind: 'bootstrap' }),
    })
    await new Promise((r) => setTimeout(r, 50))
    expect(requested).toEqual([])
  })

  it('requests each source the user may read', async () => {
    renderWithProviders(<Probe />, { me: makeMe(['missions:read', 'usage:read']) })
    await waitFor(() => {
      expect([...new Set(requested)].sort()).toEqual([
        '/budgets',
        '/inference/health',
        '/missions',
        '/review',
      ])
    })
  })
})
