import { forwardRef, useId, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { Avatar } from './Avatar'
import { Icon } from './Icon'

export interface MentionOption {
  trigger: '@' | '#'
  /** Inserted after the trigger, e.g. "vega" → "@vega". */
  insert: string
  label: string
  hint?: string
  avatar?: { initials: string; color?: string } | null
}

export interface MentionHandle {
  insertTrigger: (t: '@' | '#') => void
  focus: () => void
}

/** Token being typed right before the caret: "@ve" → {trigger:'@', query:'ve', start}. */
export function activeToken(
  text: string,
  caret: number,
): { trigger: '@' | '#'; query: string; start: number } | null {
  const before = text.slice(0, caret)
  const m = /(^|\s)([@#])([\p{L}\p{N}._-]*)$/u.exec(before)
  if (!m) return null
  const trigger = m[2] as '@' | '#'
  const query = m[3] ?? ''
  return { trigger, query, start: caret - query.length - 1 }
}

/** Mentions in a text: @handles and #connectors (matched against known options, case-insensitive). */
export function parseMentions(text: string, options: MentionOption[]): MentionOption[] {
  const found: MentionOption[] = []
  const re = /(^|\s)([@#])([\p{L}\p{N}._-]+)/gu
  for (const m of text.matchAll(re)) {
    const trigger = m[2] as '@' | '#'
    const word = (m[3] ?? '').toLowerCase().replace(/[.,;:]+$/, '')
    const opt = options.find(
      (o) =>
        o.trigger === trigger &&
        (o.insert.toLowerCase() === word || o.label.toLowerCase().replace(/\s+/g, '-') === word),
    )
    if (opt && !found.includes(opt)) found.push(opt)
  }
  return found
}

/**
 * The "What should Z do?" composer: `@` completes agents, `#` completes connectors (combobox pattern:
 * arrows move, Enter or Tab picks, Esc closes).
 */
export const MentionTextarea = forwardRef<
  MentionHandle,
  {
    value: string
    onChange: (v: string) => void
    options: MentionOption[]
    placeholder?: string
    label: string
    rows?: number
    onSubmit?: () => void
    className?: string
  }
>(function MentionTextarea(
  { value, onChange, options, placeholder, label, rows = 4, onSubmit, className = '' },
  ref,
) {
  const ta = useRef<HTMLTextAreaElement>(null)
  const listId = useId()
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(0)
  const [dismissed, setDismissed] = useState<number | null>(null)
  const token = activeToken(value, caret)
  const matches = useMemo(() => {
    if (!token) return []
    const q = token.query.toLowerCase()
    return options
      .filter(
        (o) =>
          o.trigger === token.trigger &&
          (o.insert.toLowerCase().includes(q) || o.label.toLowerCase().includes(q)),
      )
      .slice(0, 8)
  }, [token, options])
  const open = !!token && matches.length > 0 && dismissed !== token.start

  const pick = (o: MentionOption) => {
    if (!token) return
    const next = `${value.slice(0, token.start)}${o.trigger}${o.insert} ${value.slice(caret)}`
    onChange(next)
    const pos = token.start + o.insert.length + 2
    requestAnimationFrame(() => {
      ta.current?.focus()
      ta.current?.setSelectionRange(pos, pos)
      setCaret(pos)
    })
  }

  useImperativeHandle(ref, () => ({
    insertTrigger: (t) => {
      const el = ta.current
      const pos = el?.selectionStart ?? value.length
      const needsSpace = pos > 0 && !/\s/.test(value[pos - 1] ?? '')
      const next = `${value.slice(0, pos)}${needsSpace ? ' ' : ''}${t}${value.slice(pos)}`
      onChange(next)
      const np = pos + (needsSpace ? 2 : 1)
      requestAnimationFrame(() => {
        el?.focus()
        el?.setSelectionRange(np, np)
        setCaret(np)
        setDismissed(null)
      })
    },
    focus: () => ta.current?.focus(),
  }))

  return (
    <div className="relative">
      <textarea
        ref={ta}
        aria-label={label}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value)
          setCaret(e.target.selectionStart)
          setActive(0)
          setDismissed(null)
        }}
        onSelect={(e) => {
          setCaret(e.currentTarget.selectionStart)
        }}
        onKeyDown={(e) => {
          if (open) {
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActive((a) => (a + 1) % matches.length)
              return
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((a) => (a - 1 + matches.length) % matches.length)
              return
            }
            if (e.key === 'Enter' || e.key === 'Tab') {
              const o = matches[active]
              if (o) {
                e.preventDefault()
                pick(o)
                return
              }
            }
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              setDismissed(token.start)
              return
            }
          }
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && onSubmit) {
            e.preventDefault()
            onSubmit()
          }
        }}
        className={`w-full resize-y border-none bg-transparent text-[16px] leading-[1.65] text-text outline-none placeholder:text-text-3 ${className}`}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={token.trigger === '@' ? 'Agents' : 'Connectors'}
          className="absolute top-full left-0 z-30 m-0 mt-1 w-[340px] list-none rounded-panel border border-line-2 bg-raised p-1.5"
        >
          {matches.map((o, i) => (
            <li
              key={`${o.trigger}${o.insert}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault()
                pick(o)
              }}
              onMouseEnter={() => {
                setActive(i)
              }}
              className={`flex cursor-pointer items-center gap-2.5 rounded-ctl px-2.5 py-1.5 ${i === active ? 'bg-panel' : ''}`}
            >
              {o.trigger === '@' ? (
                <Avatar avatar={o.avatar ?? null} name={o.label} size={20} />
              ) : (
                <Icon name="connectors" size={14} className="text-text-3" />
              )}
              <span className="min-w-0 flex-1 truncate">{o.label}</span>
              <span className="font-mono text-sm text-text-3">
                {o.trigger}
                {o.insert}
              </span>
              {o.hint && <span className="text-sm text-text-3">{o.hint}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
})
