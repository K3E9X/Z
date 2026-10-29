import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router'
import { AppRoot } from './AppRoot'
import { NotFound } from './screens/NotFound'

const str = (v: unknown): string | undefined => (typeof v === 'string' && v.length > 0 ? v : undefined)

const rootRoute = createRootRoute({ component: AppRoot, notFoundComponent: NotFound })

const missionControl = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  validateSearch: (s: Record<string, unknown>): { selected?: string } => ({ selected: str(s.selected) }),
  component: lazyRouteComponent(() => import('./screens/MissionControl'), 'MissionControl'),
})

const newMission = createRoute({
  getParentRoute: () => rootRoute,
  path: '/missions/new',
  validateSearch: (s: Record<string, unknown>): { prompt?: string; playbook?: string; agent?: string } => ({
    prompt: str(s.prompt),
    playbook: str(s.playbook),
    agent: str(s.agent),
  }),
  component: lazyRouteComponent(() => import('./screens/NewMission'), 'NewMission'),
})

const missionDetail = createRoute({
  getParentRoute: () => rootRoute,
  path: '/missions/$id',
  validateSearch: (s: Record<string, unknown>): { tab?: string; job?: string } => ({
    tab: str(s.tab),
    job: str(s.job),
  }),
  component: lazyRouteComponent(() => import('./screens/MissionDetail'), 'MissionDetail'),
})

const takeOver = createRoute({
  getParentRoute: () => rootRoute,
  path: '/missions/$id/take-over/$jobId',
  component: lazyRouteComponent(() => import('./screens/TakeOver'), 'TakeOver'),
})

const replay = createRoute({
  getParentRoute: () => rootRoute,
  path: '/missions/$id/replay',
  validateSearch: (s: Record<string, unknown>): { seq?: number } => ({
    seq: typeof s.seq === 'number' ? s.seq : undefined,
  }),
  component: lazyRouteComponent(() => import('./screens/Replay'), 'Replay'),
})

const review = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review',
  validateSearch: (s: Record<string, unknown>): { kind?: string; item?: string } => ({
    kind: str(s.kind),
    item: str(s.item),
  }),
  component: lazyRouteComponent(() => import('./screens/Review'), 'Review'),
})

const tasks = createRoute({
  getParentRoute: () => rootRoute,
  path: '/tasks',
  component: lazyRouteComponent(() => import('./screens/Tasks'), 'Tasks'),
})

const playbooks = createRoute({
  getParentRoute: () => rootRoute,
  path: '/playbooks',
  validateSearch: (s: Record<string, unknown>): { domain?: string; q?: string; playbook?: string } => ({
    domain: str(s.domain),
    q: str(s.q),
    playbook: str(s.playbook),
  }),
  component: lazyRouteComponent(() => import('./screens/Playbooks'), 'Playbooks'),
})

const agents = createRoute({
  getParentRoute: () => rootRoute,
  path: '/agents',
  validateSearch: (s: Record<string, unknown>): { agent?: string; tab?: string } => ({
    agent: str(s.agent),
    tab: str(s.tab),
  }),
  component: lazyRouteComponent(() => import('./screens/Agents'), 'Agents'),
})

const agentBuilder = createRoute({
  getParentRoute: () => rootRoute,
  path: '/agents/new',
  validateSearch: (s: Record<string, unknown>): { from?: string } => ({ from: str(s.from) }),
  component: lazyRouteComponent(() => import('./screens/AgentBuilder'), 'AgentBuilder'),
})

const learning = createRoute({
  getParentRoute: () => rootRoute,
  path: '/learning',
  validateSearch: (s: Record<string, unknown>): { proposal?: string } => ({ proposal: str(s.proposal) }),
  component: lazyRouteComponent(() => import('./screens/Learning'), 'Learning'),
})

const evals = createRoute({
  getParentRoute: () => rootRoute,
  path: '/evals',
  validateSearch: (s: Record<string, unknown>): { target?: string; run?: string } => ({
    target: str(s.target),
    run: str(s.run),
  }),
  component: lazyRouteComponent(() => import('./screens/Evals'), 'Evals'),
})

const analytics = createRoute({
  getParentRoute: () => rootRoute,
  path: '/analytics',
  validateSearch: (s: Record<string, unknown>): { range?: string } => ({ range: str(s.range) }),
  component: lazyRouteComponent(() => import('./screens/Analytics'), 'Analytics'),
})

const connectors = createRoute({
  getParentRoute: () => rootRoute,
  path: '/connectors',
  validateSearch: (s: Record<string, unknown>): { connector?: string } => ({ connector: str(s.connector) }),
  component: lazyRouteComponent(() => import('./screens/Connectors'), 'Connectors'),
})

const connectorBuilder = createRoute({
  getParentRoute: () => rootRoute,
  path: '/connectors/new',
  validateSearch: (s: Record<string, unknown>): { kind?: string } => ({ kind: str(s.kind) }),
  component: lazyRouteComponent(() => import('./screens/ConnectorBuilder'), 'ConnectorBuilder'),
})

const inference = createRoute({
  getParentRoute: () => rootRoute,
  path: '/inference',
  validateSearch: (s: Record<string, unknown>): { range?: string } => ({ range: str(s.range) }),
  component: lazyRouteComponent(() => import('./screens/Inference'), 'Inference'),
})

const history = createRoute({
  getParentRoute: () => rootRoute,
  path: '/history',
  validateSearch: (
    s: Record<string, unknown>,
  ): { q?: string; range?: string; type?: string; seq?: number } => ({
    q: str(s.q),
    range: str(s.range),
    type: str(s.type),
    seq: typeof s.seq === 'number' ? s.seq : undefined,
  }),
  component: lazyRouteComponent(() => import('./screens/History'), 'History'),
})

const settings = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: (s: Record<string, unknown>): { section?: string } => ({ section: str(s.section) }),
  component: lazyRouteComponent(() => import('./screens/Settings'), 'Settings'),
})

const admin = createRoute({
  getParentRoute: () => rootRoute,
  path: '/admin',
  validateSearch: (s: Record<string, unknown>): { tab?: string; user?: string } => ({
    tab: str(s.tab),
    user: str(s.user),
  }),
  component: lazyRouteComponent(() => import('./screens/admin/Admin'), 'Admin'),
})

const signin = createRoute({
  getParentRoute: () => rootRoute,
  path: '/signin',
  validateSearch: (s: Record<string, unknown>): { error?: string; redirect?: string } => ({
    error: str(s.error),
    redirect: str(s.redirect),
  }),
})

const routeTree = rootRoute.addChildren([
  missionControl,
  newMission,
  missionDetail,
  replay,
  takeOver,
  review,
  tasks,
  playbooks,
  agents,
  agentBuilder,
  learning,
  evals,
  analytics,
  connectors,
  connectorBuilder,
  inference,
  history,
  settings,
  admin,
  signin,
])

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  scrollRestoration: true,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
