import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { ProposedAction } from '@/api/types'
import { ActionReview, batchItems } from '@/components/ActionReview'
import type { AgentIndex } from '@/components/Avatar'
import { makeMe, renderRouted } from './utils'

const agents: AgentIndex = { bySlug: new Map(), get: () => undefined, label: (s) => (s === 'ticket-manager' ? 'Ticket Manager' : (s ?? '')) }

function action(patch: Partial<ProposedAction> = {}): ProposedAction {
  return {
    id: 'act_1',
    job_id: 'job_1',
    mission_id: 'msn_1',
    agent: 'ticket-manager',
    connector: 'jira',
    operation: 'jira.issues.create',
    class: 'write',
    summary: 'Create SECOPS ticket: weak TLS on the monitoring portal',
    payload: { project: 'SECOPS', summary: 'Weak TLS', description: 'TLS 1.0 still accepted' },
    preview: { kind: 'issue', tracker: 'jira', project: 'SECOPS', title: 'Weak TLS on the monitoring portal', description: 'TLS 1.0 and 1.1 are still accepted.', priority: 'Low', labels: ['security'] },
    reversible: true,
    judge_verdict: { verdict: 'ok', checks: [{ name: 'injection', ok: true }] },
    runs_as: { scope: 'service', identity: 'svc-z-jira-rw' },
    status: 'pending',
    approvals: [],
    required_approvals: 1,
    idempotency_key: 'idem-1',
    created_at: new Date().toISOString(),
    ...patch,
  }
}

const calls: { url: string; body: unknown }[] = []
const server = setupServer(
  http.post('*/api/v1/actions/:id/approve', async ({ request, params }) => {
    calls.push({ url: `approve:${String(params.id)}`, body: await request.json() })
    return HttpResponse.json({ ...action(), status: 'approved' })
  }),
  http.post('*/api/v1/actions/:id/reject', async ({ request, params }) => {
    calls.push({ url: `reject:${String(params.id)}`, body: await request.json() })
    return HttpResponse.json({ ...action(), status: 'rejected' })
  }),
  http.get('*/api/v1/*', () => HttpResponse.json({ items: [], counts: { all: 0, writes: 0, questions: 0, suggestions: 0 }, next_cursor: null })),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  calls.length = 0
})
afterAll(() => {
  server.close()
})

describe('ActionReview', () => {
  it('shows who it runs as, what it calls, reversibility, the judge and the preview', async () => {
    await renderRouted(<ActionReview action={action()} agents={agents} />)
    expect(await screen.findByText('svc-z-jira-rw')).toBeInTheDocument()
    expect(screen.getByText('issues.create')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No injection detected')).toBeInTheDocument()
    expect(screen.getByText('Weak TLS on the monitoring portal')).toBeInTheDocument()
    expect(screen.getByText('One issue will be created.', { exact: false })).toBeInTheDocument()
  })

  it('approves with Cmd/Ctrl+Enter and sends the note for the agent', async () => {
    const user = userEvent.setup()
    await renderRouted(<ActionReview action={action()} agents={agents} />)
    await user.type(await screen.findByPlaceholderText(/merge the first two/), 'use component TLS')
    await user.click(document.body)
    await user.keyboard('{Control>}{Enter}{/Control}')
    await waitFor(() => {
      expect(calls).toEqual([{ url: 'approve:act_1', body: { comment: 'use component TLS' } }])
    })
  })

  it('rejects only with a reason (X opens the dialog)', async () => {
    const user = userEvent.setup()
    await renderRouted(<ActionReview action={action()} agents={agents} />)
    await screen.findByText('svc-z-jira-rw')
    await user.keyboard('x')
    const dialog = await screen.findByRole('dialog', { name: 'Reject this write' })
    const reject = dialog.querySelectorAll('button')
    const confirm = [...reject].find((b) => b.textContent === 'Reject')
    expect(confirm).toBeDisabled()
    await user.type(screen.getByPlaceholderText(/duplicate of SECOPS/), 'duplicate of SECOPS-1790')
    expect(confirm).toBeEnabled()
    if (confirm) await user.click(confirm)
    await waitFor(() => {
      expect(calls).toEqual([{ url: 'reject:act_1', body: { reason: 'duplicate of SECOPS-1790' } }])
    })
  })

  it('pre-excludes batch items the judge marked as duplicates', async () => {
    const user = userEvent.setup()
    const a = action({
      preview: { kind: 'json', data: { items: [{ key: 't1', title: 'Exposed dependency' }, { key: 't2', title: 'Weak TLS', duplicate_of: 'SECOPS-1790' }] } },
      summary: 'Create 2 tickets',
    })
    expect(batchItems(a).map((i) => [i.key, i.duplicateOf])).toEqual([
      ['t1', null],
      ['t2', 'SECOPS-1790'],
    ])
    await renderRouted(<ActionReview action={a} agents={agents} />)
    expect(await screen.findByLabelText('Include Weak TLS')).not.toBeChecked()
    expect(screen.getByText(/1 item will be written/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Approve/ }))
    await waitFor(() => {
      expect(calls).toEqual([{ url: 'approve:act_1', body: { exclude: ['t2'] } }])
    })
  })

  it('explains why a destructive write needs two approvers and blocks a second approval by the same person', async () => {
    await renderRouted(<ActionReview action={action({ class: 'destructive', required_approvals: 2, approvals: [{ by: 'alice', at: new Date().toISOString(), step_up: true }] })} agents={agents} />, { me: makeMe(['*']) })
    expect(await screen.findByText(/needs 2 different approvers \(1 of 2 so far\)/)).toBeInTheDocument()
    expect(screen.getByText(/You already approved this/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Approve/ })).toBeDisabled()
  })

  it('hides the decision for someone without actions:approve', async () => {
    await renderRouted(<ActionReview action={action()} agents={agents} />, { me: makeMe(['missions:read']) })
    expect(await screen.findByText('You need the actions:approve permission.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
  })
})
