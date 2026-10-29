import { useEffect, useRef } from 'react'

/**
 * Keyboard shortcuts (docs/09 §6). Keys: 'j', 'x', '/', '?', 'escape', 'enter', 'mod+enter', 'mod+k',
 * and two-key sequences 'g m'. Single letters are ignored while typing in a field; `mod+…` and
 * 'escape' work everywhere.
 */
/** A handler that returns `false` did not handle the key: the event keeps its default action. */
export type HotkeyMap = Record<string, (e: KeyboardEvent) => unknown>

export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (target as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'submit', 'range'].includes(type)
  }
  return false
}

export function eventKey(e: KeyboardEvent): string {
  const mod = e.metaKey || e.ctrlKey
  let k = e.key.toLowerCase()
  if (k === 'esc') k = 'escape'
  if (k === ' ') k = 'space'
  if (k === '?' || (e.shiftKey && k === '/')) return '?'
  return mod ? `mod+${k}` : e.shiftKey && k.length === 1 && /[a-z]/.test(k) ? `shift+${k}` : k
}

export function useHotkeys(map: HotkeyMap, enabled = true): void {
  const ref = useRef(map)
  useEffect(() => {
    ref.current = map
  })
  useEffect(() => {
    if (!enabled) return
    let pending: string | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return
      const key = eventKey(e)
      const typing = isTyping(e.target)
      const global = key.startsWith('mod+') || key === 'escape'
      if (typing && !global) return
      // Inside an open modal dialog only the dialog's own handlers apply.
      if (
        !global &&
        document.querySelector('dialog[open]') &&
        !(e.target instanceof Node && document.querySelector('dialog[open]')?.contains(e.target))
      )
        return
      const bindings = ref.current
      if (pending) {
        const seq = `${pending} ${key}`
        pending = null
        if (timer) clearTimeout(timer)
        const fn = bindings[seq]
        if (fn) {
          if (fn(e) !== false) e.preventDefault()
          return
        }
      }
      if (!typing && Object.keys(bindings).some((b) => b.startsWith(`${key} `))) {
        pending = key
        timer = setTimeout(() => {
          pending = null
        }, 1200)
        return
      }
      const fn = bindings[key]
      if (fn && fn(e) !== false) e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (timer) clearTimeout(timer)
    }
  }, [enabled])
}

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)
export const MOD = isMac ? '⌘' : 'Ctrl '
