import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Agent, Skill, SkillVersion } from '@/api/types'
import { Agents } from '@/screens/Agents'
import { emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

const agent = {
  id: 'agt_1',
  name: 'vega',
  display_name: 'Vega',
  handle: 'vega',
  version: '1.0.0',
  role: 'Triages findings',
  model: 'z-fast',
  skills: ['triage-findings'],
  tools: [],
  autonomy: 'propose',
  limits: { max_steps: 50, max_tokens: 100_000, timeout_s: 3600 },
  status: 'available',
  created_at: '2026-10-01T10:00:00Z',
  updated_at: '2026-10-01T10:00:00Z',
} as Agent

const skill: Skill = {
  id: 'skl_1',
  name: 'triage-findings',
  description: 'Triage scanner findings',
  version: '1.2.0',
  origin: 'pack',
  checksum: 'c3',
  agents: ['vega'],
  content: '# Triage\nGroup by asset.\nFlag duplicates.\n',
}
const CONTENT: Record<string, string> = {
  '1.0.0': '# Triage\nList the findings.\n',
  '1.1.0': '# Triage\nGroup by host.\n',
  '1.2.0': '# Triage\nGroup by asset.\nFlag duplicates.\n',
}
const versions: SkillVersion[] = ['1.2.0', '1.1.0', '1.0.0'].map((v) => ({
  version: v,
  checksum: `c${v}`,
  created_at: '2026-10-01T10:00:00Z',
  origin: 'pack',
}))
const fetched: string[] = []
const server = setupServer(
  http.get('*/api/v1/agents', () => HttpResponse.json({ items: [agent], next_cursor: null })),
  http.get('*/api/v1/agents/agt_1', () => HttpResponse.json(agent)),
  http.get('*/api/v1/skills', () => HttpResponse.json({ items: [skill], next_cursor: null })),
  http.get('*/api/v1/skills/skl_1', () => HttpResponse.json(skill)),
  http.get('*/api/v1/skills/skl_1/versions', () => HttpResponse.json(versions)),
  http.get('*/api/v1/skills/skl_1/versions/:version', ({ params }) => {
    const v = String(params.version)
    fetched.push(v)
    return HttpResponse.json({ ...versions.find((x) => x.version === v), content: CONTENT[v] })
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  fetched.length = 0
})
afterAll(() => {
  server.close()
})

async function openSkill(me = makeMe()) {
  const user = userEvent.setup()
  await renderRoute('/agents', '/agents?agent=agt_1&tab=skills', Agents, {
    me,
    others: ['/learning', '/missions/new', '/agents/new', '/evals'],
  })
  await user.click(await screen.findByRole('button', { name: /triage-findings/ }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('1.0.0')
  return { user, dialog }
}

describe('Skill detail → compare versions', () => {
  it('shows the diff between an older version and the current one, then between any two', async () => {
    const { user, dialog } = await openSkill()
    await user.click(within(dialog).getByRole('button', { name: 'Compare 1.1.0' }))
    const panel = await within(dialog).findByRole('region', { name: 'Compare versions' })
    const diff = await within(panel).findByRole('table', { name: 'Changes in SKILL.md' })
    expect(within(diff).getByText('Group by host.')).toBeInTheDocument()
    expect(within(diff).getByText('Group by asset.')).toBeInTheDocument()
    expect(within(diff).getByText('Flag duplicates.')).toBeInTheDocument()
    expect(fetched.sort()).toEqual(['1.1.0', '1.2.0'])
    await user.selectOptions(within(panel).getByLabelText('From version'), '1.0.0')
    await waitFor(() => {
      expect(within(panel).getByText('List the findings.')).toBeInTheDocument()
    })
    expect(within(panel).queryByText('Group by host.')).toBeNull()
  })

  it('compares the current version with the one before it', async () => {
    const { user, dialog } = await openSkill()
    await user.click(within(dialog).getByRole('button', { name: 'Compare 1.2.0' }))
    const panel = await within(dialog).findByRole('region', { name: 'Compare versions' })
    expect(within(panel).getByLabelText('From version')).toHaveValue('1.1.0')
    expect(within(panel).getByLabelText('To version')).toHaveValue('1.2.0')
    expect(await within(panel).findByText('Group by host.')).toBeInTheDocument()
  })

  it('lets a reader compare without offering a rollback', async () => {
    const { dialog } = await openSkill(makeMe(['agents:read', 'skills:read']))
    expect(within(dialog).getByRole('button', { name: 'Compare 1.0.0' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /Roll back/ })).toBeNull()
  })
})
