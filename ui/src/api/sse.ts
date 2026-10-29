/**
 * Server-sent events over fetch (not EventSource) so the client controls `Last-Event-ID`, sees HTTP
 * statuses (401, a route not served yet) and detects silent stalls. Reconnects with backoff and resumes
 * from the last event id (docs/09 §6).
 */
import { API_BASE, emitApiError } from './client'
import { ApiError, isNotServed, toProblem } from './problem'

export type StreamState = 'connecting' | 'open' | 'reconnecting' | 'unavailable' | 'closed'

export interface SseMessage {
  id: string | null
  event: string
  data: string
}

/** Incremental parser for the text/event-stream format (WHATWG HTML §9.2). */
export class SseParser {
  private buffer = ''
  private data: string[] = []
  private event = ''
  private id: string | null = null
  /** Last id the stream set (persists across messages, as in the spec). */
  lastId: string | null = null
  retry: number | null = null

  push(chunk: string): SseMessage[] {
    this.buffer += chunk
    const out: SseMessage[] = []
    let idx: number
    while ((idx = this.buffer.search(/\r\n|\r|\n/)) >= 0) {
      const line = this.buffer.slice(0, idx)
      const nl = this.buffer[idx] === '\r' && this.buffer[idx + 1] === '\n' ? 2 : 1
      // A lone trailing \r may be the first half of \r\n: wait for more input.
      if (this.buffer[idx] === '\r' && idx + 1 === this.buffer.length) break
      this.buffer = this.buffer.slice(idx + nl)
      const msg = this.line(line)
      if (msg) out.push(msg)
    }
    return out
  }

  private line(line: string): SseMessage | null {
    if (line === '') {
      if (this.data.length === 0) {
        this.event = ''
        return null
      }
      const msg: SseMessage = {
        id: this.id ?? this.lastId,
        event: this.event || 'message',
        data: this.data.join('\n'),
      }
      this.data = []
      this.event = ''
      this.id = null
      return msg
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    switch (field) {
      case 'data':
        this.data.push(value)
        break
      case 'event':
        this.event = value
        break
      case 'id':
        if (!value.includes('\0')) {
          this.id = value
          this.lastId = value
        }
        break
      case 'retry':
        if (/^\d+$/.test(value)) this.retry = Number(value)
        break
      default:
        break
    }
    return null
  }
}

export interface StreamOptions {
  /** Path under /api/v1, with its query string. */
  path: string
  onMessage: (msg: SseMessage) => void
  onState?: (state: StreamState, attempt: number) => void
  /** Resume from this id on the first connection (e.g. the last seq already loaded). */
  lastEventId?: string | null
  fetchImpl?: typeof fetch
  /** No byte for this long means a dead connection (the server sends keepalives). */
  stallMs?: number
  maxDelayMs?: number
}

export interface StreamHandle {
  close: () => void
  readonly lastEventId: string | null
}

export function openStream(opts: StreamOptions): StreamHandle {
  const fetchImpl =
    opts.fetchImpl ?? ((input: RequestInfo | URL, init?: RequestInit) => globalThis.fetch(input, init))
  const stallMs = opts.stallMs ?? 45_000
  const maxDelay = opts.maxDelayMs ?? 30_000
  let lastId: string | null = opts.lastEventId ?? null
  let closed = false
  let attempt = 0
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let stall: ReturnType<typeof setTimeout> | null = null
  let serverRetry: number | null = null

  const setState = (s: StreamState) => opts.onState?.(s, attempt)

  const clearStall = () => {
    if (stall) clearTimeout(stall)
    stall = null
  }
  const armStall = () => {
    clearStall()
    stall = setTimeout(() => controller?.abort(), stallMs)
  }

  const schedule = () => {
    if (closed) return
    attempt += 1
    setState('reconnecting')
    const base = serverRetry ?? 1000
    const delay = Math.min(maxDelay, base * 2 ** Math.min(attempt - 1, 5))
    timer = setTimeout(() => void connect(), delay)
  }

  const connect = async () => {
    if (closed) return
    controller = new AbortController()
    setState(attempt === 0 ? 'connecting' : 'reconnecting')
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (lastId) headers['Last-Event-ID'] = lastId
    let res: Response
    try {
      res = await fetchImpl(API_BASE + opts.path, {
        headers,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
      })
    } catch {
      schedule()
      return
    }
    if (!res.ok) {
      let body: unknown
      try {
        body = (await res.json()) as unknown
      } catch {
        body = null
      }
      const err = new ApiError(res.status, toProblem(res.status, body, res.statusText))
      if (isNotServed(err)) {
        closed = true
        setState('unavailable')
        return
      }
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        emitApiError(err)
        closed = true
        setState('closed')
        return
      }
      schedule()
      return
    }
    if (!res.body) {
      schedule()
      return
    }
    attempt = 0
    setState('open')
    const parser = new SseParser()
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    armStall()
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        armStall()
        for (const msg of parser.push(decoder.decode(value, { stream: true }))) {
          if (msg.id !== null) lastId = msg.id
          opts.onMessage(msg)
        }
        if (parser.retry !== null) serverRetry = parser.retry
      }
    } catch {
      // aborted (stall or close) or network drop
    } finally {
      clearStall()
    }
    if (!closed) schedule()
  }

  void connect()

  return {
    close() {
      closed = true
      if (timer) clearTimeout(timer)
      clearStall()
      controller?.abort()
      setState('closed')
    },
    get lastEventId() {
      return lastId
    },
  }
}
