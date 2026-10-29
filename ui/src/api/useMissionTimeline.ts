import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { ZEvent } from './events.gen'
import { emptyTimeline, parseEvent, reduceEvents, type TimelineState } from '@/lib/timeline'
import { useStream } from './live'
import { qk } from './queries'
import type { StreamState } from './sse'

type Action = { type: 'events'; events: ZEvent[] } | { type: 'reset' }

function reducer(state: TimelineState, action: Action): TimelineState {
  if (action.type === 'reset') return emptyTimeline()
  return reduceEvents(state, action.events)
}

/**
 * Live mission timeline over SSE (`GET /missions/{id}/events`, resumes with Last-Event-ID).
 * Events are batched per animation frame so a burst of 500 events renders once.
 */
export function useMissionTimeline(missionId: string | null, opts: { enabled?: boolean } = {}) {
  const [state, dispatch] = useReducer(reducer, undefined, emptyTimeline)
  const [stream, setStream] = useState<StreamState>('connecting')
  const qc = useQueryClient()
  const buffer = useRef<ZEvent[]>([])
  const frame = useRef<number | null>(null)
  const lastStatus = useRef<string | null>(null)

  useEffect(() => {
    dispatch({ type: 'reset' })
    lastStatus.current = null
  }, [missionId])

  const flush = useCallback(() => {
    frame.current = null
    const events = buffer.current
    buffer.current = []
    if (events.length) dispatch({ type: 'events', events })
    // Status, approvals and questions change server-side aggregates: refresh them.
    if (events.some((e) => e.type === 'status' || e.type === 'action_proposed' || e.type === 'question' || e.type === 'action_status' || e.type === 'question_answered' || e.type === 'plan')) {
      if (missionId) void qc.invalidateQueries({ queryKey: qk.mission(missionId) })
      void qc.invalidateQueries({ queryKey: qk.reviewAll })
    }
  }, [qc, missionId])

  useStream(
    missionId ? `/missions/${encodeURIComponent(missionId)}/events` : null,
    (m) => {
      const e = parseEvent(m.event, m.data)
      if (!e) return
      buffer.current.push(e)
      if (frame.current === null) {
        frame.current =
          typeof requestAnimationFrame === 'function'
            ? requestAnimationFrame(flush)
            : (setTimeout(flush, 16) as unknown as number)
      }
    },
    { enabled: opts.enabled ?? true, onState: setStream },
  )

  useEffect(
    () => () => {
      if (frame.current !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame.current)
    },
    [],
  )

  return { timeline: state, stream }
}
