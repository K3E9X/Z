/**
 * Review decisions with optimistic updates (docs/09 §6): the item leaves every cached review list at
 * once and comes back if the server refuses. Destructive approvals go through step-up.
 */
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useToast } from '@/components/toast'
import { StepUpCancelled, useStepUp } from '@/shell/stepup'
import { api, unwrap } from './client'
import { qk } from './queries'
import type { ProposedAction, ReviewItem, ReviewList } from './types'

type Snapshot = [readonly unknown[], ReviewList | undefined][]

function itemId(i: ReviewItem): string {
  return i.action?.id ?? i.question?.id ?? i.suggestion?.id ?? ''
}

function recount(list: ReviewList, removed: ReviewItem[]): ReviewList['counts'] {
  const c = { ...list.counts }
  for (const r of removed) {
    c.all = Math.max(0, c.all - 1)
    if (r.kind === 'action') c.writes = Math.max(0, c.writes - 1)
    if (r.kind === 'question') c.questions = Math.max(0, c.questions - 1)
    if (r.kind === 'suggestion') c.suggestions = Math.max(0, c.suggestions - 1)
  }
  return c
}

/** Remove items from every cached review list; returns a snapshot to roll back. */
export function removeFromReview(qc: QueryClient, ids: Set<string>): Snapshot {
  const snapshot = qc.getQueriesData<ReviewList>({ queryKey: qk.reviewAll })
  for (const [key, list] of snapshot) {
    if (!list) continue
    const removed = list.items.filter((i) => ids.has(itemId(i)))
    if (removed.length === 0) continue
    qc.setQueryData<ReviewList>(key, {
      ...list,
      items: list.items.filter((i) => !ids.has(itemId(i))),
      counts: recount(list, removed),
    })
  }
  return snapshot
}

export function restoreReview(qc: QueryClient, snapshot: Snapshot): void {
  for (const [key, list] of snapshot) qc.setQueryData(key, list)
}

export function actionLabel(a: Pick<ProposedAction, 'connector' | 'operation'>): string {
  return `${a.connector} · ${a.operation.replace(`${a.connector}.`, '')}`
}

export function useReviewActions() {
  const qc = useQueryClient()
  const toast = useToast()
  const stepUp = useStepUp()

  const settle = useCallback(async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: qk.reviewAll }),
      qc.invalidateQueries({ queryKey: ['missions'] }),
      qc.invalidateQueries({ queryKey: ['mission'] }),
    ])
  }, [qc])

  const approve = useCallback(
    async (action: ProposedAction, opts: { comment?: string; exclude?: string[] } = {}) => {
      const snap = removeFromReview(qc, new Set([action.id]))
      try {
        const body = {
          ...(opts.comment ? { comment: opts.comment } : {}),
          ...(opts.exclude && opts.exclude.length > 0 ? { exclude: opts.exclude } : {}),
        }
        const res = await stepUp.run(
          () => unwrap(api.POST('/actions/{id}/approve', { params: { path: { id: action.id } }, body })),
          {
            what: action.class === 'destructive' ? 'Approving a destructive action' : 'Approving this write',
            destructive: action.class === 'destructive',
            detail: `${actionLabel(action)}${action.required_approvals > 1 ? ` · ${action.approvals.length + 1} of ${action.required_approvals} approvals` : ''}`,
          },
        )
        toast.ok(
          res.status === 'pending'
            ? `Approval recorded. ${res.required_approvals - res.approvals.length} more needed.`
            : `Approved: ${action.summary}`,
        )
        return res
      } catch (e) {
        restoreReview(qc, snap)
        if (!(e instanceof StepUpCancelled)) toast.error(e, 'Approval failed')
        throw e
      } finally {
        void settle()
      }
    },
    [qc, settle, stepUp, toast],
  )

  const reject = useCallback(
    async (action: ProposedAction, reason: string) => {
      const snap = removeFromReview(qc, new Set([action.id]))
      try {
        const res = await unwrap(
          api.POST('/actions/{id}/reject', { params: { path: { id: action.id } }, body: { reason } }),
        )
        toast.ok('Rejected. The agent is told why.')
        return res
      } catch (e) {
        restoreReview(qc, snap)
        toast.error(e, 'Reject failed')
        throw e
      } finally {
        void settle()
      }
    },
    [qc, settle, toast],
  )

  const edit = useCallback(
    async (action: ProposedAction, payload: Record<string, unknown>, comment?: string) => {
      try {
        const res = await unwrap(
          api.POST('/actions/{id}/edit', {
            params: { path: { id: action.id } },
            body: { payload, ...(comment ? { comment } : {}) },
          }),
        )
        toast.ok('Edited. The new version waits for approval.')
        return res
      } catch (e) {
        toast.error(e, 'Edit failed')
        throw e
      } finally {
        void settle()
      }
    },
    [settle, toast],
  )

  const batchApprove = useCallback(
    async (actions: ProposedAction[], comment?: string) => {
      const ids = actions.map((a) => a.id)
      const snap = removeFromReview(qc, new Set(ids))
      try {
        const res = await unwrap(
          api.POST('/actions:batch-approve', { body: { action_ids: ids, ...(comment ? { comment } : {}) } }),
        )
        const failed = res.results.filter((r) => !r.ok)
        if (failed.length > 0) {
          toast.error(
            new Error(failed.map((f) => f.error?.detail ?? f.error?.title ?? f.action_id).join('; ')),
            `${failed.length} of ${ids.length} not approved`,
          )
        } else toast.ok(`Approved ${ids.length} writes`)
        return res
      } catch (e) {
        restoreReview(qc, snap)
        toast.error(e, 'Batch approval failed')
        throw e
      } finally {
        void settle()
      }
    },
    [qc, settle, toast],
  )

  const answer = useCallback(
    async (questionId: string, text: string) => {
      const snap = removeFromReview(qc, new Set([questionId]))
      try {
        const res = await unwrap(
          api.POST('/questions/{id}/answer', {
            params: { path: { id: questionId } },
            body: { answer: text },
          }),
        )
        toast.ok('Answer sent. The agent continues.')
        return res
      } catch (e) {
        restoreReview(qc, snap)
        toast.error(e, 'Answer failed')
        throw e
      } finally {
        void settle()
      }
    },
    [qc, settle, toast],
  )

  const suggestion = useCallback(
    async (id: string, accept: boolean) => {
      const snap = removeFromReview(qc, new Set([id]))
      try {
        if (accept) {
          const m = await unwrap(api.POST('/suggestions/{id}/accept', { params: { path: { id } }, body: {} }))
          toast.ok(`Mission started: ${m.title}`)
          return m
        }
        await unwrap(api.POST('/suggestions/{id}/dismiss', { params: { path: { id } }, body: {} }))
        toast.ok('Suggestion dismissed')
        return null
      } catch (e) {
        restoreReview(qc, snap)
        toast.error(e)
        throw e
      } finally {
        void settle()
      }
    },
    [qc, settle, toast],
  )

  return { approve, reject, edit, batchApprove, answer, suggestion }
}

/** Whether an inline one-click Approve is appropriate (single approver, not destructive, pending). */
export function canQuickApprove(a: ProposedAction): boolean {
  return a.status === 'pending' && a.class !== 'destructive' && a.required_approvals <= 1
}
