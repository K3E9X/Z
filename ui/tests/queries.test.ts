import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { fetchMissionJobs, JOBS_PAGE } from '@/api/queries'

const requests: { limit: string | null; cursor: string | null }[] = []
const server = setupServer(
  http.get('*/api/v1/missions/msn_1/jobs', ({ request }) => {
    const q = new URL(request.url).searchParams
    requests.push({ limit: q.get('limit'), cursor: q.get('cursor') })
    const cursor = q.get('cursor')
    const page = cursor === null ? 0 : Number(cursor)
    const items = Array.from({ length: page < 2 ? 200 : 13 }, (_, i) => ({ id: `job_${page}_${i}` }))
    return HttpResponse.json({ items, next_cursor: page < 2 ? String(page + 1) : null })
  }),
)
beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  requests.length = 0
})
afterAll(() => {
  server.close()
})

describe('fetchMissionJobs', () => {
  it('asks for at most the contract maximum and follows next_cursor to the last page', async () => {
    const res = await fetchMissionJobs('msn_1')
    expect(JOBS_PAGE).toBe(200)
    expect(requests).toEqual([
      { limit: '200', cursor: null },
      { limit: '200', cursor: '1' },
      { limit: '200', cursor: '2' },
    ])
    expect(res.items).toHaveLength(413)
    expect(res.next_cursor).toBeNull()
  })
})
