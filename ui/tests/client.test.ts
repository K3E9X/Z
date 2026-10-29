import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { api, CSRF_HEADER, readCookie, setCsrfToken, unwrap } from '@/api/client'
import {
  ApiError,
  isNotServed,
  isStepUpRequired,
  isUnauthenticated,
  problemMessage,
  toProblem,
} from '@/api/problem'

const seen: { method: string; csrf: string | null }[] = []
const server = setupServer(
  http.get('*/api/v1/missions', ({ request }) => {
    seen.push({ method: 'GET', csrf: request.headers.get(CSRF_HEADER) })
    return HttpResponse.json({ items: [], next_cursor: null })
  }),
  http.post('*/api/v1/missions/:id/pause', ({ request }) => {
    seen.push({ method: 'POST', csrf: request.headers.get(CSRF_HEADER) })
    return HttpResponse.json(
      {
        type: 'urn:z:problem:step-up-required',
        title: 'Re-authentication required',
        status: 401,
        step_up_url: '/api/v1/auth/step-up',
      },
      { status: 401, headers: { 'content-type': 'application/problem+json' } },
    )
  }),
  http.get('*/api/v1/tasks', () =>
    HttpResponse.json(
      { type: 'urn:z:problem:not-found', title: 'Not found', status: 404, detail: 'Not Found' },
      { status: 404 },
    ),
  ),
  http.get('*/api/v1/missions/:id', () =>
    HttpResponse.json({ type: 'urn:z:problem:not-found', title: 'Not found', status: 404 }, { status: 404 }),
  ),
  http.post('*/api/v1/actions/:id/approve', () =>
    HttpResponse.json(
      {
        type: 'urn:z:problem:forbidden',
        title: 'Not allowed',
        status: 403,
        detail: 'missing actions:approve',
        permission: 'actions:approve',
      },
      { status: 403 },
    ),
  ),
)

beforeAll(() => {
  server.listen()
})
afterEach(() => {
  server.resetHandlers()
  seen.length = 0
})
afterAll(() => {
  server.close()
})

describe('API client', () => {
  it('sends the CSRF token on non-GET requests only', async () => {
    setCsrfToken('tok-123')
    await unwrap(api.GET('/missions'))
    await unwrap(api.POST('/missions/{id}/pause', { params: { path: { id: 'm1' } }, body: {} })).catch(
      () => undefined,
    )
    expect(seen).toEqual([
      { method: 'GET', csrf: null },
      { method: 'POST', csrf: 'tok-123' },
    ])
  })

  it('turns problem+json into an ApiError and recognises step-up', async () => {
    const err = await unwrap(
      api.POST('/missions/{id}/pause', { params: { path: { id: 'm1' } }, body: {} }),
    ).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(isStepUpRequired(err)).toBe(true)
    expect(isUnauthenticated(err)).toBe(false)
  })

  it('tells a route that is not served from a resource that does not exist', async () => {
    const notServed = await unwrap(api.GET('/tasks')).catch((e: unknown) => e)
    const missing = await unwrap(api.GET('/missions/{id}', { params: { path: { id: 'nope' } } })).catch(
      (e: unknown) => e,
    )
    expect(isNotServed(notServed)).toBe(true)
    expect(isNotServed(missing)).toBe(false)
  })

  it('names the missing permission on 403', async () => {
    const err = await unwrap(
      api.POST('/actions/{id}/approve', { params: { path: { id: 'a1' } }, body: {} }),
    ).catch((e: unknown) => e)
    expect(problemMessage(err)).toBe('You need the actions:approve permission.')
  })

  it('normalises non-problem error bodies and reads cookies', () => {
    expect(toProblem(502, '<html>bad gateway</html>').title).toBe('z-server is not reachable')
    expect(toProblem(418, null, "I'm a teapot").title).toBe("I'm a teapot")
    expect(readCookie('__Host-z_csrf', 'a=1; __Host-z_csrf=abc%3D; b=2')).toBe('abc=')
  })
})
