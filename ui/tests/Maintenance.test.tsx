import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Maintenance } from '@/api/types'
import { Admin } from '@/screens/admin/Admin'
import { AccountBanners } from '@/shell/AppShell'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute, renderWithProviders } from './utils'

let state: Maintenance = { enabled: false, message: null, since: null, by: null }
const puts: unknown[] = []
const requested: string[] = []
let stepUps = 0
const server = setupServer(
  http.get('*/api/v1/admin/maintenance', () => HttpResponse.json(state)),
  http.put('*/api/v1/admin/maintenance', async ({ request }) => {
    const body = (await request.json()) as { enabled: boolean; message?: string }
    puts.push(body)
    state = {
      enabled: body.enabled,
      message: body.message ?? null,
      since: new Date().toISOString(),
      by: 'alice',
    }
    return HttpResponse.json(state)
  }),
  http.post('*/api/v1/auth/step-up', () => {
    stepUps += 1
    return HttpResponse.json({ redirect_url: null, step_up_at: new Date().toISOString() })
  }),
  http.get('*/api/v1/settings/guardrails', () => HttpResponse.json({})),
  http.get('*/api/v1/admin/group-mapping', () =>
    HttpResponse.json({ allowed_groups: [], group_mapping: [], team_mapping: [] }),
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
  state = { enabled: false, message: null, since: null, by: null }
  puts.length = 0
  requested.length = 0
  stepUps = 0
})
afterAll(() => {
  server.close()
})

const others = ['/history', '/settings', '/inference']

describe('Admin → Platform → Maintenance mode', () => {
  it('turns maintenance on with a message after a diff and a fresh sign-in', async () => {
    const user = userEvent.setup()
    const me = makeMe(['users:manage', 'guardrails:read', 'guardrails:manage'])
    await renderRoute('/admin', '/admin?tab=platform', Admin, { me, others })
    const section = await screen.findByRole('region', { name: 'Maintenance mode' })
    expect(await within(section).findByText('Off')).toBeInTheDocument()
    expect(screen.queryByText(/no maintenance mode/i)).toBeNull()
    await user.click(within(section).getByRole('switch', { name: 'Maintenance mode' }))
    await user.type(within(section).getByLabelText('Message shown to everyone'), 'Upgrade until 14:00')
    const pending = within(section).getByRole('region', { name: 'Unsaved change' })
    await user.click(within(pending).getByRole('button', { name: 'Re-authenticate and save' }))
    await user.click(await screen.findByRole('button', { name: 'Re-authenticate' }))
    await waitFor(() => {
      expect(puts).toEqual([{ enabled: true, message: 'Upgrade until 14:00' }])
    })
    expect(stepUps).toBe(1)
    expect(await within(section).findByText(/On since .* turned on by alice/)).toBeInTheDocument()
  })

  it('shows the state read-only with guardrails:read', async () => {
    state = { enabled: true, message: 'Back at 14:00', since: new Date().toISOString(), by: 'bob' }
    const me = makeMe(['users:manage', 'guardrails:read'])
    await renderRoute('/admin', '/admin?tab=platform', Admin, { me, others })
    const section = await screen.findByRole('region', { name: 'Maintenance mode' })
    expect(await within(section).findByText(/turned on by bob/)).toBeInTheDocument()
    expect(within(section).getByText('“Back at 14:00”')).toBeInTheDocument()
    expect(within(section).queryByRole('switch')).toBeNull()
    expect(within(section).queryByLabelText('Message shown to everyone')).toBeNull()
  })

  it('is hidden, and not requested, without guardrails:read', async () => {
    const me = makeMe(['users:manage'])
    await renderRoute('/admin', '/admin?tab=platform', Admin, { me, others })
    await screen.findByRole('region', { name: 'Version and health' })
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByRole('region', { name: 'Maintenance mode' })).toBeNull()
    expect(requested).not.toContain('GET /api/v1/admin/maintenance')
  })

  it('opens the Platform tab to guardrails:manage', async () => {
    const me = makeMe(['guardrails:read', 'guardrails:manage'])
    await renderRoute('/admin', '/admin?tab=platform', Admin, { me, others })
    expect(await screen.findByRole('region', { name: 'Maintenance mode' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Notifications', 'Platform'])
  })
})

describe('AccountBanners', () => {
  it('shows the maintenance banner from GET /me to every signed-in user', () => {
    const me = makeMe(['missions:read'])
    me.banners = [{ kind: 'maintenance', text: 'Maintenance: Z is being upgraded' }]
    renderWithProviders(<AccountBanners />, { me })
    const banner = screen.getByText('Maintenance: Z is being upgraded')
    expect(banner).toHaveAttribute('role', 'status')
  })
})
