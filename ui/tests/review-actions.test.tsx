import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { useReview } from '@/api/queries'
import type { ProposedAction, ReviewItem } from '@/api/types'
import { ActionReview } from '@/components/ActionReview'
import type { AgentIndex } from '@/components/Avatar'
import { renderRouted } from './utils'

const agents: AgentIndex = { bySlug: new Map(), get: () => undefined, label: (s) => s ?? '' }

function action(id: string, summary: string): ProposedAction {
  return {
    id,
    job_id: 'job_1',
    mission_id: 'msn_1',
    agent: 'ticket-manager',
    connector: 'jira',
    operation: 'jira.issues.create',
    class: 'write',
    summary,
    payload: { project: 'SECOPS' },
    preview: { kind: 'json', data: { project: 'SECOPS' } },
    reversible: true,
    judge_verdict: { verdict: 'ok', checks: [] },
    runs_as: { scope: 'service', identity: 'svc-z-jira-rw' },
    status: 'pending',
    approvals: [],
    required_approvals: 1,
    idempotency_key: `idem-${id}`,
    created_at: new Date().toISOString(),
  }
}

const A = action('act_a', 'Write A: create SECOPS-1')
const B = action('act_b', 'Write B: create SECOPS-2')
const item = (a: ProposedAction): ReviewItem => ({
  kind: 'action',
  action: a,
  mission_title: 'Weekly triage',
  age_s: 60,
})

let steppedUp = false
let reviewItems: ReviewItem[] = []
const approvals: string[] = []
/** When set, GET /review answers with the list as it was at request time, once the gate opens. */
let gate: Promise<void> | null = null
/** When set, POST /actions/{id}/approve waits for it. */
let approveGate: Promise<void> | null = null
const server = setupServer(
  http.post('*/api/v1/actions/:id/approve', async ({ params }) => {
    approvals.push(String(params.id))
    if (approveGate) await approveGate
    if (!steppedUp) {
      return HttpResponse.json(
        { type: 'urn:z:problem:step-up-required', title: 'Step-up required', status: 401 },
        { status: 401 },
      )
    }
    reviewItems = reviewItems.filter((i) => i.action?.id !== params.id)
    return HttpResponse.json({ ...A, status: 'approved' })
  }),
  http.post('*/api/v1/auth/step-up', () => {
    steppedUp = true
    return HttpResponse.json({})
  }),
  http.get('*/api/v1/review', async () => {
    const items = [...reviewItems]
    if (gate) await gate
    return HttpResponse.json({
      items,
      counts: { all: items.length, writes: items.length, questions: 0, suggestions: 0 },
      next_cursor: null,
    })
  }),
  http.get('*/api/v1/*', () => HttpResponse.json({ items: [], next_cursor: null })),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  approvals.length = 0
  steppedUp = false
  gate = null
  approveGate = null
})
afterAll(() => {
  server.close()
})

/** The Review detail in miniature: the first item of the list, keyed by its id. */
function Harness() {
  const review = useReview('all')
  const first = review.data?.items[0]?.action
  return first ? <ActionReview key={first.id} action={first} agents={agents} /> : <p>empty</p>
}

describe('useReviewActions', () => {
  it('keeps a write in Review while its step-up is pending, so the hotkey cannot approve the next one', async () => {
    reviewItems = [item(A), item(B)]
    const user = userEvent.setup()
    await renderRouted(<Harness />)
    await screen.findByText('Write A: create SECOPS-1')
    await user.click(screen.getByRole('button', { name: /^Approve/ }))
    await screen.findByRole('dialog', { name: "Confirm it's you" })
    // The detail still shows A, and Ctrl+Enter inside the step-up dialog does not approve anything.
    expect(screen.getByText('Write A: create SECOPS-1')).toBeInTheDocument()
    expect(screen.queryByText('Write B: create SECOPS-2')).toBeNull()
    await user.keyboard('{Control>}{Enter}{/Control}')
    expect(approvals).toEqual(['act_a'])
    await user.click(screen.getByRole('button', { name: 'Re-authenticate' }))
    await waitFor(() => {
      expect(approvals).toEqual(['act_a', 'act_a'])
    })
    expect(await screen.findByText('Write B: create SECOPS-2')).toBeInTheDocument()
    expect(approvals).not.toContain('act_b')
  })

  it('cancels an in-flight refetch so it cannot put an approved write back', async () => {
    steppedUp = true
    reviewItems = [item(A), item(B)]
    const user = userEvent.setup()
    const { qc } = await renderRouted(<Harness />)
    await screen.findByText('Write A: create SECOPS-1')
    // A poll leaves while A is still pending on the server and answers while the approval is in flight.
    let openPoll: () => void = () => undefined
    gate = new Promise<void>((r) => {
      openPoll = r
    })
    let openApprove: () => void = () => undefined
    approveGate = new Promise<void>((r) => {
      openApprove = r
    })
    void qc.refetchQueries({ queryKey: ['review'] }).catch(() => undefined)
    await waitFor(() => {
      expect(qc.isFetching({ queryKey: ['review'] })).toBeGreaterThan(0)
    })
    await user.click(screen.getByRole('button', { name: /^Approve/ }))
    await waitFor(() => {
      expect(approvals).toEqual(['act_a'])
    })
    gate = null
    openPoll()
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText('Write A: create SECOPS-1')).toBeNull()
    expect(screen.getByText('Write B: create SECOPS-2')).toBeInTheDocument()
    openApprove()
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText('Write A: create SECOPS-1')).toBeNull()
  })
})
