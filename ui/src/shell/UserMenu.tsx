import { useNavigate } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import { api, unwrap } from '@/api/client'
import { Icon } from '@/components/Icon'
import { useToast } from '@/components/toast'
import { useCurrentMe } from '@/lib/auth'
import { THEME_LABELS, THEMES, usePrefs } from '@/lib/prefs'

export async function signOut(): Promise<void> {
  try {
    await unwrap(api.POST('/auth/logout'))
  } finally {
    window.location.assign('/signin')
  }
}

export function UserMenu({ onClose, className = '' }: { onClose: () => void; className?: string }) {
  const me = useCurrentMe()
  const prefs = usePrefs()
  const navigate = useNavigate()
  const toast = useToast()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const item = 'flex w-full items-center gap-2.5 rounded-ctl px-2.5 py-1.5 text-left hover:bg-raised focus-visible:bg-raised'
  return (
    <div
      ref={ref}
      role="menu"
      aria-label="Account"
      className={`absolute z-40 w-[240px] rounded-panel border border-line-2 bg-raised p-1.5 ${className}`}
    >
      <div className="px-2.5 pt-1.5 pb-2">
        <div className="font-medium">{me.user.name || me.user.username}</div>
        <div className="font-mono text-sm text-text-3">@{me.user.username}</div>
      </div>
      <div className="h-px bg-line" />
      <button
        type="button"
        role="menuitem"
        className={item}
        onClick={() => {
          onClose()
          void navigate({ to: '/settings' })
        }}
      >
        <Icon name="settings" /> Settings
      </button>
      <button
        type="button"
        role="menuitem"
        className={item}
        onClick={() => {
          const i = THEMES.indexOf(prefs.effectiveTheme)
          const next = THEMES[(i + 1) % THEMES.length] ?? 'night'
          prefs.setTheme(next)
          toast.info(`Theme: ${THEME_LABELS[next]}`)
        }}
      >
        <Icon name="theme" /> Switch theme
        <span className="ml-auto text-sm text-text-3">{THEME_LABELS[prefs.effectiveTheme]}</span>
      </button>
      <div className="h-px bg-line" />
      <button
        type="button"
        role="menuitem"
        className={item}
        onClick={() => {
          void signOut()
        }}
      >
        <Icon name="arrowRight" /> Sign out
      </button>
    </div>
  )
}
