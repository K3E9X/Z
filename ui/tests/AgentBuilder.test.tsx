import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Agent } from '@/api/types'
import { AgentBuilder } from '@/screens/AgentBuilder'
import { emptyApi, mission } from './fixtures'
import { makeMe, renderRoute } from './utils'

const template = {
  id: 'agt_tpl',
  name: 'triage',
  template: 'triage',
  display_name: 'Triage',
  handle: 'triage',
  version: '1.0.0',
  role: 'Triages findings',
  model: 'z-fast',
  skills: [],
  tools: [],
  autonomy: 'propose',
  limits: { max_steps: 50, max_tokens: 100_000, timeout_s: 3600 },
  status: 'available',
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-01T10:00:00Z',
} as Agent

const creates: unknown[] = []
const trials: { id: string; body: unknown }[] = []
const server = setupServer(
  http.get('*/api/v1/agents', () => HttpResponse.json({ items: [template], next_cursor: null })),
  http.post('*/api/v1/agents', async ({ request }) => {
    creates.push(await request.json())
    return HttpResponse.json(
      { ...template, id: 'agt_new', name: 'triage-2', display_name: 'Atlas' },
      { status: 201 },
    )
  }),
  http.post('*/api/v1/agents/:id/test', async ({ params, request }) => {
    trials.push({ id: String(params.id), body: await request.json() })
    return HttpResponse.json(mission({ id: 'msn_trial', title: 'Trial: Atlas', status: 'queued' }), {
      status: 201,
    })
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  creates.length = 0
  trials.length = 0
})
afterAll(() => {
  server.close()
})

const others = ['/agents', '/missions/$id', '/missions/new']

describe('Agent builder', () => {
  it('creates the agent, then tries it on a sample mission and opens the trial', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/agents/new', '/agents/new', AgentBuilder, { others })
    await screen.findByRole('option', { name: 'Triage' })
    await user.selectOptions(screen.getByLabelText('Template'), 'agt_tpl')
    await user.type(screen.getByLabelText('Name your team uses'), 'Atlas')
    await user.click(screen.getByRole('button', { name: 'Create Atlas' }))
    await waitFor(() => {
      expect(creates).toEqual([
        expect.objectContaining({ template: 'triage', display_name: 'Atlas', handle: 'atlas' }),
      ])
    })
    expect(await screen.findByRole('region', { name: 'Try it on a sample mission' })).toBeInTheDocument()
    const prompt = screen.getByLabelText('Sample mission')
    await user.clear(prompt)
    await user.type(prompt, 'List the three oldest open findings')
    await user.click(screen.getByRole('button', { name: 'Run a trial mission' }))
    await waitFor(() => {
      expect(trials).toEqual([{ id: 'agt_new', body: { prompt: 'List the three oldest open findings' } }])
    })
    const open = await screen.findByRole('link', { name: 'Open the trial mission' })
    expect(open).toHaveAttribute('href', '/missions/msn_trial')
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(await screen.findByText('route: /agents')).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ agent: 'agt_new' })
  })

  it('can skip the trial', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/agents/new', '/agents/new', AgentBuilder, { others })
    await screen.findByRole('option', { name: 'Triage' })
    await user.selectOptions(screen.getByLabelText('Template'), 'agt_tpl')
    await user.type(screen.getByLabelText('Name your team uses'), 'Atlas')
    await user.click(screen.getByRole('button', { name: 'Create Atlas' }))
    await user.click(await screen.findByRole('button', { name: 'Skip the trial' }))
    expect(await screen.findByText('route: /agents')).toBeInTheDocument()
    expect(router.state.location.search).toEqual({ agent: 'agt_new' })
    expect(trials).toEqual([])
  })

  it('offers neither creation nor trial without agents:write', async () => {
    await renderRoute('/agents/new', '/agents/new', AgentBuilder, { me: makeMe(['agents:read']), others })
    expect(await screen.findByText(/needs the agents:write permission/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run a trial mission' })).toBeNull()
  })
})
