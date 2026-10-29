/** Small primitives restyled to the tokens: hairline borders, inverted primary, no shadows. */
import {
  forwardRef,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { Icon, type IconName } from './Icon'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet'
type Size = 'xs' | 'sm' | 'md' | 'lg'

const VARIANT: Record<Variant, string> = {
  primary: 'bg-inv-bg text-inv-text font-medium border border-transparent hover:opacity-90',
  secondary: 'border border-line-2 bg-transparent text-text hover:bg-raised',
  ghost: 'border border-transparent bg-transparent text-text-2 hover:bg-raised hover:text-text',
  danger: 'border border-line-2 bg-transparent text-err hover:bg-err-bg',
  quiet: 'border border-transparent bg-transparent text-text-3 hover:text-text underline-offset-2',
}
const SIZE: Record<Size, string> = {
  xs: 'h-6 px-2 text-sm rounded-ctl gap-1',
  sm: 'h-[30px] px-3 text-ui rounded-ctl gap-1.5',
  md: 'h-8 px-3.5 rounded-btn gap-2',
  lg: 'h-[38px] px-4 text-[14px] rounded-btn gap-2',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  icon?: IconName
  busy?: boolean
  /** Shortcut hint shown after the label, e.g. "⌘↵". */
  kbd?: string
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, busy, kbd, className = '', children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex flex-none items-center justify-center whitespace-nowrap transition-colors disabled:opacity-50 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
      {...rest}
    >
      {busy ? <Spinner size={13} /> : icon ? <Icon name={icon} size={14} /> : null}
      {children}
      {kbd && (
        <span
          className={`ml-1 font-mono text-micro ${variant === 'primary' ? 'text-inv-text opacity-60' : 'text-text-3'}`}
          aria-hidden="true"
        >
          {kbd}
        </span>
      )}
    </button>
  )
})

export function IconButton({
  icon,
  label,
  className = '',
  size = 32,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string; size?: number }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`inline-flex flex-none items-center justify-center rounded-btn text-text-2 hover:bg-raised hover:text-text disabled:opacity-50 ${className}`}
      style={{ width: size, height: size }}
      {...rest}
    >
      <Icon name={icon} size={16} />
    </button>
  )
}

export function Spinner({ size = 14, label }: { size?: number; label?: string }) {
  return (
    <span role={label ? 'status' : undefined} className="inline-flex items-center">
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" className="animate-zspin">
        <circle cx="8" cy="8" r="6" fill="none" stroke="var(--line-2)" strokeWidth="2" />
        <path d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-line-2 px-1 font-mono text-micro text-text-2">
      {children}
    </kbd>
  )
}

export function Chip({
  children,
  tone,
  className = '',
}: {
  children: ReactNode
  tone?: 'wait' | 'err' | 'ok' | 'run'
  className?: string
}) {
  const t =
    tone === 'wait'
      ? 'bg-wait-bg text-wait'
      : tone === 'err'
        ? 'bg-err-bg text-err'
        : tone === 'ok'
          ? 'bg-ok-bg text-ok'
          : tone === 'run'
            ? 'bg-run-bg text-run'
            : 'bg-raised text-text-2'
  return (
    <span className={`inline-flex h-6 items-center gap-1.5 rounded-[6px] px-2 text-sm ${t} ${className}`}>{children}</span>
  )
}

export function SectionHeader({
  id,
  title,
  right,
  className = '',
}: {
  id?: string
  title: ReactNode
  right?: ReactNode
  className?: string
}) {
  return (
    <div className={`flex items-baseline justify-between gap-4 border-b border-line pb-2.5 ${className}`}>
      <h2 id={id} className="m-0 text-ui font-medium text-text-2">
        {title}
      </h2>
      {right && <div className="flex items-center gap-3 text-meta text-text-3">{right}</div>}
    </div>
  )
}

export function Panel({
  children,
  className = '',
  as: As = 'section',
  ...rest
}: {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'aside' | 'article'
  'aria-label'?: string
  'aria-labelledby'?: string
}) {
  return (
    <As className={`rounded-panel border border-line bg-panel ${className}`} {...rest}>
      {children}
    </As>
  )
}

export function PageTitle({ title, lead, right }: { title: string; lead?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-6">
      <div className="flex min-w-0 max-w-[720px] flex-col gap-2">
        <h1 className="m-0 font-display-face text-display leading-[1.05]">{title}</h1>
        {lead && <p className="m-0 text-[15px] leading-[1.5] text-text-2">{lead}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  )
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
  describedBy,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  disabled?: boolean
  describedBy?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => {
        onChange(!checked)
      }}
      className={`relative inline-flex h-4 w-7 flex-none items-center rounded-full border transition-colors disabled:opacity-50 ${
        checked ? 'border-inv-bg bg-inv-bg' : 'border-line-2 bg-raised'
      }`}
    >
      <span
        aria-hidden="true"
        className={`absolute h-3 w-3 rounded-full transition-transform ${
          checked ? 'translate-x-[13px] bg-inv-text' : 'translate-x-[1px] bg-text-3'
        }`}
      />
    </button>
  )
}

export interface TabItem<T extends string> {
  id: T
  label: ReactNode
  badge?: ReactNode
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
  variant = 'underline',
  className = '',
}: {
  items: TabItem<T>[]
  value: T
  onChange: (v: T) => void
  label: string
  variant?: 'underline' | 'pill'
  className?: string
}) {
  const onKey = (e: React.KeyboardEvent, idx: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const next = items[(idx + (e.key === 'ArrowRight' ? 1 : items.length - 1)) % items.length]
    if (next) {
      onChange(next.id)
      document.getElementById(`tab-${label}-${next.id}`)?.focus()
    }
  }
  return (
    <div
      role="tablist"
      aria-label={label}
      className={`flex flex-wrap items-center ${variant === 'underline' ? 'gap-6 border-b border-line' : 'gap-1'} ${className}`}
    >
      {items.map((t, i) => {
        const active = t.id === value
        return (
          <button
            key={t.id}
            id={`tab-${label}-${t.id}`}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onKeyDown={(e) => {
              onKey(e, i)
            }}
            onClick={() => {
              onChange(t.id)
            }}
            className={
              variant === 'underline'
                ? `-mb-px flex h-10 items-center gap-1.5 border-b-2 ${active ? 'border-text text-text' : 'border-transparent text-text-2 hover:text-text'}`
                : `flex h-7 items-center gap-1.5 rounded-ctl px-2.5 ${active ? 'bg-raised text-text font-medium' : 'text-text-2 hover:text-text'}`
            }
          >
            {t.label}
            {t.badge !== undefined && t.badge !== null && <span className="text-text-3">{t.badge}</span>}
          </button>
        )
      })}
    </div>
  )
}

/** Fields are full width unless the caller sets a width. */
function width(className: string): string {
  return /(^|\s)w-|(^|\s)flex-1/.test(className) ? '' : 'w-full'
}

const FIELD =
  'rounded-btn border border-line-2 bg-transparent px-3 text-text placeholder:text-text-3 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className = '', ...rest },
  ref,
) {
  return <input ref={ref} className={`h-[34px] ${width(className)} ${FIELD} ${className}`} {...rest} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className = '', ...rest }, ref) {
    return <textarea ref={ref} className={`py-2 leading-[1.5] ${width(className)} ${FIELD} ${className}`} {...rest} />
  },
)

export function Select({ className = '', children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`h-[34px] ${width(className)} ${FIELD} bg-bg pr-8 ${className}`} {...rest}>
      {children}
    </select>
  )
}

export function Field({
  label,
  hint,
  error,
  children,
  className = '',
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  children: (id: string, describedBy: string | undefined) => ReactNode
  className?: string
}) {
  const id = useId()
  const hintId = `${id}-hint`
  const errId = `${id}-err`
  const describedBy = [hint ? hintId : null, error ? errId : null].filter(Boolean).join(' ') || undefined
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-meta text-text-2">
        {label}
      </label>
      {children(id, describedBy)}
      {hint && (
        <div id={hintId} className="text-sm text-text-3">
          {hint}
        </div>
      )}
      {error && (
        <div id={errId} className="text-sm text-err">
          {error}
        </div>
      )}
    </div>
  )
}

export function ProgressBar({
  value,
  max,
  tone = 'text',
  className = '',
  label,
}: {
  value: number
  max: number
  tone?: 'text' | 'wait' | 'err' | 'run' | 'ok'
  className?: string
  label?: string
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const bg = { text: 'bg-text', wait: 'bg-wait', err: 'bg-err', run: 'bg-run', ok: 'bg-ok' }[tone]
  return (
    <span
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-label={label}
      className={`flex h-1 overflow-hidden rounded-[2px] bg-line-2 ${className}`}
    >
      <span className={bg} style={{ width: `${pct}%` }} />
    </span>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <span aria-hidden="true" className={`block animate-zpulse rounded-ctl bg-raised ${className}`} />
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  className = '',
  description,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  disabled?: boolean
  className?: string
  description?: ReactNode
}) {
  return (
    <label className={`flex items-start gap-2.5 ${disabled ? 'opacity-60' : 'cursor-pointer'} ${className}`}>
      <input
        type="checkbox"
        className="mt-[3px] h-[14px] w-[14px] flex-none"
        checked={checked}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.target.checked)
        }}
      />
      <span className="flex flex-col">
        <span>{label}</span>
        {description && <span className="text-meta text-text-3">{description}</span>}
      </span>
    </label>
  )
}

export function Radio<T extends string>({
  name,
  value,
  current,
  onChange,
  label,
  disabled,
  className = '',
}: {
  name: string
  value: T
  current: T
  onChange: (v: T) => void
  label: ReactNode
  disabled?: boolean
  className?: string
}) {
  return (
    <label className={`flex items-center gap-2.5 ${disabled ? 'opacity-60' : 'cursor-pointer'} ${className}`}>
      <input
        type="radio"
        name={name}
        value={value}
        checked={current === value}
        disabled={disabled}
        onChange={() => {
          onChange(value)
        }}
        className="h-[14px] w-[14px] flex-none"
      />
      <span>{label}</span>
    </label>
  )
}

/** Key/value rows used in detail panels ("Runs as", "Read as", "Model"...). */
export function DefList({ rows, className = '' }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={`m-0 grid grid-cols-[minmax(84px,auto)_1fr] gap-x-4 gap-y-2 ${className}`}>
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-meta text-text-3">{k}</dt>
          <dd className="m-0 min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
