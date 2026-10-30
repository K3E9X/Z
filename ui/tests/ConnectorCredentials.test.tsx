import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { CredentialInfo } from '@/api/types'
import { Connectors } from '@/screens/Connectors'
import { connector, emptyApi } from './fixtures'
import { makeMe, renderRoute } from './utils'

// Built at runtime so secret scanners never see a literal.
const SECRET = ['tok', 'en-', 'abc', '123', 'xyz'].join('')

const READ: CredentialInfo = {
  id: 'cred_1',
  purpose: 'read',
  scope: 'service',
  backend: 'vault',
  ref: 'vault:kv/z/jira#read',
  identity: 'svc-z-read',
  rotated_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
}
const WRITE: CredentialInfo = {
  id: 'cred_2',
  purpose: 'write',
  scope: 'service',
  backend: 'sealed',
  ref: 'sealed:cred_2',
  identity: 'svc-z-write',
}
const withCreds = () => connector({ credentials: [READ, WRITE] })

const puts: { purpose: string; body: unknown }[] = []
const deletes: string[] = []
let stepUpFirst = false
let stepUps = 0
const server = setupServer(
  http.get('*/api/v1/connectors', () => HttpResponse.json([withCreds()])),
  http.get('*/api/v1/connectors/con_1', () => HttpResponse.json(withCreds())),
  http.put('*/api/v1/connectors/con_1/credentials/:purpose', async ({ params, request }) => {
    puts.push({ purpose: String(params.purpose), body: await request.json() })
    if (stepUpFirst) {
      stepUpFirst = false
      return HttpResponse.json(
        { type: 'urn:z:problem:step-up-required', title: 'Step-up required', status: 401 },
        { status: 401, headers: { 'content-type': 'application/problem+json' } },
      )
    }
    return HttpResponse.json(
      connector({
        credentials: [
          { ...READ, backend: 'sealed', ref: 'sealed:cred_1', rotated_at: new Date().toISOString() },
          WRITE,
        ],
      }),
    )
  }),
  http.delete('*/api/v1/connectors/con_1/credentials/:purpose', ({ params }) => {
    deletes.push(String(params.purpose))
    return HttpResponse.json(
      connector({ credentials: [READ, { ...WRITE, backend: 'vault', ref: 'vault:kv/z/jira#write' }] }),
    )
  }),
  http.post('*/api/v1/auth/step-up', () => {
    stepUps += 1
    return HttpResponse.json({ redirect_url: null, step_up_at: new Date().toISOString() })
  }),
  ...emptyApi(),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  puts.length = 0
  deletes.length = 0
  stepUpFirst = false
  stepUps = 0
})
afterAll(() => {
  server.close()
})

const others = ['/history', '/connectors/new', '/connectors/record/$id']
const group = (name: RegExp) => screen.getByRole('group', { name })

describe('Connector detail → secrets', () => {
  it('sends a typed secret once after a step-up, clears the field and never shows the value', async () => {
    const user = userEvent.setup()
    stepUpFirst = true
    const { container } = await renderRoute('/connectors', '/connectors', Connectors, { others })
    await screen.findByRole('region', { name: 'Secrets' })
    const read = group(/^Read secret/)
    expect(within(read).getByText('vault:kv/z/jira#read')).toBeInTheDocument()
    expect(within(read).getByText(/rotated 3 days ago/)).toBeInTheDocument()
    const field = within(read).getByLabelText('Type a secret')
    expect(field).toHaveAttribute('type', 'password')
    await user.type(field, SECRET)
    await user.type(within(read).getByLabelText('Username (basic auth, optional)'), 'svc-z-read')
    await user.click(within(read).getByRole('button', { name: 'Save the read secret' }))
    await user.click(await screen.findByRole('button', { name: 'Re-authenticate' }))
    await waitFor(() => {
      expect(puts).toHaveLength(2)
    })
    expect(stepUps).toBe(1)
    expect(puts[1]).toEqual({ purpose: 'read', body: { value: SECRET, username: 'svc-z-read' } })
    await waitFor(() => {
      expect(within(group(/^Read secret/)).getByText(/Typed in Z/)).toBeInTheDocument()
    })
    expect(within(group(/^Read secret/)).getByLabelText('Type a secret')).toHaveValue('')
    expect(within(group(/^Read secret/)).getByLabelText('Username (basic auth, optional)')).toHaveValue('')
    expect(container.textContent).not.toContain(SECRET)
  })

  it('goes back to the manifest reference for a typed secret only', async () => {
    const user = userEvent.setup()
    await renderRoute('/connectors', '/connectors', Connectors, { others })
    await screen.findByRole('region', { name: 'Secrets' })
    expect(
      within(group(/^Read secret/)).queryByRole('button', { name: 'Use the manifest reference again' }),
    ).toBeNull()
    await user.click(
      within(group(/^Write secret/)).getByRole('button', { name: 'Use the manifest reference again' }),
    )
    await waitFor(() => {
      expect(deletes).toEqual(['write'])
    })
    await waitFor(() => {
      expect(within(group(/^Write secret/)).getByText('vault:kv/z/jira#write')).toBeInTheDocument()
    })
  })

  it('offers no secret control without connectors:manage', async () => {
    await renderRoute('/connectors', '/connectors', Connectors, {
      me: makeMe(['connectors:read']),
      others,
    })
    expect(await screen.findByText('svc-z-read')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Secrets' })).toBeNull()
    expect(screen.queryByLabelText('Type a secret')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Use the manifest reference again' })).toBeNull()
  })
})
