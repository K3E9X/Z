import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { NewMission } from '@/screens/NewMission'
import { emptyApi, playbook } from './fixtures'
import { makeMe, renderRoute } from './utils'

const posted: { url: string; body: unknown }[] = []
const server = setupServer(
  http.get('*/api/v1/playbooks', () => HttpResponse.json({ items: [playbook()], next_cursor: null })),
  http.post('*/api/v1/*', async ({ request }) => {
    posted.push({ url: new URL(request.url).pathname.replace('/api/v1', ''), body: await request.json() })
    return HttpResponse.json({ id: 'trg_1', kind: 'schedule', config: {}, filter: {}, inputs: {} })
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  posted.length = 0
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

  const builder = ['missions:start', 'playbooks:read', 'triggers:read', 'triggers:write']
  const ready = async () => {
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: /On a schedule/ })).toBeEnabled()
    })
  }

  it('validates the cron, says it runs in UTC and sends it as written', async () => {
    const user = userEvent.setup()
    await render(builder)
    await ready()
    await user.click(screen.getByRole('radio', { name: /On a schedule/ }))
    const cron = screen.getByLabelText('Cron expression')
    expect(screen.getByText('Every Monday, 8:00. Schedules run in UTC.')).toBeInTheDocument()
    await user.clear(cron)
    await user.type(cron, 'z')
    expect(screen.getByRole('button', { name: 'Create the trigger' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/Five fields/)
    await user.clear(cron)
    await user.type(cron, '30 6 * * 1-5')
    await user.click(screen.getByRole('button', { name: 'Create the trigger' }))
    await waitFor(() => {
      expect(posted).toEqual([
        {
          url: '/triggers',
          body: expect.objectContaining({ kind: 'schedule', config: { cron: '30 6 * * 1-5' }, filter: {} }),
        },
      ])
    })
  })

  it('does not start a mission when the playbook is cleared after choosing a schedule', async () => {
    const user = userEvent.setup()
    await render(builder)
    await ready()
    await user.click(screen.getByRole('radio', { name: /On a schedule/ }))
    await user.click(screen.getByRole('button', { name: 'Do not use the playbook' }))
    const create = screen.getByRole('button', { name: 'Create the trigger' })
    expect(create).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/need a playbook: pick one, or choose Now/)
    await user.click(create)
    expect(posted).toEqual([])
  })

  it('refuses an event trigger that would fire on every event', async () => {
    const user = userEvent.setup()
    await render(builder)
    await ready()
    await user.click(screen.getByRole('radio', { name: /On every new event/ }))
    const create = screen.getByRole('button', { name: 'Create the trigger' })
    expect(create).toBeDisabled()
    await user.type(screen.getByLabelText('Jira project'), 'SECOPS')
    expect(create).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('Event source'), 'webhook')
    expect(create).toBeDisabled()
    await user.click(screen.getByLabelText(/Every signed call to the webhook starts a mission/))
    expect(create).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('Event source'), 'jira')
    await user.click(create)
    await waitFor(() => {
      expect(posted).toEqual([
        {
          url: '/triggers',
          body: expect.objectContaining({ kind: 'jira', config: { project: 'SECOPS', label: 'z' } }),
        },
      ])
    })
  })
  it('shows the webhook URL and the one-time secret before leaving', async () => {
    server.use(
      http.post('*/api/v1/triggers', () =>
        HttpResponse.json({
          id: 'trg_2',
          kind: 'webhook',
          config: {},
          filter: {},
          inputs: {},
          webhook_url: 'https://z.example/api/v1/hooks/trg_2',
          secret: 'whsec-once',
        }),
      ),
    )
    const user = userEvent.setup()
    await render(builder)
    await ready()
    await user.click(screen.getByRole('radio', { name: /On every new event/ }))
    await user.selectOptions(screen.getByLabelText('Event source'), 'webhook')
    await user.click(screen.getByLabelText(/Every signed call to the webhook starts a mission/))
    await user.click(screen.getByRole('button', { name: 'Create the trigger' }))
    expect(await screen.findByDisplayValue('whsec-once')).toBeInTheDocument()
    expect(screen.getByDisplayValue('https://z.example/api/v1/hooks/trg_2')).toBeInTheDocument()
    expect(screen.queryByText('route: /tasks')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(await screen.findByText('route: /tasks')).toBeInTheDocument()
  })
})
