import createClient, { type Middleware } from 'openapi-fetch'
import { ApiError, toProblem } from './problem'
import type { paths } from './schema.gen'

export const API_BASE = '/api/v1'
export const CSRF_COOKIE = '__Host-z_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS'])

/** Last CSRF token seen in GET /me (fallback when the cookie is not readable, e.g. in tests). */
let csrfFromMe: string | null = null

export function setCsrfToken(token: string | null | undefined): void {
  csrfFromMe = token ?? null
}

export function readCookie(name: string, cookie: string = typeof document === 'undefined' ? '' : document.cookie) {
  for (const part of cookie.split(';')) {
    const [k, ...rest] = part.trim().split('=')
    if (k === name) return decodeURIComponent(rest.join('='))
  }
  return null
}

/** Double-submit CSRF (docs/12 §1): the `__Host-z_csrf` cookie value echoed as a header on every non-GET. */
export function csrfToken(): string | null {
  return readCookie(CSRF_COOKIE) ?? csrfFromMe
}

export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (!SAFE.has(request.method.toUpperCase())) {
      const token = csrfToken()
      if (token) request.headers.set(CSRF_HEADER, token)
    }
    return request
  },
}

type Listener = (e: ApiError) => void
const listeners = new Set<Listener>()

/** The auth gate listens for 401s anywhere to show the sign-in screen. */
export function onApiError(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function emitApiError(e: ApiError): void {
  for (const fn of listeners) fn(e)
}

export function makeClient(fetchImpl?: (input: Request) => Promise<Response>) {
  const c = createClient<paths>({
    baseUrl: API_BASE,
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    // Resolve fetch at call time (test interceptors patch globalThis.fetch after import).
    fetch: fetchImpl ?? ((input: Request) => globalThis.fetch(input)),
  })
  c.use(csrfMiddleware)
  return c
}

export const api = makeClient()

interface Result<T> {
  data?: T
  error?: unknown
  response: Response
}

/** Await an openapi-fetch call and return its data, or throw an ApiError with the problem details. */
export async function unwrap<T>(p: Promise<Result<T>>): Promise<T> {
  let r: Result<T>
  try {
    r = await p
  } catch (e) {
    const err = new ApiError(0, {
      type: 'urn:z:problem:unavailable',
      title: 'z-server is not reachable',
      status: 0,
      detail: e instanceof Error ? e.message : undefined,
    })
    emitApiError(err)
    throw err
  }
  if (r.response.ok) return r.data as T
  const err = new ApiError(r.response.status, toProblem(r.response.status, r.error, r.response.statusText))
  emitApiError(err)
  throw err
}

/** Raw fetch for non-JSON endpoints (artifact download, exports, multipart), same CSRF and errors. */
export async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!SAFE.has(method)) {
    const token = csrfToken()
    if (token) headers.set(CSRF_HEADER, token)
  }
  let res: Response
  try {
    res = await fetch(API_BASE + path, { ...init, method, headers, credentials: 'same-origin' })
  } catch (e) {
    const err = new ApiError(0, {
      type: 'urn:z:problem:unavailable',
      title: 'z-server is not reachable',
      status: 0,
      detail: e instanceof Error ? e.message : undefined,
    })
    emitApiError(err)
    throw err
  }
  if (res.ok) return res
  let body: unknown
  try {
    const text = await res.text()
    try {
      body = JSON.parse(text) as unknown
    } catch {
      body = text
    }
  } catch {
    body = null
  }
  const err = new ApiError(res.status, toProblem(res.status, body, res.statusText))
  emitApiError(err)
  throw err
}
