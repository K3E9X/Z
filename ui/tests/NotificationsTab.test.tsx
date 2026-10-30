import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { NotificationsTab } from '@/screens/admin/NotificationsTab'
import { renderWithProviders } from './utils'

const puts: unknown[] = []
const tests: unknown[] = []
let settings = {
  targets: [
    {
      id: 'secops',
      name: 'SecOps Slack',
      kind: 'slack',
      events: ['approval_requested'],
      url_ref: 'env:Z_SLACK',
      status: { last_error: 'HTTP 404', last_error_at: '2026-10-29T08:00:00Z' },
    },
  ],
  smtp: null,
}
const server = setupServer(
  http.get('*/api/v1/admin/notifications', () => HttpResponse.json(settings)),
  http.put('*/api/v1/admin/notifications', async ({ request }) => {
    const body = (await request.json()) as typeof settings
    puts.push(body)
    settings = body
    return HttpResponse.json(body)
  }),
  http.post('*/api/v1/admin/notifications:test', async ({ request }) => {
    tests.push(await request.json())
    return new HttpResponse(null, { status: 204 })
  }),
)
beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  puts.length = 0
  tests.length = 0
})
afterAll(() => server.close())

describe('NotificationsTab', () => {
  it('shows delivery failures, sends a test and adds a signed webhook without echoing status', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NotificationsTab />)
    expect(await screen.findByText('Last delivery failed: HTTP 404')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Send test' }))
    await waitFor(() => expect(tests).toEqual([{ target_id: 'secops' }]))
    await user.click(screen.getByRole('button', { name: 'Add target' }))
    await user.type(screen.getByLabelText('Id'), 'siem')
    await user.selectOptions(screen.getByLabelText('Kind'), 'webhook')
    await user.type(screen.getByLabelText('Webhook URL reference'), 'env:Z_SIEM_HOOK')
    await user.type(screen.getByLabelText('Signing key reference'), 'env:Z_SIEM_KEY')
    await user.click(screen.getByRole('checkbox', { name: 'Budget alert' }))
    await user.click(screen.getByRole('button', { name: 'Save target' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    const body = puts[0] as { targets: Record<string, unknown>[] }
    expect(body.targets.map((t) => t.id)).toEqual(['secops', 'siem'])
    expect(body.targets.every((t) => !('status' in t))).toBe(true)
    expect(body.targets[1]).toMatchObject({
      kind: 'webhook',
      url_ref: 'env:Z_SIEM_HOOK',
      secret_ref: 'env:Z_SIEM_KEY',
      events: ['approval_requested', 'budget_alert'],
    })
  })
})
