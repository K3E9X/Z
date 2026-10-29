import { useEffect, useRef, useSyncExternalStore } from 'react'
import { openStream, type SseMessage, type StreamState } from './sse'

/** Registry of open streams, so the shell can show one "reconnecting" banner for all of them. */
const states = new Map<number, StreamState>()
const subs = new Set<() => void>()
let nextId = 1
let snapshot: StreamState = 'open'

function recompute() {
  const values = [...states.values()]
  const next: StreamState = values.includes('reconnecting') ? 'reconnecting' : 'open'
  if (next !== snapshot) {
    snapshot = next
    for (const s of subs) s()
  }
}

export function useLiveHealth(): StreamState {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb)
      return () => {
        subs.delete(cb)
      }
    },
    () => snapshot,
    () => 'open',
  )
}

export interface UseStreamOptions {
  enabled?: boolean
  lastEventId?: string | null
  onState?: (s: StreamState) => void
}

/** Subscribe to an SSE endpoint for the lifetime of the component. */
export function useStream(
  path: string | null,
  onMessage: (m: SseMessage) => void,
  opts: UseStreamOptions = {},
) {
  const handler = useRef(onMessage)
  const stateHandler = useRef(opts.onState)
  const lastIdRef = useRef(opts.lastEventId)
  useEffect(() => {
    handler.current = onMessage
    stateHandler.current = opts.onState
  })
  useEffect(() => {
    lastIdRef.current = opts.lastEventId
  }, [opts.lastEventId])
  const enabled = opts.enabled ?? true
  useEffect(() => {
    if (!path || !enabled) return
    const id = nextId++
    const handle = openStream({
      path,
      lastEventId: lastIdRef.current ?? null,
      onMessage: (m) => {
        handler.current(m)
      },
      onState: (s) => {
        if (s === 'closed' || s === 'unavailable') states.delete(id)
        else states.set(id, s)
        recompute()
        stateHandler.current?.(s)
      },
    })
    return () => {
      handle.close()
      states.delete(id)
      recompute()
    }
  }, [path, enabled])
}
