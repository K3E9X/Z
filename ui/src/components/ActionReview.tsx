import { Link } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { actionLabel, useReviewActions } from '@/api/review-actions'
import type { Mission, ProposedAction } from '@/api/types'
import { StepUpCancelled } from '@/shell/stepup'
import { useCan, useCurrentMe } from '@/lib/auth'
import { relative } from '@/lib/format'
import { MOD, useHotkeys } from '@/lib/hotkeys'
import { actionStatus } from '@/lib/status'
import type { AgentIndex } from './Avatar'
import { Dialog } from './Dialog'
import { Icon } from './Icon'
import { JsonView, PreviewView, previewSummary } from './previews'
import { Button, Textarea } from './ui'

export interface BatchItem {
  key: string
  title: string
  detail?: string
  meta?: string
  tag?: string
  duplicateOf?: string | null
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/**
 * Items of a batch write (several tickets in one action), from a json preview `{items: [{key, ...}]}`
 * or a payload `items` list. The judge's `duplicate` checks pre-exclude items it matched.
 */
export function batchItems(action: ProposedAction): BatchItem[] {
  const source =
    action.preview.kind === 'json' && isObj(action.preview.data) && Array.isArray(action.preview.data.items)
      ? action.preview.data.items
      : Array.isArray(action.payload.items)
        ? action.payload.items
        : null
  if (!source) return []
  const dupChecks = (action.judge_verdict?.checks ?? []).filter((c) => /dup/i.test(c.name) && !c.ok)
  return source.filter(isObj).map((it, i) => {
    const key = typeof it.key === 'string' ? it.key : String(i)
    const t = [it.title, it.summary, it.name].find((x) => typeof x === 'string')
    const title = typeof t === 'string' ? t : key
    const dup = dupChecks.find((c) => (c.detail ?? '').includes(key) || (c.detail ?? '').includes(title))
    return {
      key,
      title,
      detail: typeof it.description === 'string' ? it.description : typeof it.body === 'string' ? it.body : undefined,
      meta: [it.team, it.owner, it.ref].filter((x) => typeof x === 'string').join(' · ') || undefined,
      tag: typeof it.priority === 'string' ? it.priority : undefined,
      duplicateOf: typeof it.duplicate_of === 'string' ? it.duplicate_of : dup ? (dup.detail ?? 'a duplicate') : null,
    }
  })
}

function verdict(a: ProposedAction): { text: string; tone: 'ok' | 'wait' | 'err' } {
  const v = a.judge_verdict
  if (!v) return { text: 'Not checked', tone: 'wait' }
  if (v.verdict === 'ok') return { text: 'No injection detected', tone: 'ok' }
  const failed = v.checks.filter((c) => !c.ok)
  const first = failed[0]
  return {
    text: first ? `${first.name}${first.detail ? `: ${first.detail}` : ''}` : v.verdict === 'block' ? 'Blocked' : 'Warning',
    tone: v.verdict === 'block' ? 'err' : 'wait',
  }
}

function RejectDialog({ open, onClose, onReject }: { open: boolean; onClose: () => void; onReject: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Reject this write"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            busy={busy}
            disabled={!reason.trim()}
            onClick={() => {
              setBusy(true)
              onReject(reason.trim())
                .then(onClose)
                .catch(() => undefined)
                .finally(() => {
                  setBusy(false)
                })
            }}
          >
            Reject
          </Button>
        </>
      }
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-meta text-text-2">Why? The agent gets the reason, and it becomes a learning signal.</span>
        <Textarea
          rows={3}
          autoFocus
          data-autofocus
          value={reason}
          onChange={(e) => {
            setReason(e.target.value)
          }}
          placeholder="e.g. duplicate of SECOPS-1790, wrong owning team"
        />
      </label>
    </Dialog>
  )
}

function EditDialog({
  open,
  onClose,
  action,
  onSave,
}: {
  open: boolean
  onClose: () => void
  action: ProposedAction
  onSave: (payload: Record<string, unknown>, comment: string) => Promise<void>
}) {
  const [text, setText] = useState(() => JSON.stringify(action.payload, null, 2))
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const parsed = useMemo(() => {
    try {
      const v = JSON.parse(text) as unknown
      return isObj(v) ? { ok: true as const, value: v } : { ok: false as const, error: 'The payload must be a JSON object.' }
    } catch (e) {
      return { ok: false as const, error: e instanceof Error ? e.message : 'Invalid JSON' }
    }
  }, [text])
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Edit the payload"
      eyebrow={`${actionLabel(action)} · validated against the operation's input schema`}
      width={760}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!parsed.ok}
            onClick={() => {
              if (!parsed.ok) return
              setBusy(true)
              onSave(parsed.value, comment)
                .then(onClose)
                .catch(() => undefined)
                .finally(() => {
                  setBusy(false)
                })
            }}
          >
            Save as a new version
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="m-0 text-meta text-text-2">
          Editing supersedes this proposal with a new one that still needs approval. The difference is recorded as a learning signal.
        </p>
        <Textarea
          aria-label="Payload (JSON)"
          rows={16}
          spellCheck={false}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
          }}
          className="font-mono text-[12px]"
        />
        {!parsed.ok && (
          <div role="alert" className="text-meta text-err">
            {parsed.error}
          </div>
        )}
        <Textarea
          aria-label="Note for the agent"
          rows={2}
          value={comment}
          onChange={(e) => {
            setComment(e.target.value)
          }}
          placeholder="Note for the agent (optional)"
        />
      </div>
    </Dialog>
  )
}

/** The review of one proposed write (docs/09 screen 4): who it runs as, what it calls, the preview, the decision. */
export function ActionReview({
  action,
  mission,
  agents,
  hotkeys = true,
  onDecided,
}: {
  action: ProposedAction
  mission?: Mission | null
  agents: AgentIndex
  hotkeys?: boolean
  onDecided?: () => void
}) {
  const me = useCurrentMe()
  const actions = useReviewActions()
  const canApprove = useCan('actions:approve')
  const [note, setNote] = useState('')
  const [rejecting, setRejecting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const items = useMemo(() => batchItems(action), [action])
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set(items.filter((i) => i.duplicateOf).map((i) => i.key)))
  const pending = action.status === 'pending'
  const alreadyApproved = action.approvals.some((a) => a.by === me.user.username || a.by === me.user.id)
  const fourEyesBlocked = !!action.four_eyes && !!mission && (mission.created_by === me.user.id || mission.created_by === me.user.username)
  const disabledReason = !canApprove
    ? 'You need the actions:approve permission.'
    : alreadyApproved
      ? 'You already approved this. A different approver must give the second approval.'
      : fourEyesBlocked
        ? 'Four-eyes: you started this mission, someone else must approve its writes.'
        : !pending
          ? `This write is ${actionStatus(action.status).label.toLowerCase()}.`
          : null
  const v = verdict(action)
  const count = items.length ? items.length - excluded.size : 1
  const opShort = action.operation.startsWith(`${action.connector}.`) ? action.operation.slice(action.connector.length + 1) : action.operation

  const approve = () => {
    if (disabledReason || busy) return
    setBusy(true)
    actions
      .approve(action, { comment: note || undefined, exclude: [...excluded] })
      .then(() => onDecided?.())
      .catch((e: unknown) => {
        if (e instanceof StepUpCancelled) return
      })
      .finally(() => {
        setBusy(false)
      })
  }

  useHotkeys(
    {
      'mod+enter': approve,
      x: () => {
        if (pending && canApprove) setRejecting(true)
      },
      e: () => {
        if (pending && canApprove) setEditing(true)
      },
    },
    hotkeys && !rejecting && !editing,
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-1.5 border-b border-line px-8 pt-6 pb-5">
          <div className="text-meta text-text-3">
            Mission “{mission?.title ?? action.mission_id}” ·{' '}
            <Link to="/missions/$id" params={{ id: action.mission_id }} search={{ tab: 'reasoning' }} className="text-text-2 underline underline-offset-2">
              see the reasoning
            </Link>
          </div>
          <h1 className="m-0 text-2xl font-semibold">{action.summary}</h1>
          <div className="text-meta text-text-3">
            Proposed by {agents.label(action.agent)} · {relative(action.created_at)}
            {action.edited_from && ' · edited version'}
          </div>
        </div>
        <div className="flex max-w-[920px] flex-col gap-6 px-8 py-6">
          <dl className="m-0 grid grid-cols-2 gap-px overflow-hidden rounded-panel border border-line bg-line sm:grid-cols-4">
            {[
              ['Runs as', <span key="r" className="font-mono text-[12.5px]">{action.runs_as.identity}</span>, action.runs_as.scope === 'user' ? 'your own account' : 'service account'],
              ['Calls', <span key="c" className="font-mono text-[12.5px]">{opShort}{count > 1 ? ` × ${count}` : ''}</span>, action.connector],
              ['Reversible', action.reversible ? 'Yes' : 'No, irreversible', action.class === 'destructive' ? 'destructive' : 'write'],
              ['Judge', <span key="j" className={v.tone === 'ok' ? 'text-ok' : v.tone === 'err' ? 'text-err' : 'text-wait'}>{v.text}</span>, action.judge_verdict ? `${action.judge_verdict.checks.length} checks` : ''],
            ].map(([k, val, sub], i) => (
              <div key={i} className="flex flex-col gap-1 bg-panel px-4 py-3.5">
                <dt className="text-meta text-text-3">{k}</dt>
                <dd className="m-0 flex flex-col">
                  <span>{val}</span>
                  {sub && <span className="text-sm text-text-3">{sub}</span>}
                </dd>
              </div>
            ))}
          </dl>

          {action.class === 'destructive' && (
            <div role="note" className="flex items-start gap-2.5 rounded-panel border border-err px-4 py-3 text-ui">
              <Icon name="ban" size={15} className="mt-[2px] text-err" />
              <span>
                <span className="font-medium text-err">Destructive</span> · needs {action.required_approvals} different approvers ({action.approvals.length} of{' '}
                {action.required_approvals} so far) and a fresh sign-in. It can only be approved in the Z app.
              </span>
            </div>
          )}

          {action.judge_verdict && action.judge_verdict.checks.some((c) => !c.ok) && (
            <ul className="m-0 flex list-none flex-col gap-1 p-0 text-meta">
              {action.judge_verdict.checks
                .filter((c) => !c.ok)
                .map((c) => (
                  <li key={c.name} className="flex gap-2 text-wait">
                    <Icon name="ban" size={12} className="mt-[3px]" />
                    <span>
                      <span className="font-medium">{c.name}</span>
                      {c.detail ? ` · ${c.detail}` : ''}
                    </span>
                  </li>
                ))}
            </ul>
          )}

          {items.length > 0 ? (
            <ul className="m-0 flex list-none flex-col border-t border-line p-0" aria-label="Items in this write">
              {items.map((it) => {
                const off = excluded.has(it.key)
                return (
                  <li key={it.key} className="flex gap-4 border-b border-line py-4">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 flex-none"
                      checked={!off}
                      disabled={!pending}
                      aria-label={`Include ${it.title}`}
                      onChange={(e) => {
                        setExcluded((cur) => {
                          const n = new Set(cur)
                          if (e.target.checked) n.delete(it.key)
                          else n.add(it.key)
                          return n
                        })
                      }}
                    />
                    <div className="flex min-w-0 flex-col gap-1">
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        <span className={`font-medium ${off ? 'text-text-3 line-through' : ''}`}>{it.title}</span>
                        {it.tag && !off && <span className={`text-meta ${priorityText(it.tag)}`}>{it.tag} priority</span>}
                        {it.meta && !off && <span className="text-meta text-text-3">{it.meta}</span>}
                        {it.duplicateOf && <span className="text-meta text-text-3">Duplicate of {it.duplicateOf}{off ? ', excluded by Z' : ''}</span>}
                      </div>
                      {it.detail && !off && <p className="m-0 max-w-[620px] text-text-2">{it.detail}</p>}
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <PreviewView preview={action.preview} />
          )}

          {action.approvals.length > 0 && (
            <div className="text-meta text-text-2">
              Approved by {action.approvals.map((a) => `${a.by}${a.step_up ? ' (re-authenticated)' : ''}`).join(', ')}
            </div>
          )}

          <details className="text-meta text-text-3">
            <summary className="cursor-pointer">Exact payload sent to {action.connector}</summary>
            <JsonView data={action.payload} className="mt-2" />
            <div className="mt-1 font-mono">idempotency key {action.idempotency_key}</div>
          </details>

          {pending && canApprove && (
            <label className="flex flex-col gap-1.5">
              <span className="text-meta text-text-2">Note for the agent</span>
              <Textarea
                rows={2}
                value={note}
                onChange={(e) => {
                  setNote(e.target.value)
                }}
                placeholder="e.g. merge the first two into a single ticket"
              />
            </label>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t border-line px-8 py-4">
        <span className="min-w-0 flex-1 text-meta text-text-2">
          {disabledReason ?? (
            <>
              {items.length ? `${count} ${count === 1 ? 'item' : 'items'} will be written.` : previewSummary(action.preview)} Your decision also teaches the agent.
            </>
          )}
        </span>
        {pending && canApprove && (
          <>
            <Button variant="danger" onClick={() => setRejecting(true)} title="Reject (X)">
              Reject
            </Button>
            <Button onClick={() => setEditing(true)} title="Edit (E)">
              Edit
            </Button>
          </>
        )}
        <Button variant="primary" kbd={`${MOD}↵`} busy={busy} disabled={!!disabledReason || count === 0} onClick={approve}>
          {action.class === 'destructive' ? 'Approve (step-up)' : 'Approve'}
        </Button>
      </div>
      <RejectDialog
        open={rejecting}
        onClose={() => setRejecting(false)}
        onReject={async (reason) => {
          await actions.reject(action, reason)
          onDecided?.()
        }}
      />
      {editing && (
        <EditDialog
          open={editing}
          onClose={() => setEditing(false)}
          action={action}
          onSave={async (payload, comment) => {
            await actions.edit(action, payload, comment || note || undefined)
            onDecided?.()
          }}
        />
      )}
    </div>
  )
}

function priorityText(p: string): string {
  const s = p.toLowerCase()
  if (/(highest|critical|blocker|p0|p1|high)/.test(s)) return 'text-err'
  if (/(medium|p2|major)/.test(s)) return 'text-wait'
  return 'text-text-2'
}
