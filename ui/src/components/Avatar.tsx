import { useMemo } from 'react'
import { useAgents } from '@/api/queries'
import { usePrefs } from '@/lib/prefs'
import type { Agent, Avatar as AvatarT } from '@/api/types'
import { initials as toInitials } from '@/lib/format'

/** Initials in a hairline circle; the agent's (or user's) chosen color tints the letters only. */
export function Avatar({
  avatar,
  name,
  size = 26,
  className = '',
}: {
  avatar?: AvatarT | null
  name: string
  size?: number
  className?: string
}) {
  const { effectiveTheme } = usePrefs()
  const text = (avatar?.initials ?? toInitials(name)).slice(0, 2)
  const color = avatar?.color && isSafeColor(avatar.color) ? readable(avatar.color, effectiveTheme === 'day') : undefined
  return (
    <span
      aria-hidden="true"
      className={`inline-flex flex-none items-center justify-center rounded-full border border-line-2 bg-raised font-semibold ${className}`}
      style={{
        width: size,
        height: size,
        fontSize: Math.max(9.5, Math.round(size * 0.4)),
        color,
      }}
    >
      {text}
    </span>
  )
}

/** Keep the chosen color unless it would vanish on the theme background (WCAG contrast). */
export function readable(hex: string, light: boolean): string | undefined {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h.slice(0, 6)
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(full.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  const lum = 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0)
  // Panels are ~0.93 luminance in day and ~0.01 in the dark themes; require 4.5:1 (small text, WCAG AA).
  const contrast = light ? (0.93 + 0.05) / (lum + 0.05) : (lum + 0.05) / (0.012 + 0.05)
  return contrast >= 4.5 ? `#${full}` : undefined
}

/** Only plain hex colors from manifests reach a style property. */
function isSafeColor(c: string): boolean {
  return /^#[0-9a-fA-F]{3,8}$/.test(c)
}

export interface AgentIndex {
  bySlug: Map<string, Agent>
  get: (slug: string | null | undefined) => Agent | undefined
  label: (slug: string | null | undefined) => string
}

/** Resolve agent slugs (jobs, plan steps, actions) to their chosen name, handle and avatar. */
export function useAgentIndex(): AgentIndex {
  const q = useAgents()
  return useMemo(() => {
    const bySlug = new Map<string, Agent>()
    for (const a of q.data?.items ?? []) {
      if (a.name) bySlug.set(a.name, a)
      bySlug.set(a.id, a)
    }
    const get = (slug: string | null | undefined) => (slug ? bySlug.get(slug.replace(/^@/, '').split('@')[0] ?? slug) : undefined)
    return {
      bySlug,
      get,
      label: (slug) => {
        if (!slug) return '—'
        const a = get(slug)
        return a?.display_name ?? slug
      },
    }
  }, [q.data])
}

export function AgentName({
  slug,
  index,
  withAvatar = false,
  withHandle = false,
  size = 20,
  className = '',
}: {
  slug: string | null | undefined
  index: AgentIndex
  withAvatar?: boolean
  withHandle?: boolean
  size?: number
  className?: string
}) {
  const a = index.get(slug)
  const name = a?.display_name ?? slug ?? '—'
  return (
    <span className={`inline-flex min-w-0 items-center gap-2 ${className}`}>
      {withAvatar && <Avatar avatar={a?.avatar ?? null} name={name} size={size} />}
      <span className="truncate">{name}</span>
      {withHandle && a?.handle && <span className="font-mono text-sm text-text-3">@{a.handle}</span>}
    </span>
  )
}
