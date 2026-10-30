import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { TakeOver } from '@/screens/TakeOver'
import { renderWithProviders } from './utils'

const BASE = '*/api/v1/missions/msn_1/take-over/job_1'
let mode: 'read_only' | 'read_write' = 'read_write'
const calls: { what: string; body?: unknown }[] = []
const server = setupServer(
  http.get(`${BASE}/files`, () =>
    HttpResponse.json({
      job_id: 'job_1',
      mode,
      by: 'bob',
      expires_at: new Date(Date.now() + 1_800_000).toISOString(),
      truncated: false,
      files: [
        { path: 'draft.md', size: 11, origin: 'agent', deleted: false },
        { path: 'notes.md', size: 9, origin: 'staged', deleted: false },
        { path: 'old.txt', size: 3, origin: 'agent', deleted: true },
      ],
    }),
  ),
  http.get(`${BASE}/file`, ({ request }) => {
    const path = new URL(request.url).searchParams.get('path')
    return HttpResponse.json({
      path,
      encoding: 'utf-8',
      content: path === 'draft.md' ? 'first draft' : '# Notes',
      size: 11,
      origin: 'agent',
    })
  }),
  http.put(`${BASE}/file`, async ({ request }) => {
    const body = await request.json()
    calls.push({ what: 'put', body })
    return HttpResponse.json({ path: 'draft.md', size: 12, origin: 'human', deleted: false })
  }),
  http.post(`${BASE}/finish`, async ({ request }) => {
    calls.push({ what: 'finish', body: await request.json() })
    return HttpResponse.json({ job_id: 'job_1', edited: ['draft.md'], restarted: true, resumed: true })
  }),
  http.get('*/api/v1/missions/msn_1', () => HttpResponse.json({ id: 'msn_1', title: 'Summarise the notes' })),
)
beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  calls.length = 0
  mode = 'read_write'
})
afterAll(() => server.close())

async function render() {
  const { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } =
    await import('@tanstack/react-router')
  const root = createRootRoute()
  const page = createRoute({
    getParentRoute: () => root,
    path: '/missions/$id/take-over/$jobId',
    component: TakeOver,
  })
  const detail = createRoute({
    getParentRoute: () => root,
    path: '/missions/$id',
    component: () => <p>detail</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([page, detail]),
    history: createMemoryHistory({ initialEntries: ['/missions/msn_1/take-over/job_1'] }),
  })
  renderWithProviders(<RouterProvider router={router as never} />)
  await router.load()
}

describe('TakeOver', () => {
  it('lists the workspace by origin, edits a file and ends with a restart', async () => {
    const user = userEvent.setup()
    await render()
    const editor = await screen.findByLabelText('Content of draft.md')
    expect(await screen.findByDisplayValue('first draft')).toBe(editor)
    expect(screen.getByText('notes.md').nextElementSibling?.textContent).toBe('input')
    expect(screen.getByText('draft.md', { selector: 'button span' }).nextElementSibling?.textContent).toBe(
      'agent',
    )
    expect(screen.getByText('old.txt').closest('button')?.className).toContain('line-through')
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    await user.type(editor, '!')
    await user.click(save)
    await waitFor(() => {
      expect(calls).toEqual([
        { what: 'put', body: { path: 'draft.md', content: 'first draft!', encoding: 'utf-8' } },
      ])
    })
    await user.click(screen.getByRole('button', { name: 'End and resume' }))
    await waitFor(() => {
      expect(calls.at(-1)).toEqual({ what: 'finish', body: { resume: true } })
    })
    expect(await screen.findByText('detail')).toBeInTheDocument()
  })

  it('is read-only until the person asks to edit', async () => {
    mode = 'read_only'
    await render()
    const editor = await screen.findByLabelText('Content of draft.md')
    expect(editor).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Edit files' })).toBeInTheDocument()
  })
})
