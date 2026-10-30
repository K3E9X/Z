import { useSyncExternalStore } from 'react'

export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      if (typeof window.matchMedia !== 'function') return () => undefined
      const mq = window.matchMedia(query)
      mq.addEventListener('change', cb)
      return () => {
        mq.removeEventListener('change', cb)
      }
    },
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false),
    () => false,
  )
}

export const useIsMobile = () => useMedia('(max-width: 767px)')

/** Re-render every `ms` (elapsed clocks). */
export function useNow(ms = 1000): number {
  return useSyncExternalStore(
    (cb) => {
      const t = setInterval(cb, ms)
      return () => {
        clearInterval(t)
      }
    },
    () => Math.floor(Date.now() / ms) * ms,
    () => 0,
  )
}
