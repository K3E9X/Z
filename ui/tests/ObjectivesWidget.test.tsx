import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Agent, Objective } from '@/api/types'
import { MissionControl } from '@/screens/MissionControl'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

function agent(id: string, name: string): Agent {
  return {
    id,
    name: id.replace('agt_', ''),
    display_name: name,
    handle: name.toLowerCase(),
    version: '1.0.0',
    role: 'Watches the backlog',
    model: 'z-fast',
    skills: [],
    tools: [],
    autonomy: 'propose',
    limits: { max_steps: 50, max_tokens: 100_000, timeout_s: 3600 },
    status: 'available',
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
  } as Agent
}

function objective(id: string, agentName: string, title: string, status: Objective['status']): Objective {
  return {
    id,
    agent: agentName,
    title,
    status,
    metric: { source: 'z', query: 'untriaged_findings', target: 20, direction: 'down' },
    cadence: '0 * * * *',
    last_value: 34,
    trend: [12, 20, 34],
  } as Objective
}

const OBJECTIVES: Record<string, Objective[]> = {
  agt_vega: [
    objective('obj_1', 'vega', 'Untriaged findings under 20', 'at_risk'),
    objective('obj_2', 'vega', 'Stale tickets under 5', 'on_track'),
  ],
  agt_atlas: [objective('obj_3', 'atlas', 'No failed mission this week', 'off_track')],
}
const requested: string[] = []
const server = setupServer(
  http.get('*/api/v1/agents', () =>
    HttpResponse.json({ items: [agent('agt_vega', 'Vega'), agent('agt_atlas', 'Atlas')], next_cursor: null }),
  ),
  http.get('*/api/v1/agents/:id/objectives', ({ params }) =>
    HttpResponse.json(OBJECTIVES[String(params.id)] ?? []),
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
  requested.length = 0
})
afterAll(() => {
  server.close()
})

describe('Mission control → Objectives widget', () => {
  it('lists the objectives at risk or off track and links to the agent’s Objectives tab', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/', '/', MissionControl, { others: ['/agents'] })
    const section = (await screen.findByRole('heading', { name: 'Objectives' })).closest('section')
    if (!section) throw new Error('no Objectives section')
    const w = within(section)
    expect(await w.findByText('Untriaged findings under 20')).toBeInTheDocument()
    expect(w.getByText('No failed mission this week')).toBeInTheDocument()
    expect(w.queryByText('Stale tickets under 5')).toBeNull()
    expect(w.getByText('2 need attention')).toBeInTheDocument()
    // Off track first.
    const links = w.getAllByRole('link')
    expect(links[0]).toHaveTextContent('Off track')
    await user.click(w.getByRole('link', { name: /Untriaged findings under 20/ }))
    expect(await screen.findByText('route: /agents')).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ agent: 'agt_vega', tab: 'objectives' })
  })

  it('is hidden without agents:read, and reads no objective', async () => {
    await renderRoute('/', '/', MissionControl, { me: makeMe(['missions:read']), others: ['/agents'] })
    await screen.findByRole('heading', { name: 'Finished today' })
    await new Promise((r) => setTimeout(r, 30))
    expect(screen.queryByRole('heading', { name: 'Objectives' })).toBeNull()
    expect(requested.filter((r) => r.endsWith('/objectives'))).toEqual([])
  })
})
