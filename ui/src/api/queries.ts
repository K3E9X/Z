/**
 * Query keys and read hooks over the generated client. Every hook returns TanStack Query state;
 * screens render `isNotServed(error)` as "not available on this server yet", never invented numbers.
 */
import { keepPreviousData, QueryClient, useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { isApiError } from './problem'
import type { Job } from './types'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => {
        if (isApiError(error) && error.status >= 400 && error.status < 500) return false
        return count < 2
      },
    },
    mutations: { retry: false },
  },
})

/** Polling interval used where the contract has no SSE yet (or the global stream is not served). */
export const POLL_MS = 10_000

export const qk = {
  me: ['me'] as const,
  myNotifications: ['me', 'notifications'] as const,
  missions: (params: Record<string, unknown> = {}) => ['missions', params] as const,
  mission: (id: string) => ['mission', id] as const,
  missionJobs: (id: string) => ['mission', id, 'jobs'] as const,
  replay: (id: string) => ['mission', id, 'replay'] as const,
  job: (id: string) => ['job', id] as const,
  jobArtifacts: (id: string) => ['job', id, 'artifacts'] as const,
  review: (kind: string) => ['review', kind] as const,
  reviewAll: ['review'] as const,
  action: (id: string) => ['action', id] as const,
  agents: ['agents'] as const,
  agent: (id: string) => ['agent', id] as const,
  agentVersions: (id: string) => ['agent', id, 'versions'] as const,
  objectives: (agentId: string) => ['agent', agentId, 'objectives'] as const,
  objectiveHistory: (id: string) => ['objective', id, 'history'] as const,
  assignments: (agentId: string) => ['agent', agentId, 'assignments'] as const,
  skills: (params: Record<string, unknown> = {}) => ['skills', params] as const,
  skill: (id: string) => ['skill', id] as const,
  skillVersions: (id: string) => ['skill', id, 'versions'] as const,
  playbooks: (params: Record<string, unknown> = {}) => ['playbooks', params] as const,
  playbook: (id: string) => ['playbook', id] as const,
  tasks: ['tasks'] as const,
  triggers: ['triggers'] as const,
  sessions: ['sessions'] as const,
  connectors: ['connectors'] as const,
  connector: (id: string) => ['connector', id] as const,
  operations: (id: string) => ['connector', id, 'operations'] as const,
  myConnections: ['me', 'connections'] as const,
  mySessions: ['me', 'sessions'] as const,
  myTokens: ['me', 'tokens'] as const,
  channels: ['channels'] as const,
  inferenceHealth: ['inference', 'health'] as const,
  inferenceModels: ['inference', 'models'] as const,
  inferenceNodes: ['inference', 'nodes'] as const,
  inferenceCredential: ['inference', 'credential'] as const,
  docs: ['docs'] as const,
  doc: (slug: string) => ['docs', slug] as const,
  usage: (params: Record<string, unknown>) => ['usage', params] as const,
  budgets: ['budgets'] as const,
  providers: ['providers'] as const,
  routing: ['routing-policy'] as const,
  signals: ['signals'] as const,
  proposals: (status?: string) => ['proposals', status ?? 'all'] as const,
  proposal: (id: string) => ['proposal', id] as const,
  evals: (target?: string) => ['evals', target ?? 'all'] as const,
  evalRun: (id: string) => ['eval', id] as const,
  analytics: (params: Record<string, unknown>) => ['analytics', params] as const,
  audit: (params: Record<string, unknown>) => ['audit', params] as const,
  guardrails: ['guardrails'] as const,
  teams: ['teams'] as const,
  admin: {
    users: (params: Record<string, unknown>) => ['admin', 'users', params] as const,
    user: (id: string) => ['admin', 'user', id] as const,
    bindings: (params: Record<string, unknown>) => ['admin', 'bindings', params] as const,
    roles: ['admin', 'roles'] as const,
    permissions: ['admin', 'permissions'] as const,
    whoCan: (p: string) => ['admin', 'who-can', p] as const,
    groupMapping: ['admin', 'group-mapping'] as const,
    authSettings: ['admin', 'auth-settings'] as const,
    sessions: ['admin', 'sessions'] as const,
    tokens: ['admin', 'tokens'] as const,
    auditSettings: ['admin', 'audit-settings'] as const,
    notifications: ['admin', 'notifications'] as const,
  },
}

// ------------------------------------------------------------------ identity

export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: () => unwrap(api.GET('/me')),
    staleTime: 60_000,
    retry: false,
  })
}

// ------------------------------------------------------------------ missions

export interface MissionListParams {
  status?: string
  playbook?: string
  agent?: string
  q?: string
  limit?: number
}

export function useMissions(
  params: MissionListParams = {},
  opts: { live?: boolean; enabled?: boolean } = {},
) {
  return useQuery({
    queryKey: qk.missions(params as Record<string, unknown>),
    queryFn: () => unwrap(api.GET('/missions', { params: { query: { limit: 100, ...params } } })),
    refetchInterval: opts.live ? 5_000 : false,
    placeholderData: keepPreviousData,
    enabled: opts.enabled ?? true,
  })
}

export function useMission(id: string) {
  return useQuery({
    queryKey: qk.mission(id),
    queryFn: () => unwrap(api.GET('/missions/{id}', { params: { path: { id } } })),
  })
}

/** Page size of GET /missions/{id}/jobs (the contract's maximum) and a safety cap on pages followed. */
export const JOBS_PAGE = 200
const JOBS_MAX_PAGES = 25

/** Every job of a mission: pages of 200 following next_cursor (bounded), merged into one list. */
export async function fetchMissionJobs(id: string) {
  const items: Job[] = []
  let cursor: string | undefined
  for (let page = 0; page < JOBS_MAX_PAGES; page += 1) {
    const res = await unwrap(
      api.GET('/missions/{id}/jobs', {
        params: { path: { id }, query: { limit: JOBS_PAGE, ...(cursor ? { cursor } : {}) } },
      }),
    )
    items.push(...res.items)
    if (!res.next_cursor) return { items, next_cursor: null }
    cursor = res.next_cursor
  }
  return { items, next_cursor: cursor ?? null }
}

export function useMissionJobs(id: string, opts: { live?: boolean } = {}) {
  return useQuery({
    queryKey: qk.missionJobs(id),
    queryFn: () => fetchMissionJobs(id),
    refetchInterval: opts.live ? 5_000 : false,
  })
}

export function useReplay(id: string, enabled = true) {
  return useQuery({
    queryKey: qk.replay(id),
    queryFn: () => unwrap(api.GET('/missions/{id}/replay', { params: { path: { id } } })),
    enabled,
    staleTime: 60_000,
  })
}

export function useJobArtifacts(id: string | null) {
  return useQuery({
    queryKey: qk.jobArtifacts(id ?? ''),
    queryFn: () => unwrap(api.GET('/jobs/{id}/artifacts', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

// ------------------------------------------------------------------ review

export type ReviewKind = 'all' | 'writes' | 'questions' | 'suggestions'

export function useReview(kind: ReviewKind = 'all', opts: { mission?: string; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: opts.mission ? [...qk.review(kind), opts.mission] : qk.review(kind),
    queryFn: () =>
      unwrap(
        api.GET('/review', {
          params: { query: { kind, limit: 200, ...(opts.mission ? { mission: opts.mission } : {}) } },
        }),
      ),
    refetchInterval: POLL_MS,
    enabled: opts.enabled ?? true,
  })
}

export function useAction(id: string | null) {
  return useQuery({
    queryKey: qk.action(id ?? ''),
    queryFn: () => unwrap(api.GET('/actions/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

// ------------------------------------------------------------------ catalog

export function useAgents(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.agents,
    queryFn: () => unwrap(api.GET('/agents', { params: { query: { limit: 200 } } })),
    staleTime: 30_000,
    enabled: opts.enabled ?? true,
  })
}

export function useAgent(id: string | null) {
  return useQuery({
    queryKey: qk.agent(id ?? ''),
    queryFn: () => unwrap(api.GET('/agents/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useAgentVersions(id: string | null) {
  return useQuery({
    queryKey: qk.agentVersions(id ?? ''),
    queryFn: () => unwrap(api.GET('/agents/{id}/versions', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useObjectives(agentId: string | null) {
  return useQuery({
    queryKey: qk.objectives(agentId ?? ''),
    queryFn: () => unwrap(api.GET('/agents/{id}/objectives', { params: { path: { id: agentId ?? '' } } })),
    enabled: !!agentId,
  })
}

export function useAssignments(agentId: string | null) {
  return useQuery({
    queryKey: qk.assignments(agentId ?? ''),
    queryFn: () => unwrap(api.GET('/agents/{id}/assignments', { params: { path: { id: agentId ?? '' } } })),
    enabled: !!agentId,
  })
}

export function useSkills(params: { agent?: string; origin?: string } = {}) {
  return useQuery({
    queryKey: qk.skills(params),
    queryFn: () => unwrap(api.GET('/skills', { params: { query: { limit: 200, ...params } } })),
    staleTime: 30_000,
  })
}

export function useSkill(id: string | null) {
  return useQuery({
    queryKey: qk.skill(id ?? ''),
    queryFn: () => unwrap(api.GET('/skills/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useSkillVersions(id: string | null) {
  return useQuery({
    queryKey: qk.skillVersions(id ?? ''),
    queryFn: () => unwrap(api.GET('/skills/{id}/versions', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function usePlaybooks(params: { domain?: string; q?: string } = {}, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.playbooks(params),
    queryFn: () => unwrap(api.GET('/playbooks', { params: { query: { limit: 200, ...params } } })),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
    enabled: opts.enabled ?? true,
  })
}

export function usePlaybook(id: string | null) {
  return useQuery({
    queryKey: qk.playbook(id ?? ''),
    queryFn: () => unwrap(api.GET('/playbooks/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

// ------------------------------------------------------------------ work intake

export function useTasks() {
  return useQuery({
    queryKey: qk.tasks,
    queryFn: () => unwrap(api.GET('/tasks', { params: { query: { limit: 200 } } })),
    refetchInterval: POLL_MS,
  })
}

export function useTriggers(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.triggers,
    queryFn: () => unwrap(api.GET('/triggers')),
    enabled: opts.enabled ?? true,
  })
}

// ------------------------------------------------------------------ connectors

export function useConnectors(opts: { live?: boolean; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.connectors,
    queryFn: () => unwrap(api.GET('/connectors')),
    refetchInterval: opts.live ? 30_000 : false,
    enabled: opts.enabled ?? true,
  })
}

export function useConnector(id: string | null) {
  return useQuery({
    queryKey: qk.connector(id ?? ''),
    queryFn: () => unwrap(api.GET('/connectors/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useOperations(id: string | null) {
  return useQuery({
    queryKey: qk.operations(id ?? ''),
    queryFn: () => unwrap(api.GET('/connectors/{id}/operations', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useMyConnections() {
  return useQuery({ queryKey: qk.myConnections, queryFn: () => unwrap(api.GET('/me/connections')) })
}

export function useMySessions() {
  return useQuery({ queryKey: qk.mySessions, queryFn: () => unwrap(api.GET('/me/sessions')) })
}

export function useMyTokens() {
  return useQuery({ queryKey: qk.myTokens, queryFn: () => unwrap(api.GET('/me/tokens')) })
}

export function useChannels() {
  return useQuery({ queryKey: qk.channels, queryFn: () => unwrap(api.GET('/channels')) })
}

// ------------------------------------------------------------------ inference and usage

export function useInferenceHealth(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.inferenceHealth,
    queryFn: () => unwrap(api.GET('/inference/health')),
    refetchInterval: 30_000,
    enabled: opts.enabled ?? true,
  })
}

export function useInferenceModels() {
  return useQuery({
    queryKey: qk.inferenceModels,
    queryFn: () => unwrap(api.GET('/inference/models')),
    refetchInterval: 30_000,
  })
}

export function useInferenceNodes() {
  return useQuery({
    queryKey: qk.inferenceNodes,
    queryFn: () => unwrap(api.GET('/inference/nodes')),
    refetchInterval: 30_000,
  })
}

export type UsageGroup = 'model' | 'agent' | 'mission' | 'team' | 'user' | 'playbook'
export type UsageBucketSize = 'minute' | 'hour' | 'day'

export function useUsage(group_by: UsageGroup, from: string, to: string, bucket: UsageBucketSize) {
  const params = { group_by, from, to, bucket }
  return useQuery({
    queryKey: qk.usage(params),
    queryFn: () => unwrap(api.GET('/usage', { params: { query: params } })),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
}

export function useBudgets(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.budgets,
    queryFn: () => unwrap(api.GET('/budgets')),
    enabled: opts.enabled ?? true,
  })
}

export function useDocs() {
  return useQuery({ queryKey: qk.docs, queryFn: () => unwrap(api.GET('/docs')), staleTime: Infinity })
}

export function useDoc(slug: string) {
  return useQuery({
    queryKey: qk.doc(slug),
    queryFn: () => unwrap(api.GET('/docs/{slug}', { params: { path: { slug } } })),
    staleTime: Infinity,
  })
}

export function useInferenceCredential(enabled = true) {
  return useQuery({
    queryKey: qk.inferenceCredential,
    queryFn: () => unwrap(api.GET('/inference/credential')),
    enabled,
  })
}

export function useProviders() {
  return useQuery({ queryKey: qk.providers, queryFn: () => unwrap(api.GET('/providers')) })
}

export function useRoutingPolicy() {
  return useQuery({ queryKey: qk.routing, queryFn: () => unwrap(api.GET('/routing-policy')) })
}

// ------------------------------------------------------------------ learning

export function useSignals() {
  return useQuery({
    queryKey: qk.signals,
    queryFn: () => unwrap(api.GET('/signals', { params: { query: { limit: 100 } } })),
  })
}

export function useProposals(status?: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.proposals(status),
    queryFn: () =>
      unwrap(api.GET('/proposals', { params: { query: { limit: 100, ...(status ? { status } : {}) } } })),
    enabled: opts.enabled ?? true,
  })
}

export function useProposal(id: string | null) {
  return useQuery({
    queryKey: qk.proposal(id ?? ''),
    queryFn: () => unwrap(api.GET('/proposals/{id}', { params: { path: { id: id ?? '' } } })),
    enabled: !!id,
  })
}

export function useEvalRuns(target?: string) {
  return useQuery({
    queryKey: qk.evals(target),
    queryFn: () =>
      unwrap(api.GET('/evals', { params: { query: { limit: 100, ...(target ? { target } : {}) } } })),
  })
}

export function useAnalytics(from: string, to: string) {
  const params = { from, to }
  return useQuery({
    queryKey: qk.analytics(params),
    queryFn: () => unwrap(api.GET('/analytics', { params: { query: params } })),
  })
}

// ------------------------------------------------------------------ history and platform

export interface AuditParams {
  actor?: string
  action?: string
  target_type?: string
  target_id?: string
  result?: string
  ip?: string
  from?: string
  to?: string
}

export function useAudit(params: AuditParams, cursor?: string) {
  const query = { limit: 200, ...params, ...(cursor ? { cursor } : {}) }
  return useQuery({
    queryKey: qk.audit(query),
    queryFn: () => unwrap(api.GET('/audit', { params: { query } })),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS * 3,
  })
}

export function useGuardrails() {
  return useQuery({ queryKey: qk.guardrails, queryFn: () => unwrap(api.GET('/settings/guardrails')) })
}

export function useTeams(enabled = true) {
  return useQuery({ queryKey: qk.teams, queryFn: () => unwrap(api.GET('/teams')), enabled })
}
