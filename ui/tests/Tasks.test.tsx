import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cronError } from '@/lib/triggers'
import { Tasks } from '@/screens/Tasks'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

const posted: unknown[] = []
const server = setupServer(
  http.post('*/api/v1/triggers', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    posted.push(body)
    return HttpResponse.json({ id: 'trg_1', ...body, inputs: {}, owner_user: 'alice', created_at: '' })
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

async function openDialog() {
  const user = userEvent.setup()
  await renderRoute('/tasks', '/tasks', Tasks, {
    me: makeMe(['tasks:read', 'triggers:read', 'triggers:write', 'missions:read', 'playbooks:read']),
    others: ['/missions/new', '/missions/$id', '/review'],
  })
  await user.click(await screen.findByRole('button', { name: 'Add a source' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add a source' })
  const add = within(dialog).getByRole('button', { name: 'Add source' })
  return { user, dialog, add }
}

describe('cronError', () => {
  it('accepts five-field expressions and explains the rest', () => {
    expect(cronError('0 8 * * 1')).toBeNull()
    expect(cronError('*/15 9-17 * * mon-fri')).toBeNull()
    expect(cronError('z')).toMatch(/Five fields/)
    expect(cronError('0 8 * *')).toMatch(/Five fields/)
    expect(cronError('0 8 * * x?')).toMatch(/day of week/)
  })
})

describe('Add a source', () => {
  it('keeps its own value per field and validates the schedule (in UTC)', async () => {
    const { user, dialog, add } = await openDialog()
    await user.selectOptions(within(dialog).getByLabelText('Source'), 'schedule')
    const cron = within(dialog).getByLabelText('Cron')
    expect(cron).toHaveValue('0 8 * * 1')
    expect(within(dialog).getByText(/Every Monday, 8:00\. Schedules run in UTC\./)).toBeInTheDocument()
    await user.clear(cron)
    await user.type(cron, '0 8 * *')
    expect(add).toBeDisabled()
    await user.type(cron, ' 1-5')
    expect(add).toBeEnabled()
    await user.click(add)
    await waitFor(() => {
      expect(posted).toEqual([expect.objectContaining({ kind: 'schedule', config: { cron: '0 8 * * 1-5' } })])
    })
  })

  it('refuses a source that would turn every event into a task', async () => {
    const { user, dialog, add } = await openDialog()
    expect(within(dialog).getByLabelText('Label')).toHaveValue('z')
    expect(add).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Project'), 'SECOPS')
    expect(add).toBeEnabled()
    await user.clear(within(dialog).getByLabelText('Label'))
    expect(add).toBeDisabled()
    await user.selectOptions(within(dialog).getByLabelText('Source'), 'gitlab')
    expect(within(dialog).getByLabelText('Assignee')).toHaveValue('')
    expect(add).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Assignee'), '@z-bot')
    await user.click(add)
    await waitFor(() => {
      expect(posted).toEqual([
        expect.objectContaining({ kind: 'gitlab', config: {}, filter: { assignee: 'z-bot' } }),
      ])
    })
  })

  it('sends the Jira project and label where z-server reads them', async () => {
    const { user, dialog, add } = await openDialog()
    await user.type(within(dialog).getByLabelText('Project'), 'SECOPS')
    await user.click(add)
    await waitFor(() => {
      expect(posted).toEqual([
        expect.objectContaining({ kind: 'jira', config: { project: 'SECOPS', label: 'z' }, filter: {} }),
      ])
    })
  })
})
