/**
 * What the reviewer sees for each preview kind of contracts/action.schema.json. Everything in a preview
 * comes from tools or agents: rendered as text or safe markdown, never as HTML.
 */
import { useArtifactText } from '@/api/artifacts'
import type {
  ChatMessagePreview,
  CommentPreview,
  DiffPreview,
  EventPreview,
  FilePreview,
  IssuePreview,
  IssueUpdatePreview,
  JsonPreview,
  MailPreview,
  Preview,
} from '@/api/types'
import { bytes, dateTime } from '@/lib/format'
import { DiffViewer } from './DiffViewer'
import { Icon } from './Icon'
import { Markdown } from './Markdown'
import { Chip, DefList, Skeleton } from './ui'

function value(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

function priorityTone(p?: string): 'err' | 'wait' | undefined {
  if (!p) return undefined
  const s = p.toLowerCase()
  if (/(highest|critical|blocker|p0|p1|high)/.test(s)) return 'err'
  if (/(medium|p2|major)/.test(s)) return 'wait'
  return undefined
}

function Section({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`flex flex-col gap-3 rounded-panel border border-line bg-panel px-5 py-4 ${className}`}>{children}</div>
}

function DiffBody({ p }: { p: DiffPreview }) {
  const q = useArtifactText(!p.diff ? p.patch_artifact : null)
  const diff = p.diff ?? q.data
  return (
    <div className="flex flex-col gap-3">
      <Section>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[12.5px] text-text-2">
          <span>{p.project}</span>
          <span className="text-text-3">·</span>
          <span className="text-text">{p.source_branch}</span>
          <Icon name="arrowRight" size={12} />
          <span>{p.target_branch}</span>
        </div>
        <div className="text-[15px] font-medium">{p.title}</div>
        {p.description && <Markdown className="text-text-2">{p.description}</Markdown>}
        {p.files && p.files.length > 0 && (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 font-mono text-[12.5px]">
            {p.files.map((f) => (
              <li key={f.path} className="flex gap-3">
                <span className="min-w-0 flex-1 truncate">{f.path}</span>
                <span className="text-ok">+{f.added ?? 0}</span>
                <span className="text-err">−{f.removed ?? 0}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {diff ? (
        <DiffViewer diff={diff} maxHeight={520} />
      ) : q.isPending && p.patch_artifact ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="text-meta text-text-3">The patch is not available{q.error ? ` (${q.error.message})` : ''}.</div>
      )}
    </div>
  )
}

function IssueBody({ p }: { p: IssuePreview }) {
  const tone = priorityTone(p.priority)
  return (
    <Section>
      <div className="flex flex-wrap items-center gap-2 text-meta text-text-3">
        <span className="font-mono">
          {p.tracker} · {p.project}
        </span>
        {p.issue_type && <span>· {p.issue_type}</span>}
      </div>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-[15px] font-medium">{p.title}</span>
        {p.priority && <span className={`text-meta ${tone === 'err' ? 'text-err' : tone === 'wait' ? 'text-wait' : 'text-text-2'}`}>{p.priority} priority</span>}
      </div>
      {p.description && <Markdown className="text-text-2">{p.description}</Markdown>}
      <DefList
        className="text-ui"
        rows={[
          ['Assignee', p.assignee ?? 'Unassigned'],
          ['Due', p.due_date ?? '—'],
          ['Labels', p.labels?.length ? <span className="flex flex-wrap gap-1.5">{p.labels.map((l) => <Chip key={l}>{l}</Chip>)}</span> : '—'],
          ...Object.entries(p.fields ?? {}).map(([k, v]) => [k, <span key={k} className="font-mono text-[12.5px]">{value(v)}</span>] as [string, React.ReactNode]),
        ]}
      />
    </Section>
  )
}

function IssueUpdateBody({ p }: { p: IssueUpdatePreview }) {
  return (
    <Section>
      <div className="font-mono text-meta text-text-3">
        {p.tracker} · {p.issue}
      </div>
      <table className="w-full border-collapse text-ui">
        <thead>
          <tr className="text-sm text-text-3">
            <th className="py-1.5 pr-4 font-normal">Field</th>
            <th className="py-1.5 pr-4 font-normal">Now</th>
            <th className="py-1.5 font-normal">After</th>
          </tr>
        </thead>
        <tbody>
          {p.changes.map((c) => (
            <tr key={c.field} className="border-t border-line align-top">
              <td className="py-2 pr-4 font-mono text-[12.5px]">{c.field}</td>
              <td className="py-2 pr-4 text-err line-through decoration-1">{value(c.from)}</td>
              <td className="py-2 text-ok">{value(c.to)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  )
}

function CommentBody({ p }: { p: CommentPreview }) {
  return (
    <Section>
      <div className="font-mono text-meta text-text-3">
        {p.tracker ? `${p.tracker} · ` : ''}comment on {p.target}
      </div>
      <Markdown>{p.body}</Markdown>
    </Section>
  )
}

function MailBody({ p }: { p: MailPreview }) {
  return (
    <Section>
      <DefList
        className="text-ui"
        rows={[
          ['From', p.from ?? 'you'],
          ['To', p.to.join(', ')],
          ...(p.cc?.length ? [['Cc', p.cc.join(', ')] as [string, string]] : []),
          ['Subject', <span key="s" className="font-medium">{p.subject}</span>],
          ...(p.in_reply_to ? [['In reply to', p.in_reply_to] as [string, string]] : []),
        ]}
      />
      <div className="border-t border-line pt-3 whitespace-pre-wrap">{p.body}</div>
      {p.attachments && p.attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {p.attachments.map((a) => (
            <Chip key={a}>
              <Icon name="paperclip" size={12} />
              {a}
            </Chip>
          ))}
        </div>
      )}
    </Section>
  )
}

function EventBody({ p }: { p: EventPreview }) {
  return (
    <Section>
      <div className="text-[15px] font-medium">{p.title}</div>
      <DefList
        className="text-ui"
        rows={[
          ['When', `${dateTime(p.start)} → ${dateTime(p.end)}`],
          ['Where', p.location ?? '—'],
          ['Attendees', p.attendees?.join(', ') ?? '—'],
        ]}
      />
      {p.body && <div className="border-t border-line pt-3 whitespace-pre-wrap text-text-2">{p.body}</div>}
    </Section>
  )
}

function ChatBody({ p }: { p: ChatMessagePreview }) {
  return (
    <Section>
      <div className="flex items-center gap-2 text-meta text-text-3">
        <Icon name="chat" size={13} />
        {p.platform} · {p.target}
      </div>
      <Markdown>{p.text}</Markdown>
    </Section>
  )
}

function FileBody({ p }: { p: FilePreview }) {
  return (
    <Section>
      <div className="flex items-center gap-3">
        <Icon name="file" size={18} className="text-text-3" />
        <div className="flex flex-col">
          <span className="font-medium">{p.name}</span>
          <span className="text-meta text-text-3">{p.size !== undefined ? bytes(p.size) : 'size unknown'}</span>
        </div>
      </div>
      <DefList className="text-ui" rows={[['Destination', <span key="d" className="font-mono text-[12.5px]">{p.destination}</span>]]} />
    </Section>
  )
}

export function JsonView({ data, className = '' }: { data: unknown; className?: string }) {
  return (
    <pre className={`scrollbar-thin m-0 max-h-[520px] overflow-auto rounded-panel border border-line bg-code px-4 py-3 font-mono text-xs leading-[1.65] text-text-2 ${className}`}>
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

function JsonBody({ p }: { p: JsonPreview }) {
  return <JsonView data={p.data} />
}

export function PreviewView({ preview }: { preview: Preview }) {
  switch (preview.kind) {
    case 'diff':
      return <DiffBody p={preview} />
    case 'issue':
      return <IssueBody p={preview} />
    case 'issue_update':
      return <IssueUpdateBody p={preview} />
    case 'comment':
      return <CommentBody p={preview} />
    case 'mail':
      return <MailBody p={preview} />
    case 'event':
      return <EventBody p={preview} />
    case 'chat_message':
      return <ChatBody p={preview} />
    case 'file':
      return <FileBody p={preview} />
    case 'json':
      return <JsonBody p={preview} />
  }
}

/** One-line description of what will run, used in confirmations and the footer. */
export function previewSummary(preview: Preview, count = 1): string {
  switch (preview.kind) {
    case 'diff':
      return 'A merge request will be opened.'
    case 'issue':
      return count > 1 ? `${count} issues will be created.` : 'One issue will be created.'
    case 'issue_update':
      return `${preview.issue} will be updated (${preview.changes.length} ${preview.changes.length === 1 ? 'field' : 'fields'}).`
    case 'comment':
      return `A comment will be posted on ${preview.target}.`
    case 'mail':
      return `A mail will be sent to ${preview.to.length} ${preview.to.length === 1 ? 'recipient' : 'recipients'}.`
    case 'event':
      return 'A calendar event will be created.'
    case 'chat_message':
      return `A message will be posted in ${preview.target}.`
    case 'file':
      return `${preview.name} will be uploaded.`
    case 'json':
      return count > 1 ? `${count} items will be written.` : 'The payload below will be sent.'
  }
}
