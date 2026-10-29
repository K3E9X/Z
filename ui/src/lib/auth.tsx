import { createContext, useContext, type ReactNode } from 'react'
import type { PermissionName } from '@/api/permissions.gen'
import type { Me } from '@/api/types'

const MeContext = createContext<Me | null>(null)

export function MeProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>
}

export function useCurrentMe(): Me {
  const me = useContext(MeContext)
  if (!me) throw new Error('useCurrentMe outside MeProvider')
  return me
}

export function useOptionalMe(): Me | null {
  return useContext(MeContext)
}

/**
 * Whether the signed-in user holds `permission` (org-wide, or for `team` when given).
 * UI affordance only: z-server enforces every permission on every route.
 */
export function can(me: Me | null, permission: PermissionName, team?: string | null): boolean {
  if (!me) return false
  return me.permissions.some(
    (p) =>
      (p.permission === permission || p.permission === '*') &&
      (p.scope === 'org' || (team ? p.scope === `team:${team}` : p.scope.startsWith('team:'))),
  )
}

export function useCan(permission: PermissionName, team?: string | null): boolean {
  return can(useOptionalMe(), permission, team)
}

/** Seconds left on the step-up window, or 0 when a fresh re-authentication is needed. */
export function stepUpRemaining(me: Me | null, maxAgeS = 300, now = Date.now()): number {
  const at = me?.session.step_up_at
  if (!at) return 0
  const left = maxAgeS - (now - Date.parse(at)) / 1000
  return left > 0 ? Math.floor(left) : 0
}
