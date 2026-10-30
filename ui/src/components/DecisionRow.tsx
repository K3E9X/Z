import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { canQuickApprove, useReviewActions } from '@/api/review-actions'
import type { ReviewItem } from '@/api/types'
import { useCan } from '@/lib/auth'
import { shortAge } from '@/lib/format'
import type { AgentIndex } from './Avatar'
import { Icon } from './Icon'
import { Button } from './ui'

export function reviewItemId(i: ReviewItem): string {
  return i.action?.id ?? i.question?.id ?? i.suggestion?.id ?? ''
}

export function reviewItemTitle(i: ReviewItem): string {
  if (i.action) return i.action.summary
  if (i.question) return `“${i.question.text}”`
  if (i.suggestion) return i.suggestion.title
  return ''
}

function ago(s: number): string {
  const a = shortAge(s)
  return a === 'now' ? 'just now' : `${a} ago`
}

/** One decision waiting for the user, with inline Approve / Answer (docs/09 screen 1). */
export function DecisionRow({ item, agents }: { item: ReviewItem; agents: AgentIndex }) {
  const actions = useReviewActions()
  const canApprove = useCan('actions:approve')
  const canAnswer = useCan('questions:answer')
  const [busy, setBusy] = useState<string | null>(null)
  const id = reviewItemId(item)
  const agent = item.action?.agent ?? item.question?.agent ?? item.suggestion?.agent
  const middle =
    item.kind === 'action'
      ? `${item.action?.class === 'destructive' ? 'destructive · ' : ''}${item.mission_title}`
      : item.kind === 'question'
        ? 'question'
        : 'suggested mission'
  const meta = [agents.label(agent), middle, ago(item.age_s)]
  const question = item.question

  return (
    <div className="flex flex-wrap items-center gap-3.5 border-b border-line py-3.5">
      <span
        className={`flex h-[30px] w-[30px] flex-none items-center justify-center rounded-btn ${
          item.action?.class === 'destructive' ? 'bg-err-bg text-err' : 'bg-wait-bg text-wait'
        }`}
        aria-hidden="true"
      >
        <Icon
          name={item.kind === 'question' ? 'question' : item.kind === 'suggestion' ? 'sparkle' : 'doc'}
          size={15}
          strokeWidth={1.8}
        />
      </span>
      <div className="min-w-0 flex-[1_1_320px]">
        <div className="font-medium">{reviewItemTitle(item)}</div>
        <div className="text-meta text-text-3">{meta.join(' · ')}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        {item.action && (
          <>
            <Link
              to="/review"
              search={{ item: id }}
              className="flex h-[30px] items-center rounded-ctl border border-line-2 px-3 no-underline hover:bg-raised"
            >
              Review
            </Link>
            {canApprove && canQuickApprove(item.action) && (
              <Button
                variant="primary"
                size="sm"
                busy={busy === 'approve'}
                onClick={() => {
                  const a = item.action
                  if (!a) return
                  setBusy('approve')
                  actions
                    .approve(a)
                    .catch(() => undefined)
                    .finally(() => {
                      setBusy(null)
                    })
                }}
              >
                Approve
              </Button>
            )}
          </>
        )}
        {question &&
          (canAnswer && question.options.length > 0 && question.options.length <= 3 ? (
            question.options.map((opt, i) => (
              <Button
                key={opt}
                size="sm"
                variant={i === 0 ? 'primary' : 'secondary'}
                busy={busy === opt}
                onClick={() => {
                  setBusy(opt)
                  actions
                    .answer(question.id, opt)
                    .catch(() => undefined)
                    .finally(() => {
                      setBusy(null)
                    })
                }}
              >
                {opt}
              </Button>
            ))
          ) : (
            <Link
              to="/review"
              search={{ item: id }}
              className="flex h-[30px] items-center rounded-ctl border border-line-2 px-3 no-underline hover:bg-raised"
            >
              Answer
            </Link>
          ))}
        {item.suggestion && (
          <Link
            to="/review"
            search={{ item: id }}
            className="flex h-[30px] items-center rounded-ctl border border-line-2 px-3 no-underline hover:bg-raised"
          >
            Review
          </Link>
        )}
      </div>
    </div>
  )
}
