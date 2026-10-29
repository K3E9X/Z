import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { MissionControl } from '@/screens/MissionControl'
import { emptyApi, emptyPage, mission } from './fixtures'
import { renderRoute } from './utils'

const server = setupServer(
  http.get('*/api/v1/missions', ({ request }) => {
    const status = new URL(request.url).searchParams.get('status') ?? ''
    return HttpResponse.json(
      status.includes('running') ? { items: [mission({ id: 'msn_run' })], next_cursor: null } : emptyPage,
    )
  }),
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

describe('MissionControl', () => {
  it('lets Enter activate a focused link instead of opening the selected mission', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/', '/', MissionControl, {
      others: ['/missions/$id', '/review', '/missions/new', '/agents'],
    })
    await screen.findByRole('table', { name: 'Agents at work' })
    const viewAll = screen.getByRole('link', { name: 'View all' })
    viewAll.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('route: /review')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/review')
  })

  it('opens the focused row with Enter, and the selected mission when nothing is focused', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/', '/', MissionControl, { others: ['/missions/$id'] })
    const row = (await screen.findByRole('table', { name: 'Agents at work' })).closest<HTMLElement>(
      '[role=row]',
    )
    row?.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('route: /missions/$id')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/missions/msn_run')
    await router.navigate({ to: '/' })
    await screen.findByRole('table', { name: 'Agents at work' })
    ;(document.activeElement as HTMLElement | null)?.blur()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('route: /missions/$id')).toBeInTheDocument()
  })
})
