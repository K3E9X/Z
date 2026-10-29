import { afterEach, describe, expect, it, vi } from 'vitest'
import { onApiError } from '@/api/client'
import { openStream, SseParser, type StreamState } from '@/api/sse'

describe('SseParser', () => {
  it('parses named events, multi-line data, ids, comments and retry across chunks', () => {
    const p = new SseParser()
    const out = [
      ...p.push('retry: 3000\n\n: keepalive\n\nid: 1\nevent: plan\ndata: {"a":'),
      ...p.push('1}\n\nid: 2\r\nevent: message\r\ndata: line one\r\ndata: line two\r\n\r\n'),
      ...p.push('data: no id\n\n'),
    ]
    expect(p.retry).toBe(3000)
    expect(out).toEqual([
      { id: '1', event: 'plan', data: '{"a":1}' },
      { id: '2', event: 'message', data: 'line one\nline two' },
      { id: '2', event: 'message', data: 'no id' },
    ])
  })
})

function sseResponse(chunks: string[], close = true): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch))
      if (close) c.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

describe('openStream', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reconnects after a drop and resumes with Last-Event-ID', async () => {
    const seen: string[] = []
    const states: StreamState[] = []
    const calls: (string | null)[] = []
    const fetchImpl = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      const h = new Headers(init?.headers)
      calls.push(h.get('Last-Event-ID'))
      if (calls.length === 1) return Promise.resolve(sseResponse(['id: 41\nevent: status\ndata: {}\n\n', 'id: 42\nevent: usage\ndata: {}\n\n']))
      return Promise.resolve(sseResponse(['id: 43\nevent: plan\ndata: {}\n\n'], false))
    })
    const h = openStream({ path: '/missions/m/events', fetchImpl: fetchImpl as unknown as typeof fetch, onMessage: (m) => seen.push(m.id ?? ''), onState: (s) => states.push(s), maxDelayMs: 5 })
    await vi.waitFor(() => {
      expect(seen).toEqual(['41', '42', '43'])
    })
    expect(calls).toEqual([null, '42'])
    expect(states).toContain('reconnecting')
    expect(h.lastEventId).toBe('43')
    h.close()
  })

  it('stops with "unavailable" when the route is not served (404 Not Found)', async () => {
    const states: StreamState[] = []
    const fetchImpl = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ type: 'urn:z:problem:not-found', title: 'Not found', status: 404, detail: 'Not Found' }), { status: 404 })))
    openStream({ path: '/stream', fetchImpl: fetchImpl as unknown as typeof fetch, onMessage: () => undefined, onState: (s) => states.push(s) })
    await vi.waitFor(() => {
      expect(states.at(-1)).toBe('unavailable')
    })
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('reports a 401 to the auth gate and stops', async () => {
    const errors: number[] = []
    const off = onApiError((e) => errors.push(e.status))
    const states: StreamState[] = []
    const fetchImpl = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ type: 'urn:z:problem:unauthenticated', title: 'Sign-in required', status: 401 }), { status: 401 })))
    openStream({ path: '/missions/m/events', fetchImpl: fetchImpl as unknown as typeof fetch, onMessage: () => undefined, onState: (s) => states.push(s) })
    await vi.waitFor(() => {
      expect(states.at(-1)).toBe('closed')
    })
    expect(errors).toEqual([401])
    off()
  })
})
