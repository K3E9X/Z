import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { useLiveHealth } from '@/api/live'
import { Icon, ZMark } from '@/components/Icon'
import { useCurrentMe } from '@/lib/auth'
import { useHotkeys } from '@/lib/hotkeys'
import { CommandPalette } from './CommandPalette'
import { HelpDialog } from './HelpDialog'
import { IconRail, Sidebar } from './Navigation'
import { NAV } from './nav'
import { PaletteContext } from './palette-context'

function LiveBanner() {
  const live = useLiveHealth()
  return (
    <div aria-live="polite" className="contents">
      {live === 'reconnecting' && (
        <div className="flex h-7 flex-none items-center justify-center gap-2 border-b border-line bg-wait-bg text-sm text-wait">
          <span className="h-1.5 w-1.5 animate-zpulse rounded-full bg-wait" aria-hidden="true" />
          Live updates interrupted. Reconnecting and resuming from the last event…
        </div>
      )}
    </div>
  )
}

function AccountBanners() {
  const me = useCurrentMe()
  if (me.banners.length === 0) return null
  return (
    <div className="flex flex-none flex-col">
      {me.banners.map((b, i) => (
        <div
          key={i}
          role={b.kind === 'breakglass' ? 'alert' : 'status'}
          className={`flex min-h-7 items-center justify-center gap-2 border-b border-line px-4 text-sm ${
            b.kind === 'breakglass' ? 'bg-err-bg text-err' : 'bg-wait-bg text-wait'
          }`}
        >
          <Icon name={b.kind === 'local_account' || b.kind === 'breakglass' ? 'key' : 'bell'} size={12} />
          {b.text}
        </div>
      ))}
    </div>
  )
}

function MobileBar() {
  const [open, setOpen] = useState(false)
  return (
    <div className="md:hidden">
      <div className="flex h-12 items-center justify-between px-4">
        <Link to="/" aria-label="Mission control">
          <ZMark size={26} />
        </Link>
        <button
          type="button"
          aria-label="Menu"
          aria-expanded={open}
          onClick={() => {
            setOpen((v) => !v)
          }}
          className="flex h-9 w-9 items-center justify-center rounded-btn text-text-2"
        >
          <Icon name="menu" size={18} />
        </button>
      </div>
      {open && (
        <nav aria-label="Main navigation" className="flex flex-col border-b border-line px-3 pb-3">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              onClick={() => {
                setOpen(false)
              }}
              className="flex h-10 items-center gap-3 rounded-ctl px-2 text-text-2 no-underline"
            >
              <Icon name={n.icon} />
              {n.label}
            </Link>
          ))}
        </nav>
      )}
    </div>
  )
}

export function AppShell() {
  const path = useRouterState({ select: (s) => s.location.pathname })
  const navigate = useNavigate()
  const [palette, setPalette] = useState<{ open: boolean; initial: string }>({ open: false, initial: '' })
  const [help, setHelp] = useState(false)
  const full = path === '/'

  const api = useMemo(
    () => ({
      open: (initial = '') => {
        setPalette({ open: true, initial })
      },
      close: () => {
        setPalette({ open: false, initial: '' })
      },
      isOpen: palette.open,
      openHelp: () => {
        setHelp(true)
      },
    }),
    [palette.open],
  )

  const go = (to: string) => () => {
    void navigate({ to })
  }
  useHotkeys({
    'mod+k': () => {
      setPalette((p) => ({ open: !p.open, initial: '' }))
    },
    '?': () => {
      setHelp(true)
    },
    '/': () => {
      const el = document.querySelector<HTMLElement>('[data-search]')
      if (el) el.focus()
      else setPalette({ open: true, initial: '' })
    },
    'g m': go('/'),
    'g r': go('/review'),
    'g t': go('/tasks'),
    'g p': go('/playbooks'),
    'g a': go('/agents'),
    'g h': go('/history'),
    'g l': go('/learning'),
    'g c': go('/connectors'),
    'g i': go('/inference'),
    'g d': go('/docs'),
    'g s': go('/settings'),
    'g n': go('/missions/new'),
  })

  return (
    <PaletteContext.Provider value={api}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-btn focus:bg-inv-bg focus:px-3 focus:py-2 focus:text-inv-text"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen bg-bg text-text">
        <div className="hidden border-r border-line bg-panel md:flex">
          {full ? <Sidebar /> : <IconRail />}
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <AccountBanners />
          <LiveBanner />
          <MobileBar />
          <div id="main" className="flex min-w-0 flex-1 flex-col" tabIndex={-1}>
            <Outlet />
          </div>
        </div>
      </div>
      <CommandPalette
        open={palette.open}
        initial={palette.initial}
        onClose={() => {
          setPalette({ open: false, initial: '' })
        }}
      />
      <HelpDialog
        open={help}
        onClose={() => {
          setHelp(false)
        }}
      />
    </PaletteContext.Provider>
  )
}
