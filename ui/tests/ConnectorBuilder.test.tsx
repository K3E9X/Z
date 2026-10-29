import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { ConnectorBuilder } from '@/screens/ConnectorBuilder'
import { renderWithProviders } from './utils'

const imports: Record<string, unknown>[] = []
const server = setupServer(
  http.post('*/api/v1/connectors/import', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>
    imports.push(body)
    return HttpResponse.json({
      manifest: { name: body.name, kind: 'cli', cli: body.cli, declared: body.declared },
      operations: [
        {
          id: `${String(body.name)}.merge_requests.list`,
          connector: body.name,
          source_ref: 'declared:merge_requests.list',
          description: 'Open merge requests of a project',
          class: 'read',
          classifier_class: 'read',
          exposed: false,
          proposed_exposed: true,
          review_status: 'pending_review',
        },
      ],
      warnings: [],
    })
  }),
)
beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  imports.length = 0
})
afterAll(() => server.close())

async function render(kind: string) {
  const { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } =
    await import('@tanstack/react-router')
  const root = createRootRoute()
  const page = createRoute({
    getParentRoute: () => root,
    path: '/connectors/new',
    validateSearch: (s: Record<string, unknown>): { kind?: string } => ({
      kind: typeof s.kind === 'string' ? s.kind : undefined,
    }),
    component: ConnectorBuilder,
  })
  const router = createRouter({
    routeTree: root.addChildren([page]),
    history: createMemoryHistory({ initialEntries: [`/connectors/new?kind=${kind}`] }),
  })
  renderWithProviders(<RouterProvider router={router as never} />)
  await router.load()
}

describe('ConnectorBuilder', () => {
  it('drafts a cli connector from its tool image and declared commands, without a base URL', async () => {
    const user = userEvent.setup()
    await render('cli')
    await user.type(await screen.findByLabelText('Name (slug)'), 'glab')
    expect(screen.queryByLabelText('Base URL')).toBeNull()
    const tool = screen.getByLabelText('Tool image and commands (JSON)')
    expect((tool as HTMLTextAreaElement).value).toContain('{{auth.secret}}')
    await user.click(screen.getByRole('button', { name: 'List operations' }))
    await waitFor(() => expect(imports).toHaveLength(1))
    const body = imports[0] ?? {}
    expect(body).toMatchObject({ source: 'cli', name: 'glab', data_class: 'C2' })
    expect(body).not.toHaveProperty('base_url')
    expect(body).not.toHaveProperty('docs')
    expect(body.cli).toMatchObject({ env: { GITLAB_TOKEN: '{{auth.secret}}' }, output: 'json' })
    expect(body.declared).toEqual([
      expect.objectContaining({
        id: 'merge_requests.list',
        command: ['glab', 'api', 'projects/{{args.project_id}}/merge_requests?state=opened'],
      }),
    ])
    expect(await screen.findByText('declared:merge_requests.list')).toBeInTheDocument()
  })

  it('does not call the server when the tool JSON is not valid', async () => {
    const user = userEvent.setup()
    await render('cli')
    await user.type(await screen.findByLabelText('Name (slug)'), 'glab')
    const tool = screen.getByLabelText('Tool image and commands (JSON)')
    await user.clear(tool)
    await user.type(tool, 'not json')
    await user.click(screen.getByRole('button', { name: 'List operations' }))
    expect(await screen.findByText(/"cli" \(an object\)/)).toBeInTheDocument()
    expect(imports).toHaveLength(0)
  })
})
