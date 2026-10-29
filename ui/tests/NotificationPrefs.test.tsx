import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { NotificationPrefs } from '@/screens/Settings'
import { renderWithProviders } from './utils'

const puts: unknown[] = []
const server = setupServer(
  http.get('*/api/v1/me/notifications', () =>
    HttpResponse.json({
      email: { enabled: false, events: [] },
      address: 'bob@example.internal',
      available: true,
    }),
  ),
  http.put('*/api/v1/me/notifications', async ({ request }) => {
    const body = (await request.json()) as { email: unknown }
    puts.push(body)
    return HttpResponse.json({ ...body, address: 'bob@example.internal', available: true })
  }),
)
beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  puts.length = 0
})
afterAll(() => server.close())

describe('NotificationPrefs', () => {
  it('turns on email for chosen events of my missions and saves only the email part', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NotificationPrefs />)
    await user.click(await screen.findByRole('checkbox', { name: 'Email me at bob@example.internal' }))
    await user.click(screen.getByRole('checkbox', { name: 'An agent asks me a question' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(puts).toEqual([{ email: { enabled: true, events: ['question_asked'] } }]))
  })
})
