import type { components } from './schema.gen'

export type Problem = components['schemas']['Problem']

export const PROBLEM = {
  unauthenticated: 'urn:z:problem:unauthenticated',
  stepUp: 'urn:z:problem:step-up-required',
  forbidden: 'urn:z:problem:forbidden',
  separation: 'urn:z:problem:separation-of-duties',
  notFound: 'urn:z:problem:not-found',
  conflict: 'urn:z:problem:conflict',
  locked: 'urn:z:problem:locked',
  validation: 'urn:z:problem:validation',
  rateLimited: 'urn:z:problem:rate-limited',
  budget: 'urn:z:problem:budget-exceeded',
  unavailable: 'urn:z:problem:unavailable',
} as const

/** Error thrown for every non-2xx API answer, carrying the RFC 9457 problem. */
export class ApiError extends Error {
  readonly status: number
  readonly problem: Problem

  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title)
    this.name = 'ApiError'
    this.status = status
    this.problem = problem
  }

  get type(): string {
    return this.problem.type
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** Normalise whatever the server (or a proxy) answered into a Problem. */
export function toProblem(status: number, body: unknown, statusText = ''): Problem {
  if (isRecord(body) && typeof body.type === 'string' && typeof body.title === 'string') {
    return {
      ...(body as Problem),
      status: typeof body.status === 'number' ? body.status : status,
    }
  }
  const generic: Record<number, [string, string]> = {
    401: [PROBLEM.unauthenticated, 'Sign-in required'],
    403: [PROBLEM.forbidden, 'Not allowed'],
    404: [PROBLEM.notFound, 'Not found'],
    502: [PROBLEM.unavailable, 'z-server is not reachable'],
    503: [PROBLEM.unavailable, 'Service unavailable'],
    504: [PROBLEM.unavailable, 'z-server did not answer in time'],
  }
  const [type, title] = generic[status] ?? ['about:blank', statusText || `HTTP ${status}`]
  const detail = typeof body === 'string' && body.length > 0 && body.length < 300 ? body : undefined
  return detail ? { type, title, status, detail } : { type, title, status }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

/**
 * The endpoint exists in the contract but this server does not serve it yet (the route is not
 * registered: Starlette answers 404 "Not Found" or 405, never a resource-level 404 which has no detail).
 */
export function isNotServed(e: unknown): boolean {
  if (!isApiError(e)) return false
  if (e.status === 405 || e.status === 501) return true
  return e.status === 404 && (e.problem.detail === 'Not Found' || e.problem.detail === 'Method Not Allowed')
}

export function isUnauthenticated(e: unknown): boolean {
  return isApiError(e) && e.status === 401 && e.type !== PROBLEM.stepUp
}

export function isStepUpRequired(e: unknown): boolean {
  return isApiError(e) && e.type === PROBLEM.stepUp
}

export function isForbidden(e: unknown): boolean {
  return isApiError(e) && e.status === 403
}

/** One line for a toast or an inline error. */
export function problemMessage(e: unknown): string {
  if (isApiError(e)) {
    const p = e.problem
    if (p.type === PROBLEM.forbidden && p.permission) return `You need the ${p.permission} permission.`
    if (p.errors && p.errors.length > 0) {
      const first = p.errors[0]
      if (first) return `${p.detail ?? p.title}: ${first.path ? `${first.path} ` : ''}${first.message}`
    }
    return p.detail ?? p.title
  }
  if (e instanceof Error) return e.message
  return 'Something went wrong.'
}
