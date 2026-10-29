import { Link } from '@tanstack/react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { artifactUrl, downloadArtifact, useArtifactText } from '@/api/artifacts'
import type { TimelineItem, TimelineState } from '@/lib/timeline'
import { clock } from '@/lib/format'
import { actionStatus } from '@/lib/status'
import type { AgentIndex } from './Avatar'
import { DiffViewer } from './DiffViewer'
import { Icon } from './Icon'
import { Markdown } from './Markdown'
import { QuestionCard } from './QuestionCard'
import { ToolCallItem } from './ToolCallItem'
import { Spinner } from './ui'

function LazyDiff({ id }: { id: string }) {
  const q = useArtifactText(id)
  if (q.isPending) return <Spinner label="Loading diff" />
  if (q.error) return <div className="text-meta text-text-3">The diff is not available ({q.error.message}).</div>
  return <DiffViewer diff={q.data} showFileHeader={false} maxHeight={360} />
}

function FileEdit({ item }: { item: Extract<TimelineItem, { kind: 'file_edit' }> }) {
  const [open, setOpen] = useState(item.edit.added + item.edit.removed <= 12)
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v)
        }}
        className="flex items-baseline gap-2 text-left"
      >
        <span className="font-mono text-[12.5px] text-text-2">edit</span>
        <span className="truncate font-mono text-[12.5px]">{item.edit.path}</span>
        <span className="font-mono text-xs text-ok">+{item.edit.added}</span>
        <span className="font-mono text-xs text-err">−{item.edit.removed}</span>
      </button>
      {open && item.edit.diff_ref && <LazyDiff id={item.edit.diff_ref} />}
    </div>
  )
}

function Terminal({ item }: { item: Extract<TimelineItem, { kind: 'terminal' }> }) {
  const t = item.term
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-baseline gap-2 font-mono text-[12.5px]">
        <span className="text-text-3">$</span>
        <span className="min-w-0 truncate">{t.command}</span>
        {t.exit_code !== null && t.exit_code !== undefined && (
          <span className={`text-xs ${t.exit_code === 0 ? 'text-text-3' : 'text-err'}`}>exit {t.exit_code}</span>
        )}
      </div>
      {t.output_tail && (
        <pre className="scrollbar-thin m-0 max-h-[200px] overflow-auto rounded-btn border border-line bg-code px-3 py-2 font-mono text-xs leading-[1.6] text-text-2">
          {t.output_tail}
        </pre>
      )}
    </div>
  )
}

export function TimelineRow({
  item,
  timeline,
  agents,
  showAgent,
}: {
  item: TimelineItem
  timeline: TimelineState
  agents: AgentIndex
  showAgent: boolean
}) {
  let body: ReactNode = null
  switch (item.kind) {
    case 'tool':
      body = <ToolCallItem call={item.call} result={item.result} />
      break
    case 'file_edit':
      body = <FileEdit item={item} />
      break
    case 'terminal':
      body = <Terminal item={item} />
      break
    case 'denied':
      body = (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 font-medium text-err">
            <Icon name="ban" size={14} />
            Blocked by a guardrail
          </div>
          <div className="text-text-2">
            <span className="font-mono text-[12.5px] text-text">{item.denied.what}</span>
            {item.denied.detail ? ` · ${item.denied.detail}` : ''}
            <span className="ml-2 font-mono text-xs text-text-3">{item.denied.rule}</span>
          </div>
        </div>
      )
      break
    case 'question': {
      const q = timeline.questions.get(item.questionId)
      body = q ? <QuestionCard id={q.id} text={q.text} options={q.options} answer={q.answer} answeredBy={q.answeredBy} /> : null
      break
    }
    case 'action': {
      const a = timeline.actions.get(item.actionId)
      if (!a) break
      const st = actionStatus(a.status)
      body = (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-mono text-[12.5px] text-text-2">propose_action</span>
          <span className="min-w-0 flex-1">{a.summary}</span>
          <span className={`text-meta ${a.cls === 'destructive' ? 'text-err' : 'text-text-3'}`}>
            {a.connector} · {a.cls}
          </span>
          {a.status === 'pending' ? (
            <Link
              to="/review"
              search={{ item: a.id }}
              className="flex h-[30px] items-center rounded-ctl border border-line-2 px-3 text-text no-underline hover:bg-raised"
            >
              Review
            </Link>
          ) : (
            <span className={`text-meta ${st.tone === 'ok' ? 'text-ok' : st.tone === 'err' ? 'text-err' : 'text-text-2'}`}>{st.label}</span>
          )}
        </div>
      )
      break
    }
    case 'message':
      body =
        item.message.role === 'user' ? (
          <div className="flex flex-col gap-0.5">
            <span className="text-meta text-text-3">{item.message.by ? `${item.message.by} guided the agent` : 'You guided the agent'}</span>
            <span className="whitespace-pre-wrap">“{item.message.text}”</span>
          </div>
        ) : item.message.role === 'system' ? (
          <span className="text-text-3">{item.message.text}</span>
        ) : (
          <Markdown>{item.message.text}</Markdown>
        )
      break
    case 'validation':
      body = (
        <div className="flex flex-col gap-1">
          <span className="font-medium text-wait">
            {item.error.where === 'output' ? 'Output did not match its schema' : 'Malformed tool arguments'}
            {item.error.attempt ? ` · repair attempt ${item.error.attempt}` : ''}
          </span>
          <ul className="m-0 list-none p-0 font-mono text-xs text-text-2">
            {item.error.errors.slice(0, 5).map((e, i) => (
              <li key={i}>
                {e.path || '/'}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )
      break
    case 'status':
      body = (
        <span className="text-text-2">
          {item.status.entity === 'mission' ? 'Mission' : item.status.entity === 'job' ? 'Job' : 'Step'}{' '}
          <span className="text-text">{item.status.to.replace(/_/g, ' ')}</span>
          {item.status.reason ? <span className="text-text-3"> · {item.status.reason}</span> : null}
        </span>
      )
      break
    case 'subagent':
      body = (
        <span>
          <span className="font-mono text-[12.5px] text-text-2">spawn_subagent</span> {agents.label(item.sub.agent)}
          <span className="text-text-3"> · {item.sub.input_summary}</span>
        </span>
      )
      break
    case 'artifact':
      body = (
        <span className="flex flex-wrap items-center gap-2">
          <Icon name="file" size={14} className="text-text-3" />
          Produced <span className="font-mono text-[12.5px]">{item.artifact.name}</span>
          <span className="text-meta text-text-3">{item.artifact.kind}</span>
          <button
            type="button"
            className="text-meta text-text-2 underline underline-offset-2"
            onClick={() => void downloadArtifact(item.artifact.artifact_id, item.artifact.name)}
          >
            Download
          </button>
        </span>
      )
      break
    case 'data_class':
      body = (
        <span className="text-text-2">
          Data class raised <span className="font-mono text-text">{item.change.from}</span> →{' '}
          <span className="font-mono text-text">{item.change.to}</span>
          <span className="text-text-3"> · {item.change.cause.replace(/_/g, ' ')}{item.change.to >= 'C2' ? ' · internal models only from now on' : ''}</span>
        </span>
      )
      break
    case 'judge':
      body = (
        <div className="flex flex-col gap-0.5">
          <span className={`flex items-center gap-2 font-medium ${item.judge.verdict === 'blocked' ? 'text-err' : 'text-wait'}`}>
            <Icon name="ban" size={14} />
            {item.judge.verdict === 'blocked' ? 'Judge blocked' : 'Judge flagged'} {item.judge.subject.replace(/_/g, ' ')}
          </span>
          <span className="text-text-2">
            {item.judge.reason ?? 'Suspected prompt injection; the content was isolated and labelled untrusted.'}
            {item.judge.categories?.length ? <span className="text-text-3"> · {item.judge.categories.join(', ')}</span> : null}
          </span>
        </div>
      )
      break
    case 'browser':
      body = (
        <div className="flex flex-col gap-2">
          <span>
            <span className="font-mono text-[12.5px] text-text-2">browser</span> {item.browser.action}{' '}
            <span className="font-mono text-xs text-text-3">{item.browser.url}</span>
          </span>
          {item.browser.screenshot_ref && (
            <img
              src={artifactUrl(item.browser.screenshot_ref)}
              alt={`Screenshot after ${item.browser.action}`}
              loading="lazy"
              className="max-h-[240px] max-w-full self-start rounded-btn border border-line"
            />
          )}
        </div>
      )
      break
  }
  if (!body) return null
  return (
    <div className="grid grid-cols-[52px_minmax(0,1fr)] gap-x-4 border-b border-line py-[var(--cell-y)]" data-seq={item.seq}>
      <span className="pt-px font-mono text-xs text-text-3">{clock(item.ts)}</span>
      <div className="flex min-w-0 flex-col gap-1">
        {showAgent && item.agent && <span className="text-sm text-text-3">{agents.label(item.agent)}</span>}
        {body}
      </div>
    </div>
  )
}

/**
 * The Activity timeline (tool calls, diffs, guardrail blocks, questions, proposed actions). Virtualized
 * past 150 items; sticks to the newest event while the reader is at the bottom.
 */
export function EventTimeline({
  timeline,
  items,
  agents,
  scrollRef,
  empty,
}: {
  timeline: TimelineState
  items: TimelineItem[]
  agents: AgentIndex
  scrollRef: RefObject<HTMLDivElement | null>
  empty?: ReactNode
}) {
  const multiAgent = new Set(items.map((i) => i.agent).filter(Boolean)).size > 1
  const virtual = items.length > 150
  const stick = useRef(true)
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual returns unstable functions by design
  const v = useVirtualizer({
    count: virtual ? items.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 64,
    overscan: 12,
    getItemKey: (i) => items[i]?.key ?? i,
  })

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
    }
  }, [scrollRef])

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !stick.current) return
    if (virtual) v.scrollToIndex(items.length - 1, { align: 'end' })
    else el.scrollTop = el.scrollHeight
  }, [items.length, virtual, v, scrollRef])

  if (items.length === 0) return <>{empty}</>
  if (!virtual) {
    return (
      <div role="log" aria-label="Activity" aria-live="polite">
        {items.map((it) => (
          <TimelineRow key={it.key} item={it} timeline={timeline} agents={agents} showAgent={multiAgent} />
        ))}
      </div>
    )
  }
  return (
    <div role="log" aria-label="Activity" aria-live="polite" style={{ height: v.getTotalSize(), position: 'relative' }}>
      {v.getVirtualItems().map((row) => {
        const it = items[row.index]
        if (!it) return null
        return (
          <div
            key={row.key}
            data-index={row.index}
            ref={v.measureElement}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${row.start}px)` }}
          >
            <TimelineRow item={it} timeline={timeline} agents={agents} showAgent={multiAgent} />
          </div>
        )
      })}
    </div>
  )
}
