import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { NewMission } from '@/screens/NewMission'
import { emptyApi, playbook } from './fixtures'
import { makeMe, renderRoute } from './utils'

const server = setupServer(
  http.get('*/api/v1/playbooks', () => HttpResponse.json({ items: [playbook()], next_cursor: null })),
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

const render = (permissions: string[]) =>
  renderRoute('/missions/new', '/missions/new?playbook=weekly-triage', NewMission, {
    me: makeMe(permissions),
    others: ['/tasks', '/missions/$id'],
  })

describe('NewMission', () => {
  it('does not offer schedules and events without triggers:write', async () => {
    await render(['missions:start', 'playbooks:read'])
    expect(await screen.findByText(/need the triggers:write permission/)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /On a schedule/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /On every new event/ })).toBeDisabled()
  })

  it('offers them with triggers:write and a playbook', async () => {
    await render(['missions:start', 'playbooks:read', 'triggers:read', 'triggers:write'])
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: /On a schedule/ })).toBeEnabled()
    })
    expect(screen.getByRole('radio', { name: /On every new event/ })).toBeEnabled()
  })
})
