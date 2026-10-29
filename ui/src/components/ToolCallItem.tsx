import { useState } from 'react'
import type { ZEventOf } from '@/api/events.gen'
import { ms } from '@/lib/format'
import { Icon } from './Icon'

function argSummary(args: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(args)) {
    if (parts.join(' ').length > 90) break
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') parts.push(String(v))
    else if (Array.isArray(v)) parts.push(`${k}[${v.length}]`)
  }
  return parts.join(' ')
}

/** A tool call and its result: `operation args` then the result summary, judge verdict, duration. */
export function ToolCallItem({
  call,
  result,
}: {
  call: ZEventOf<'tool_call'>['payload']
  result: ZEventOf<'tool_result'>['payload'] | null
}) {
  const [open, setOpen] = useState(false)
  const name = call.operation ?? call.tool
  const hasArgs = Object.keys(call.args).length > 0
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        disabled={!hasArgs}
        className="flex min-w-0 items-baseline gap-2 text-left disabled:cursor-default"
      >
        <span className="font-mono text-[12.5px] text-text-2">{name}</span>
        <span className="min-w-0 truncate text-text-2">{argSummary(call.args)}</span>
        {hasArgs && <Icon name={open ? 'chevronDown' : 'chevronRight'} size={11} className="flex-none self-center text-text-3" />}
      </button>
      {result ? (
        <div className="flex flex-wrap items-baseline gap-x-2">
          {!result.ok && <span className="font-medium text-err">Failed:</span>}
          <span className={result.ok ? '' : 'text-text-2'}>{result.summary}</span>
          {result.judge !== 'clean' && (
            <span className={`inline-flex items-center gap-1 rounded-[5px] px-1.5 text-sm ${result.judge === 'blocked' ? 'bg-err-bg text-err' : 'bg-wait-bg text-wait'}`}>
              <Icon name="ban" size={11} />
              {result.judge === 'blocked' ? 'Blocked by the judge' : 'Judge: possible injection, content isolated'}
            </span>
          )}
          <span className="font-mono text-xs text-text-3">{ms(result.duration_ms)}</span>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-meta text-text-3">
          <span className="h-1.5 w-1.5 animate-zpulse rounded-full bg-run" aria-hidden="true" />
          running…
        </div>
      )}
      {open && (
        <pre className="scrollbar-thin m-0 max-h-[240px] overflow-auto rounded-btn border border-line bg-code px-3 py-2 font-mono text-xs leading-[1.6] text-text-2">
          {JSON.stringify(call.args, null, 2)}
        </pre>
      )}
    </div>
  )
}
