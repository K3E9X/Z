import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { Connectors } from '@/screens/Connectors'
import { connector, emptyApi, operation } from './fixtures'
import { renderRoute } from './utils'

let exposed = false
let opsFetches = 0
const server = setupServer(
  http.get('*/api/v1/connectors', () => HttpResponse.json([connector()])),
  http.get('*/api/v1/connectors/con_1', () => HttpResponse.json(connector())),
  http.get('*/api/v1/connectors/con_1/operations', () => {
    opsFetches += 1
    return HttpResponse.json([operation({ exposed })])
  }),
  http.patch('*/api/v1/operations/:id', async ({ request }) => {
    const body = (await request.json()) as { exposed: boolean }
    exposed = body.exposed
    return HttpResponse.json(operation({ exposed }))
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  exposed = false
  opsFetches = 0
})
afterAll(() => {
  server.close()
})

describe('Connectors', () => {
  it('keeps an operation exposed after saving the toggle (refetches by connector id)', async () => {
    const user = userEvent.setup()
    await renderRoute('/connectors', '/connectors', Connectors, { others: ['/history', '/connectors/new'] })
    const toggle = await screen.findByRole('switch', { name: 'Expose jira.issues.get' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    await user.click(toggle)
    await waitFor(() => {
      expect(opsFetches).toBe(2)
    })
    await waitFor(() => {
      expect(screen.getByRole('switch', { name: 'Expose jira.issues.get' })).toHaveAttribute(
        'aria-checked',
        'true',
      )
    })
  })
})
