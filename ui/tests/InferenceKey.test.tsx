import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { InferenceKeyPanel } from '@/screens/InferenceKey'
import { renderWithProviders } from './utils'

const KEY = 'ixk_' + '0123456789abcdef'.repeat(4)
const puts: unknown[] = []
let status: Record<string, unknown> = { source: 'env' }
const server = setupServer(
  http.get('*/api/v1/inference/credential', () => HttpResponse.json(status)),
  http.put('*/api/v1/inference/credential', async ({ request }) => {
    puts.push(await request.json())
    status = {
      source: 'ui',
      fingerprint: 'a1b2c3d4e5f6',
      updated_by: 'alice',
      updated_at: '2026-10-29T14:00:00Z',
      check: { ok: true, model: 'z-fast', detail: 'z-fast answered', checked_at: '2026-10-29T14:00:01Z' },
    }
    return HttpResponse.json(status)
  }),
  http.get('*/api/v1/inference/health', () =>
    HttpResponse.json({ status: 'healthy', models: [], nodes: [] }),
  ),
)
beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  puts.length = 0
  status = { source: 'env' }
})
afterAll(() => server.close())

describe('InferenceKeyPanel', () => {
  it('sends a well-formed key once, then shows only its fingerprint', async () => {
    const user = userEvent.setup()
    const { container } = renderWithProviders(<InferenceKeyPanel />)
    expect(await screen.findByText('From the deployment (INFERENCE_API_KEY)')).toBeInTheDocument()
    const field = screen.getByLabelText('New inferX key')
    expect(field).toHaveAttribute('type', 'password')
    const save = screen.getByRole('button', { name: 'Save and check' })
    await user.type(field, 'sk-not-inferx')
    expect(save).toBeDisabled()
    expect(screen.getByText(/ixk_ followed by 64 hexadecimal/)).toBeInTheDocument()
    await user.clear(field)
    await user.click(field)
    await user.paste(KEY)
    await user.click(save)
    await waitFor(() => expect(puts).toEqual([{ api_key: KEY, test: true }]))
    expect(await screen.findByText('a1b2c3d4e5f6')).toBeInTheDocument()
    expect(screen.getByText(/Set from Z by alice/)).toBeInTheDocument()
    expect((screen.getByLabelText('New inferX key') as HTMLInputElement).value).toBe('') // not kept once sent
    expect(container.textContent).not.toContain(KEY)
  })
})
