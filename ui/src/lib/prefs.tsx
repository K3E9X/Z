import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { readBoolPref, readPref, writePref } from './storage'

export const THEMES = ['night', 'day', 'slate', 'contrast'] as const
export const FONTS = ['plex', 'inter', 'editorial', 'geist'] as const
export const DENSITIES = ['comfortable', 'compact'] as const
export type Theme = (typeof THEMES)[number]
export type Font = (typeof FONTS)[number]
export type Density = (typeof DENSITIES)[number]

export const THEME_LABELS: Record<Theme, string> = {
  night: 'Night',
  day: 'Day',
  slate: 'Slate',
  contrast: 'High contrast',
}
export const FONT_LABELS: Record<Font, string> = {
  plex: 'IBM Plex Sans + Mono',
  inter: 'Inter + JetBrains Mono',
  editorial: 'Editorial (Newsreader titles)',
  geist: 'Geist + Instrument Serif',
}

interface Prefs {
  theme: Theme
  /** The theme actually applied (follow-system can override `theme`). */
  effectiveTheme: Theme
  followSystem: boolean
  font: Font
  density: Density
  setTheme: (t: Theme) => void
  setFollowSystem: (v: boolean) => void
  setFont: (f: Font) => void
  setDensity: (d: Density) => void
}

const PrefsContext = createContext<Prefs | null>(null)

function systemPrefersLight(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: light)').matches
    : false
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => readPref('theme', THEMES, 'night'))
  const [followSystem, setFollowState] = useState(() => readBoolPref('followSystem', false))
  const [font, setFontState] = useState<Font>(() => readPref('font', FONTS, 'plex'))
  const [density, setDensityState] = useState<Density>(() => readPref('density', DENSITIES, 'comfortable'))
  const [light, setLight] = useState(systemPrefersLight)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const on = (e: MediaQueryListEvent) => {
      setLight(e.matches)
    }
    mq.addEventListener('change', on)
    return () => {
      mq.removeEventListener('change', on)
    }
  }, [])

  // "Day by day, Night by night": follow the OS between day and night, keep slate/contrast as chosen otherwise.
  const effectiveTheme: Theme = followSystem ? (light ? 'day' : 'night') : theme

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = effectiveTheme
    root.dataset.font = font
    root.dataset.density = density
  }, [effectiveTheme, font, density])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    writePref('theme', t)
    // Choosing a theme explicitly stops following the system.
    setFollowState(false)
    writePref('followSystem', false)
  }, [])
  const setFollowSystem = useCallback((v: boolean) => {
    setFollowState(v)
    writePref('followSystem', v)
  }, [])
  const setFont = useCallback((f: Font) => {
    setFontState(f)
    writePref('font', f)
  }, [])
  const setDensity = useCallback((d: Density) => {
    setDensityState(d)
    writePref('density', d)
  }, [])

  const value = useMemo(
    () => ({
      theme,
      effectiveTheme,
      followSystem,
      font,
      density,
      setTheme,
      setFollowSystem,
      setFont,
      setDensity,
    }),
    [theme, effectiveTheme, followSystem, font, density, setTheme, setFollowSystem, setFont, setDensity],
  )
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>
}

export function usePrefs(): Prefs {
  const ctx = useContext(PrefsContext)
  if (!ctx) throw new Error('usePrefs outside PrefsProvider')
  return ctx
}
