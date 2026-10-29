import { Link, useRouterState } from '@tanstack/react-router'
import { useState } from 'react'
import { Avatar } from '@/components/Avatar'
import { Icon, ZMark } from '@/components/Icon'
import { Dot } from '@/components/StatusMark'
import { can, useCurrentMe } from '@/lib/auth'
import { percent } from '@/lib/format'
import { MOD } from '@/lib/hotkeys'
import { useCommandPalette } from './palette-context'
import { isActive, NAV, visibleNav, type NavItem } from './nav'
import { useShellData } from './useShellData'
import { UserMenu } from './UserMenu'

function roleLine(me: ReturnType<typeof useCurrentMe>): string {
  const roles = me.user.effective_roles.map((r) => r.role)
  const uniq = [...new Set(roles)]
  if (me.user.local_kind) return `local · ${me.user.local_kind}`
  return uniq.length ? uniq.join(', ') : 'viewer'
}

function useBadges() {
  const d = useShellData()
  return (to: string): { text?: string; tone?: 'wait' | 'err' } | null => {
    switch (to) {
      case '/':
        return d.runningCount ? { text: String(d.runningCount) } : null
      case '/review':
        return d.reviewCount ? { text: String(d.reviewCount), tone: 'wait' } : null
      case '/agents':
        return d.agentCount ? { text: String(d.agentCount) } : null
      case '/learning':
        return d.learningCount ? { text: String(d.learningCount), tone: 'wait' } : null
      case '/connectors':
        return d.connectorAlert ? { tone: d.connectorAlert } : null
      case '/inference':
        return d.budgetPct !== null ? { text: percent(d.budgetPct) } : null
      default:
        return null
    }
  }
}

/** Full sidebar of Mission control (docs/09 §3). */
export function Sidebar() {
  const me = useCurrentMe()
  const path = useRouterState({ select: (s) => s.location.pathname })
  const badge = useBadges()
  const palette = useCommandPalette()
  const [menu, setMenu] = useState(false)
  const items = visibleNav(NAV, me)
  const canStart = can(me, 'missions:start')

  const row = (i: NavItem) => {
    const active = isActive(path, i.to)
    const b = badge(i.to)
    return (
      <Link
        key={i.to}
        to={i.to}
        aria-current={active ? 'page' : undefined}
        className={`flex h-8 items-center gap-2.5 rounded-ctl px-2.5 no-underline ${
          active ? 'bg-raised font-medium text-text' : 'text-text-2 hover:bg-raised hover:text-text'
        }`}
      >
        <Icon name={i.icon} />
        <span className="flex-1 truncate">{i.label}</span>
        {b?.text && (
          <span className={`font-mono text-sm ${b.tone === 'wait' ? 'text-wait' : 'text-text-3'}`}>
            {b.text}
          </span>
        )}
        {b && !b.text && b.tone && <Dot tone={b.tone} />}
      </Link>
    )
  }

  return (
    <nav
      aria-label="Main navigation"
      className="sticky top-0 flex h-screen w-[248px] flex-none flex-col gap-3.5 bg-panel px-3 py-3.5"
    >
      <div className="flex items-center gap-2.5 rounded-btn px-2 py-1.5">
        <ZMark />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-semibold">{me.workspace.name}</span>
          <span className="truncate text-sm text-text-3">
            {me.workspace.environment ?? 'workspace'}
            {me.user.teams.length > 0 && ` · ${me.user.teams.map((t) => t.name).join(', ')}`}
          </span>
        </span>
      </div>
      <div className="flex gap-1.5">
        {canStart ? (
          <Link
            to="/missions/new"
            className="flex h-[34px] flex-1 items-center justify-center gap-2 rounded-btn bg-inv-bg font-medium text-inv-text no-underline hover:opacity-90"
          >
            <Icon name="plus" size={14} strokeWidth={2} />
            New mission
          </Link>
        ) : (
          <span className="flex h-[34px] flex-1 items-center justify-center rounded-btn border border-line text-meta text-text-3">
            Read-only access
          </span>
        )}
        <button
          type="button"
          aria-label={`Search or ask (${MOD}K)`}
          title={`Search or ask (${MOD}K)`}
          onClick={() => {
            palette.open()
          }}
          className="flex h-[34px] w-[34px] items-center justify-center rounded-btn border border-line-2 text-text-2 hover:text-text"
        >
          <Icon name="search" />
        </button>
      </div>
      <div className="flex flex-col gap-px">{items.filter((i) => i.group === 'main').map(row)}</div>
      <div className="flex flex-col gap-px">
        <div className="px-2.5 pb-1.5 text-xs text-text-3">Configure</div>
        {items.filter((i) => i.group === 'configure').map(row)}
      </div>
      <div className="flex-1" />
      <div className="relative border-t border-line pt-2">
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={menu}
          onClick={() => {
            setMenu((v) => !v)
          }}
          className="flex w-full items-center gap-2.5 rounded-btn px-2.5 py-2 text-left hover:bg-raised"
        >
          <Avatar name={me.user.name || me.user.username} size={26} />
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate font-medium">{me.user.name || me.user.username}</span>
            <span className="truncate text-sm text-text-3">{roleLine(me)}</span>
          </span>
        </button>
        {menu && (
          <UserMenu
            onClose={() => {
              setMenu(false)
            }}
            className="bottom-full left-0 mb-1"
          />
        )}
      </div>
    </nav>
  )
}

/** 56 px icon rail for every other page, same destinations, current page marked. */
export function IconRail() {
  const me = useCurrentMe()
  const path = useRouterState({ select: (s) => s.location.pathname })
  const badge = useBadges()
  const d = useShellData()
  const [menu, setMenu] = useState(false)
  const items = visibleNav(NAV, me)
  const healthTone = d.health
    ? d.health.status === 'healthy'
      ? 'ok'
      : d.health.status === 'down'
        ? 'err'
        : 'wait'
    : null
  return (
    <nav
      aria-label="Main navigation"
      className="sticky top-0 flex h-screen w-14 flex-none flex-col items-center gap-1.5 bg-panel py-3.5"
    >
      <Link to="/" aria-label="Z · Mission control" className="mb-3 rounded-btn">
        <ZMark size={26} />
      </Link>
      {items.map((i, idx) => {
        const active = isActive(path, i.to)
        const b = badge(i.to)
        const prev = items[idx - 1]
        return (
          <div key={i.to} className="contents">
            {prev && prev.group !== i.group && <div className="my-1 h-px w-6 bg-line" aria-hidden="true" />}
            <Link
              to={i.to}
              aria-label={`${i.label}${b?.text ? ` (${b.text})` : ''}`}
              title={i.shortcut ? `${i.label} · ${i.shortcut}` : i.label}
              aria-current={active ? 'page' : undefined}
              className={`relative flex h-9 w-9 items-center justify-center rounded-btn ${
                active ? 'bg-raised text-text' : 'text-text-2 hover:bg-raised hover:text-text'
              }`}
            >
              <Icon name={i.icon} size={16} />
              {i.to === '/review' && b?.text && (
                <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-wait px-1 font-mono text-[9.5px] font-semibold text-bg">
                  {b.text}
                </span>
              )}
              {i.to !== '/review' && b?.tone && (
                <span
                  className={`absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full ${b.tone === 'err' ? 'bg-err' : 'bg-wait'}`}
                  aria-hidden="true"
                />
              )}
            </Link>
          </div>
        )
      })}
      <div className="flex-1" />
      {healthTone && (
        <Link
          to="/inference"
          aria-label={`inferX ${d.health?.status ?? ''}`}
          title={`inferX ${d.health?.status ?? ''}`}
          className="flex h-8 w-8 items-center justify-center rounded-btn hover:bg-raised"
        >
          <Dot tone={healthTone} />
        </Link>
      )}
      <div className="relative">
        <button
          type="button"
          aria-label="Account menu"
          aria-haspopup="menu"
          aria-expanded={menu}
          onClick={() => {
            setMenu((v) => !v)
          }}
          className="rounded-full"
        >
          <Avatar name={me.user.name || me.user.username} size={28} />
        </button>
        {menu && (
          <UserMenu
            onClose={() => {
              setMenu(false)
            }}
            className="bottom-0 left-full ml-2"
          />
        )}
      </div>
    </nav>
  )
}
