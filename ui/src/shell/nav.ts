import type { PermissionName } from '@/api/permissions.gen'
import type { IconName } from '@/components/Icon'

export interface NavItem {
  to: string
  label: string
  icon: IconName
  /** Hidden when the user lacks this permission (server enforces it anyway). */
  permission?: PermissionName
  shortcut?: string
  group: 'main' | 'configure'
}

export const NAV: NavItem[] = [
  {
    to: '/',
    label: 'Mission control',
    icon: 'missionControl',
    group: 'main',
    shortcut: 'G M',
    permission: 'missions:read',
  },
  {
    to: '/review',
    label: 'Review',
    icon: 'review',
    group: 'main',
    shortcut: 'G R',
    permission: 'missions:read',
  },
  { to: '/tasks', label: 'Tasks', icon: 'tasks', group: 'main', shortcut: 'G T', permission: 'tasks:read' },
  {
    to: '/playbooks',
    label: 'Playbooks',
    icon: 'playbooks',
    group: 'main',
    shortcut: 'G P',
    permission: 'playbooks:read',
  },
  {
    to: '/history',
    label: 'History',
    icon: 'history',
    group: 'main',
    shortcut: 'G H',
    permission: 'audit:read_own',
  },
  {
    to: '/agents',
    label: 'Agents and skills',
    icon: 'agents',
    group: 'configure',
    shortcut: 'G A',
    permission: 'agents:read',
  },
  {
    to: '/learning',
    label: 'Learning',
    icon: 'learning',
    group: 'configure',
    shortcut: 'G L',
    permission: 'learning:read',
  },
  {
    to: '/connectors',
    label: 'Connectors',
    icon: 'connectors',
    group: 'configure',
    shortcut: 'G C',
    permission: 'connectors:read',
  },
  {
    to: '/inference',
    label: 'Inference and usage',
    icon: 'inference',
    group: 'configure',
    shortcut: 'G I',
    permission: 'usage:read',
  },
  { to: '/settings', label: 'Settings', icon: 'settings', group: 'configure', shortcut: 'G S' },
  { to: '/admin', label: 'Admin', icon: 'admin', group: 'configure', permission: 'users:manage' },
]

export function isActive(path: string, to: string): boolean {
  if (to === '/') return path === '/' || path.startsWith('/missions')
  return path === to || path.startsWith(`${to}/`)
}
