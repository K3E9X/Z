/**
 * A form from a JSON Schema (playbook `inputs.schema`): strings (enum → select, format date/date-time,
 * multi-line when maxLength > 200), numbers, integers, booleans, string arrays, nested objects.
 * Defaults pre-fill, `required` and bounds are validated before submit; the server validates again.
 */
import { useId, useState } from 'react'
import { Checkbox, Input, Select, Textarea } from './ui'

export interface JsonSchema {
  type?: string | string[]
  title?: string
  description?: string
  enum?: unknown[]
  default?: unknown
  format?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  minItems?: number
}

export type FormValue = Record<string, unknown>

/** Display form of a primitive JSON value. */
function str(v: unknown): string {
  if (v === undefined || v === null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

function typeOf(s: JsonSchema): string {
  const t = Array.isArray(s.type) ? s.type.find((x) => x !== 'null') : s.type
  if (t) return t
  if (s.enum) return 'string'
  if (s.properties) return 'object'
  return 'string'
}

export function defaultsFor(schema: JsonSchema | undefined): FormValue {
  const out: FormValue = {}
  for (const [k, p] of Object.entries(schema?.properties ?? {})) {
    if (p.default !== undefined) out[k] = p.default
    else if (typeOf(p) === 'object' && p.properties) out[k] = defaultsFor(p)
    else if (typeOf(p) === 'boolean') out[k] = false
  }
  return out
}

function label(key: string, s: JsonSchema): string {
  return s.title ?? key.replace(/[_-]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

/** Errors keyed by path ("product", "limits.max"). Empty object = valid. */
export function validate(
  schema: JsonSchema | undefined,
  value: FormValue,
  prefix = '',
): Record<string, string> {
  const errors: Record<string, string> = {}
  const req = new Set(schema?.required ?? [])
  for (const [k, p] of Object.entries(schema?.properties ?? {})) {
    const path = prefix ? `${prefix}.${k}` : k
    const v = value[k]
    const empty = v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)
    if (empty) {
      if (req.has(k)) errors[path] = 'Required'
      continue
    }
    const t = typeOf(p)
    if (t === 'string' && typeof v === 'string') {
      if (p.minLength !== undefined && v.length < p.minLength)
        errors[path] = `At least ${p.minLength} characters`
      else if (p.maxLength !== undefined && v.length > p.maxLength)
        errors[path] = `At most ${p.maxLength} characters`
      else if (p.pattern && !new RegExp(p.pattern).test(v))
        errors[path] = 'Does not match the expected format'
      else if (p.enum && !p.enum.includes(v)) errors[path] = 'Pick one of the options'
    }
    if (t === 'number' || t === 'integer') {
      const n = typeof v === 'number' ? v : Number(v)
      if (Number.isNaN(n)) errors[path] = 'A number'
      else if (t === 'integer' && !Number.isInteger(n)) errors[path] = 'A whole number'
      else if (p.minimum !== undefined && n < p.minimum) errors[path] = `At least ${p.minimum}`
      else if (p.maximum !== undefined && n > p.maximum) errors[path] = `At most ${p.maximum}`
    }
    if (t === 'array' && Array.isArray(v) && p.minItems !== undefined && v.length < p.minItems)
      errors[path] = `At least ${p.minItems}`
    if (t === 'object' && p.properties && typeof v === 'object' && !Array.isArray(v))
      Object.assign(errors, validate(p, v as FormValue, path))
  }
  return errors
}

function FieldFor({
  name,
  schema,
  value,
  onChange,
  required,
  error,
  errors,
  path,
}: {
  name: string
  schema: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
  required: boolean
  error?: string
  errors: Record<string, string>
  path: string
}) {
  const id = useId()
  const t = typeOf(schema)
  const descId = `${id}-d`
  const errId = `${id}-e`
  const describedBy =
    [schema.description ? descId : null, error ? errId : null].filter(Boolean).join(' ') || undefined
  const common = { id, 'aria-describedby': describedBy, 'aria-invalid': error ? true : undefined, required }

  if (t === 'object' && schema.properties) {
    return (
      <fieldset className="m-0 flex flex-col gap-3 rounded-panel border border-line p-4">
        <legend className="px-1 text-meta text-text-2">{label(name, schema)}</legend>
        <SchemaFields
          schema={schema}
          value={(value as FormValue | undefined) ?? {}}
          onChange={onChange}
          errors={errors}
          prefix={path}
        />
      </fieldset>
    )
  }
  if (t === 'boolean') {
    return (
      <Checkbox
        checked={value === true}
        onChange={(v) => {
          onChange(v)
        }}
        label={label(name, schema)}
        description={schema.description}
      />
    )
  }
  let control: React.ReactNode
  if (schema.enum) {
    control = (
      <Select
        {...common}
        value={str(value)}
        onChange={(e) => {
          const raw = e.target.value
          const match = schema.enum?.find((x) => str(x) === raw)
          onChange(raw === '' ? undefined : match)
        }}
      >
        {!required || value === undefined ? <option value="">Choose…</option> : null}
        {schema.enum.map((o) => (
          <option key={str(o)} value={str(o)}>
            {str(o)}
          </option>
        ))}
      </Select>
    )
  } else if (t === 'number' || t === 'integer') {
    control = (
      <Input
        {...common}
        type="number"
        inputMode={t === 'integer' ? 'numeric' : 'decimal'}
        step={t === 'integer' ? 1 : 'any'}
        min={schema.minimum}
        max={schema.maximum}
        value={str(value)}
        onChange={(e) => {
          onChange(e.target.value === '' ? undefined : Number(e.target.value))
        }}
        className="max-w-[220px] font-mono"
      />
    )
  } else if (t === 'array') {
    control = (
      <ArrayInput
        common={common}
        value={value}
        itemType={schema.items ? typeOf(schema.items) : 'string'}
        onChange={onChange}
      />
    )
  } else if ((schema.maxLength ?? 0) > 200) {
    control = (
      <Textarea
        {...common}
        rows={4}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => {
          onChange(e.target.value)
        }}
      />
    )
  } else {
    control = (
      <Input
        {...common}
        type={
          schema.format === 'date'
            ? 'date'
            : schema.format === 'date-time'
              ? 'datetime-local'
              : schema.format === 'uri'
                ? 'url'
                : 'text'
        }
        value={str(value)}
        onChange={(e) => {
          onChange(e.target.value)
        }}
        className={schema.format ? '' : 'max-w-[480px]'}
      />
    )
  }
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-meta text-text-2">
        {label(name, schema)}
        {required && <span className="text-text-3"> · required</span>}
      </label>
      {control}
      {schema.description && (
        <span id={descId} className="text-sm text-text-3">
          {schema.description}
        </span>
      )}
      {error && (
        <span id={errId} role="alert" className="text-sm text-err">
          {error}
        </span>
      )}
    </div>
  )
}

/** Comma-separated list: keeps the raw text while typing so separators are not swallowed. */
function ArrayInput({
  common,
  value,
  itemType,
  onChange,
}: {
  common: Record<string, unknown>
  value: unknown
  itemType: string
  onChange: (v: unknown) => void
}) {
  const [text, setText] = useState(() =>
    Array.isArray(value) ? (value as unknown[]).map(str).join(', ') : '',
  )
  return (
    <Input
      {...common}
      value={text}
      placeholder="Comma-separated"
      onChange={(e) => {
        setText(e.target.value)
        const items = e.target.value
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean)
        onChange(itemType === 'number' || itemType === 'integer' ? items.map(Number) : items)
      }}
    />
  )
}

function SchemaFields({
  schema,
  value,
  onChange,
  errors,
  prefix,
}: {
  schema: JsonSchema
  value: FormValue
  onChange: (v: FormValue) => void
  errors: Record<string, string>
  prefix: string
}) {
  const req = new Set(schema.required ?? [])
  const keys = Object.keys(schema.properties ?? {}).sort((a, b) => Number(req.has(b)) - Number(req.has(a)))
  return (
    <>
      {keys.map((k) => {
        const p = schema.properties?.[k]
        if (!p) return null
        const path = prefix ? `${prefix}.${k}` : k
        return (
          <FieldFor
            key={k}
            name={k}
            schema={p}
            value={value[k]}
            required={req.has(k)}
            error={errors[path]}
            errors={errors}
            path={path}
            onChange={(v) => {
              const { [k]: _old, ...rest } = value
              onChange(v === undefined ? rest : { ...rest, [k]: v })
            }}
          />
        )
      })}
    </>
  )
}

export function SchemaForm({
  schema,
  value,
  onChange,
  errors = {},
  className = '',
}: {
  schema: JsonSchema | undefined
  value: FormValue
  onChange: (v: FormValue) => void
  errors?: Record<string, string>
  className?: string
}) {
  if (!schema?.properties || Object.keys(schema.properties).length === 0) {
    return <div className={`text-meta text-text-3 ${className}`}>This playbook takes no inputs.</div>
  }
  return (
    <div className={`flex flex-col gap-4 ${className}`}>
      <SchemaFields schema={schema} value={value} onChange={onChange} errors={errors} prefix="" />
    </div>
  )
}
