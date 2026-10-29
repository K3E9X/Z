import { useState } from 'react'
import { useReviewActions } from '@/api/review-actions'
import { useCan } from '@/lib/auth'
import { Button, Textarea } from './ui'

/** An agent question (ask_human): options as buttons, or a free answer. Answered ones show who answered. */
export function QuestionCard({
  id,
  text,
  options,
  answer,
  answeredBy,
  compact = false,
}: {
  id: string
  text: string
  options: string[]
  answer?: string | null
  answeredBy?: string | null
  compact?: boolean
}) {
  const actions = useReviewActions()
  const canAnswer = useCan('questions:answer')
  const [busy, setBusy] = useState<string | null>(null)
  const [free, setFree] = useState('')
  const [local, setLocal] = useState<string | null>(null)
  const done = answer ?? local

  const send = (value: string) => {
    setBusy(value)
    actions
      .answer(id, value)
      .then(() => {
        setLocal(value)
      })
      .catch(() => undefined)
      .finally(() => {
        setBusy(null)
      })
  }

  return (
    <div className={`flex flex-col gap-2.5 rounded-panel ${done ? 'border border-line bg-panel' : 'bg-wait-bg'} ${compact ? 'p-3' : 'px-4 py-3.5'}`}>
      <div className={`text-meta font-medium ${done ? 'text-text-3' : 'text-wait'}`}>{done ? 'Question answered' : 'Question for you'}</div>
      <p className="m-0 leading-[1.55] whitespace-pre-wrap">{text}</p>
      {done ? (
        <div className="text-meta text-text-2">
          <span className="text-text-3">Answer: </span>
          {done}
          {answeredBy && <span className="text-text-3"> · {answeredBy}</span>}
        </div>
      ) : canAnswer ? (
        <div className="flex flex-col gap-2">
          {options.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {options.map((o, i) => (
                <Button
                  key={o}
                  size="sm"
                  variant={i === 0 ? 'primary' : 'secondary'}
                  busy={busy === o}
                  onClick={() => {
                    send(o)
                  }}
                >
                  {o}
                </Button>
              ))}
            </div>
          )}
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (free.trim()) send(free.trim())
            }}
          >
            <label className="sr-only" htmlFor={`answer-${id}`}>
              Your answer
            </label>
            <Textarea
              id={`answer-${id}`}
              rows={1}
              value={free}
              onChange={(e) => {
                setFree(e.target.value)
              }}
              placeholder={options.length ? 'Or answer in your own words' : 'Your answer'}
              className="min-h-[32px] flex-1 bg-bg"
            />
            <Button type="submit" size="sm" disabled={!free.trim()} busy={busy === free.trim() && !!free.trim()}>
              Send
            </Button>
          </form>
        </div>
      ) : (
        <div className="text-meta text-text-3">You need the questions:answer permission to answer.</div>
      )}
    </div>
  )
}
