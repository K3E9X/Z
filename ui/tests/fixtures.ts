import { http, HttpResponse } from 'msw'
import type { Mission } from '@/api/types'

export function mission(patch: Partial<Mission> = {}): Mission {
  return {
    id: 'msn_1',
    title: 'Triage the weekly findings',
    inputs: {},
    connectors: [],
    data_class: 'C1',
    autonomy: 'propose',
    plan_first: false,
    status: 'running',
    created_by: 'alice',
    budget: { max_tokens: 100_000, timeout_s: 3600 },
    tokens_in: 0,
    tokens_out: 0,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    ...patch,
  }
}

export const emptyPage = { items: [], next_cursor: null }

/** GET operations whose 200 is a JSON array in contracts/openapi.yaml. */
const ARRAY_GETS = [
  '/me/sessions',
  '/me/tokens',
  '/jobs/:id/artifacts',
  '/agents/:id/versions',
  '/skills/:id/versions',
  '/agents/:id/objectives',
  '/objectives/:id/history',
  '/agents/:id/assignments',
  '/triggers',
  '/connectors',
  '/connectors/:id/operations',
  '/me/connections',
  '/channels',
  '/docs',
  '/inference/models',
  '/inference/nodes',
  '/budgets',
  '/providers',
  '/admin/role-bindings',
  '/admin/roles',
  '/admin/permissions',
  '/teams',
]

/** Fallback handlers (register last): empty arrays where the contract returns one, an empty page elsewhere. */
export function emptyApi() {
  return [
    ...ARRAY_GETS.map((p) => http.get(`*/api/v1${p}`, () => HttpResponse.json([]))),
    http.get('*/api/v1/inference/health', () =>
      HttpResponse.json({
        status: 'unknown',
        gateway: { ok: true },
        prometheus_configured: false,
        models: [],
        nodes: [],
      }),
    ),
    http.get('*/api/v1/review', () =>
      HttpResponse.json({ ...emptyPage, counts: { all: 0, writes: 0, questions: 0, suggestions: 0 } }),
    ),
    http.get('*/api/v1/*', () => HttpResponse.json(emptyPage)),
  ]
}
