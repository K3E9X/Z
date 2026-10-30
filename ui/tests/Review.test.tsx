import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { ReviewItem } from '@/api/types'
import { Review } from '@/screens/Review'
import { emptyApi } from './fixtures'
import { renderRoute } from './utils'

function question(id: string, text: string): ReviewItem {
  return {
    kind: 'question',
    mission_title: 'Weekly triage',
    age_s: 30,
    question: {
      id,
      job_id: 'job_1',
      mission_id: 'msn_1',
      agent: 'vega',
      text,
      options: [],
      created_at: new Date().toISOString(),
    },
  }
}

const server = setupServer(
  http.get('*/api/v1/review', () =>
    HttpResponse.json({
      items: [question('q_1', 'Which project?'), question('q_2', 'Which severity?')],
      counts: { all: 2, writes: 0, questions: 2, suggestions: 0 },
      next_cursor: null,
    }),
  ),
  ...emptyApi(),
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

const selected = () => document.querySelector('[data-review-row][aria-current="true"]')?.textContent ?? ''

describe('Review keyboard', () => {
  it('lets the arrows scroll a focused detail pane, and move the list otherwise', async () => {
    const user = userEvent.setup()
    const { router } = await renderRoute('/review', '/review', Review, { others: ['/missions/$id'] })
    await waitFor(() => {
      expect(selected()).toContain('Which project?')
    })
    const pane = screen.getByRole('region', { name: 'Question' })
    pane.style.overflowY = 'auto'
    Object.defineProperty(pane, 'scrollHeight', { configurable: true, value: 900 })
    Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 300 })
    pane.focus()
    await user.keyboard('{ArrowDown}')
    expect(router.state.location.search).toEqual({})
    expect(selected()).toContain('Which project?')

    pane.blur()
    await user.keyboard('{ArrowDown}')
    await waitFor(() => {
      expect(selected()).toContain('Which severity?')
    })

    // In the list, the arrows move between rows and focus follows the selection.
    const rows = document.querySelectorAll<HTMLElement>('[data-review-row]')
    rows[1]?.focus()
    await user.keyboard('{ArrowUp}')
    await waitFor(() => {
      expect(selected()).toContain('Which project?')
    })
    expect(document.activeElement).toBe(rows[0])
  })
})
