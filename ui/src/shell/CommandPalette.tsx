import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Command } from 'cmdk'
import { useEffect, useRef, useState } from 'react'
import { api, unwrap } from '@/api/client'
import { useAgents, useMissions, usePlaybooks, useReview } from '@/api/queries'
import { Avatar } from '@/components/Avatar'
import { Icon, ZMark, type IconName } from '@/components/Icon'
import { Dot } from '@/components/StatusMark'
import { useToast } from '@/components/toast'
import { useCan, useOptionalMe } from '@/lib/auth'
import { MOD } from '@/lib/hotkeys'
import { THEME_LABELS, THEMES, usePrefs } from '@/lib/prefs'
import { missionStatus } from '@/lib/status'
import { NAV, visibleNav } from './nav'

/**
 * Word-based matching instead of cmdk's fuzzy default (which matched "crit" against any value holding
 * those letters in order). Every typed word must appear; prefix-of-a-word matches rank above inner ones.
 */
const ASK_Z = 'ask z new mission'

export function paletteFilter(value: string, search: string, keywords?: string[]): number {
  // "New mission: <what you typed>" always matches and stays first: Ask Z is the default action.
  if (value.startsWith(ASK_Z)) return 1
  const hay = `${value} ${(keywords ?? []).join(' ')}`.toLowerCase()
  const words = search.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return 1
  let score = 1
  for (const w of words) {
    const at = hay.indexOf(w)
    if (at < 0) return 0
    const boundary = at === 0 || /[\s\-_.:/@#"“]/.test(hay[at - 1] ?? '')
    score *= hay.startsWith(w) ? 1 : boundary ? 0.8 : 0.4
  }
  return score
}

function Item({
  value,
  keywords,
  icon,
  children,
  hint,
  onSelect,
}: {
  value: string
  keywords?: string[]
  icon?: IconName
  children: React.ReactNode
  hint?: React.ReactNode
  onSelect: () => void
}) {
  return (
    <Command.Item
      value={value}
      keywords={keywords}
      onSelect={onSelect}
      className="flex h-[38px] cursor-pointer items-center gap-3 rounded-btn px-2.5 data-[selected=true]:bg-raised"
    >
      {icon && <Icon name={icon} size={15} className="text-text-2" />}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <span className="flex-none text-meta text-text-3">{hint}</span>}
    </Command.Item>
  )
}

/** ⌘K: Ask Z, agents, missions, actions (docs/09 screen 12). */
export function CommandPalette({
  open,
  initial,
  onClose,
}: {
  open: boolean
  initial: string
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [query, setQuery] = useState(initial)
  const navigate = useNavigate()
  const prefs = usePrefs()
  const toast = useToast()
  const qc = useQueryClient()
  const me = useOptionalMe()
  const canStart = useCan('missions:start')
  const canControl = useCan('missions:control')
  const agents = useAgents()
  const missions = useMissions({ status: 'running,waiting,paused,queued' })
  const playbooks = usePlaybooks()
  const review = useReview('all')

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) {
      setQuery(initial)
      if (typeof d.showModal === 'function') d.showModal()
      else d.setAttribute('open', '')
      // React's autoFocus ran while the dialog was still closed (not focusable): focus the input now.
      d.querySelector('input')?.focus()
    } else if (!open && d.open) {
      if (typeof d.close === 'function') d.close()
      else d.removeAttribute('open')
    }
  }, [open, initial])

  const go = (to: string, search?: Record<string, string>) => {
    onClose()
    void navigate({ to, ...(search ? { search } : {}) })
  }

  const runInBackground = async () => {
    const prompt = query.trim()
    if (!prompt || !canStart) return
    onClose()
    try {
      const m = await unwrap(api.POST('/missions', { body: { prompt, start: true } }))
      await qc.invalidateQueries({ queryKey: ['missions'] })
      toast.ok(`Mission started: ${m.title}`, {
        label: 'Open',
        run: () => {
          void navigate({ to: '/missions/$id', params: { id: m.id } })
        },
      })
    } catch (e) {
      toast.error(e, 'Could not start the mission')
    }
  }

  const pauseAll = async () => {
    onClose()
    const running = (missions.data?.items ?? []).filter(
      (m) => m.status === 'running' || m.status === 'waiting',
    )
    let ok = 0
    for (const m of running) {
      try {
        await unwrap(
          api.POST('/missions/{id}/pause', {
            params: { path: { id: m.id } },
            body: { reason: 'Pause all from the palette' },
          }),
        )
        ok++
      } catch {
        // reported below
      }
    }
    await qc.invalidateQueries({ queryKey: ['missions'] })
    toast.info(`Paused ${ok} of ${running.length} missions`)
  }

  const q = query.trim()
  const matchingPlaybooks = (playbooks.data?.items ?? []).slice(0, 40)
  const writes = review.data?.items.filter((i) => i.kind === 'action') ?? []

  return (
    <dialog
      ref={ref}
      aria-label="Command palette"
      className="mx-auto mt-[12vh] mb-auto w-[680px] max-w-[calc(100vw-24px)] overflow-hidden rounded-card border border-line-2 bg-panel p-0 text-text"
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
    >
      {open && (
        <Command
          label="Command palette"
          loop
          filter={paletteFilter}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              void runInBackground()
            }
          }}
        >
          <div className="flex items-center gap-3 border-b border-line px-[18px] py-3.5">
            <ZMark size={18} />
            <Command.Input
              value={query}
              onValueChange={setQuery}
              placeholder="Ask Z, or search agents, missions and actions"
              className="h-7 flex-1 border-none bg-transparent text-[16px] outline-none"
              autoFocus
            />
            <kbd className="rounded-[5px] border border-line-2 px-1.5 py-px font-mono text-micro text-text-3">
              esc
            </kbd>
          </div>
          <Command.List className="scrollbar-thin max-h-[min(460px,60vh)] overflow-auto p-2 [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:text-meta [&_[cmdk-group-heading]]:text-text-3">
            <Command.Empty className="px-3 py-6 text-center text-meta text-text-3">
              Nothing matches. Press ↵ on “New mission” to ask Z.
            </Command.Empty>
            {canStart && (
              <Command.Group heading="Ask Z">
                <Item
                  value={`${ASK_Z} ${q}`}
                  keywords={[q]}
                  icon="arrowRight"
                  hint="↵"
                  onSelect={() => {
                    go('/missions/new', q ? { prompt: q } : undefined)
                  }}
                >
                  {q ? `New mission: “${q}”` : 'New mission'}
                </Item>
                {matchingPlaybooks.map((p) => (
                  <Item
                    key={p.id}
                    value={`run playbook ${p.name ?? ''} ${p.description ?? ''}`}
                    keywords={[p.domain ?? '', ...(p.connectors ?? [])]}
                    icon="playbooks"
                    hint="Playbook"
                    onSelect={() => {
                      go('/missions/new', { playbook: p.name ?? p.id })
                    }}
                  >
                    Run playbook: {p.description ? titleOf(p.name ?? p.id) : (p.name ?? p.id)}
                  </Item>
                ))}
              </Command.Group>
            )}
            <Command.Group heading="Agents">
              {(agents.data?.items ?? []).map((a) => (
                <Command.Item
                  key={a.id}
                  value={`agent ${a.display_name ?? ''} ${a.handle ?? ''} ${a.name ?? ''}`}
                  onSelect={() => {
                    go('/agents', { agent: a.id })
                  }}
                  className="flex h-[38px] cursor-pointer items-center gap-3 rounded-btn px-2.5 data-[selected=true]:bg-raised"
                >
                  <Avatar avatar={a.avatar ?? null} name={a.display_name ?? a.name ?? '?'} size={22} />
                  <span className="min-w-0 flex-1 truncate">
                    {a.display_name} <span className="font-mono text-sm text-text-3">@{a.handle}</span>
                  </span>
                  <span className="flex items-center gap-1.5 text-meta text-text-3">
                    <Dot tone={a.status === 'working' ? 'run' : a.status === 'disabled' ? 'idle' : 'ok'} />
                    {a.status}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Missions">
              {(missions.data?.items ?? []).map((m) => {
                const st = missionStatus(m.status)
                const j = m.jobs
                return (
                  <Item
                    key={m.id}
                    value={`mission ${m.title} ${m.id}`}
                    icon="missionControl"
                    hint={j?.total ? `${j.succeeded ?? 0}/${j.total}` : st.label}
                    onSelect={() => {
                      onClose()
                      void navigate({ to: '/missions/$id', params: { id: m.id } })
                    }}
                  >
                    {m.title}
                  </Item>
                )
              })}
            </Command.Group>
            <Command.Group heading="Actions">
              {writes.length > 0 && (
                <Item
                  value="review approve writes"
                  icon="check"
                  hint="G R"
                  onSelect={() => {
                    go('/review', { kind: 'writes' })
                  }}
                >
                  Review the {writes.length} proposed {writes.length === 1 ? 'write' : 'writes'}
                </Item>
              )}
              {canControl && (
                <Item value="pause all agents" icon="pause" onSelect={() => void pauseAll()}>
                  Pause all agents
                </Item>
              )}
              <Item
                value="switch theme night day slate contrast"
                icon="theme"
                hint={THEMES.map((t) => THEME_LABELS[t].replace('High contrast', 'Contrast')).join(' · ')}
                onSelect={() => {
                  const i = THEMES.indexOf(prefs.effectiveTheme)
                  const next = THEMES[(i + 1) % THEMES.length] ?? 'night'
                  prefs.setTheme(next)
                }}
              >
                Switch theme
              </Item>
              {visibleNav(NAV, me).map((n) => (
                <Item
                  key={n.to}
                  value={`go to ${n.label}`}
                  icon={n.icon}
                  hint={n.shortcut}
                  onSelect={() => {
                    go(n.to)
                  }}
                >
                  Go to {n.label}
                </Item>
              ))}
              <Item value="go to evals" icon="chart" onSelect={() => go('/evals')}>
                Go to Evals
              </Item>
              <Item value="go to analytics" icon="chart" onSelect={() => go('/analytics')}>
                Go to Analytics
              </Item>
            </Command.Group>
          </Command.List>
          <div className="flex gap-4 border-t border-line px-[18px] py-2.5 text-sm text-text-3">
            <span>↑ ↓ move</span>
            <span>↵ open</span>
            {canStart && <span>{MOD}↵ run in background</span>}
          </div>
        </Command>
      )}
    </dialog>
  )
}

function titleOf(slug: string): string {
  return slug.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
