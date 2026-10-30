import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { AgentIndex } from '@/components/Avatar'
import { TimelineRow } from '@/components/EventTimeline'
import { emptyTimeline, type TimelineItem } from '@/lib/timeline'
import { FilesTab } from '@/screens/MissionDetail'
import { renderWithProviders } from './utils'

const agents: AgentIndex = { bySlug: new Map(), get: () => undefined, label: (s) => s ?? '' }

const server = setupServer(
  http.get('*/api/v1/artifacts/:id', () =>
    HttpResponse.json(
      {
        type: 'urn:z:problem:forbidden',
        title: 'Not allowed',
        status: 403,
        detail: 'missions:read on team red',
      },
      { status: 403, headers: { 'content-type': 'application/problem+json' } },
    ),
  ),
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

const item = {
  kind: 'artifact',
  key: 'a1',
  seq: 1,
  ts: new Date().toISOString(),
  agent: null,
  artifact: { artifact_id: 'art_1', name: 'report.md', kind: 'report' },
} as unknown as TimelineItem

describe('artifact downloads', () => {
  it('tell the user when the download is refused (Files tab)', async () => {
    const user = userEvent.setup()
    renderWithProviders(<FilesTab timeline={{ ...emptyTimeline(), items: [item] }} />)
    await user.click(screen.getByRole('button', { name: 'Download' }))
    expect(
      await screen.findByText('report.md was not downloaded: missions:read on team red'),
    ).toBeInTheDocument()
  })

  it('tell the user when the download is refused (timeline)', async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <TimelineRow item={item} timeline={emptyTimeline()} agents={agents} showAgent={false} />,
    )
    await user.click(screen.getByRole('button', { name: 'Download' }))
    expect(
      await screen.findByText('report.md was not downloaded: missions:read on team red'),
    ).toBeInTheDocument()
  })
})
