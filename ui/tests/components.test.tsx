import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { StackedBars } from '@/components/charts'
import { activeToken, MentionTextarea, parseMentions, type MentionOption } from '@/components/MentionTextarea'
import { defaultsFor, SchemaForm, validate, type FormValue, type JsonSchema } from '@/components/SchemaForm'
import { StatusMark } from '@/components/StatusMark'
import { can } from '@/lib/auth'
import { makeMe, renderWithProviders } from './utils'

describe('StatusMark', () => {
  it('always carries a text label next to the color (docs/09 §2)', () => {
    const { container } = render(<StatusMark tone="run" label="Running" />)
    expect(screen.getByText('Running')).toHaveClass('sr-only')
    expect(container.querySelector('[data-tone="run"] svg')).toHaveClass('animate-zspin')
  })
  it('shows the label visibly when asked, for every tone', () => {
    for (const tone of ['wait', 'ok', 'err', 'idle', 'budget'] as const) {
      const { unmount } = render(<StatusMark tone={tone} label={`L-${tone}`} showLabel />)
      expect(screen.getByText(`L-${tone}`)).not.toHaveClass('sr-only')
      unmount()
    }
  })
})

const schema: JsonSchema = {
  type: 'object',
  required: ['product'],
  properties: {
    product: { type: 'string', description: 'Product name in DefectDojo' },
    min_severity: { type: 'string', enum: ['high', 'critical'], default: 'critical' },
    limit: { type: 'integer', minimum: 1, maximum: 50, default: 10 },
    dry_run: { type: 'boolean' },
    repos: { type: 'array', items: { type: 'string' } },
  },
}

function Harness({ onValue }: { onValue: (v: FormValue) => void }) {
  const [v, setV] = useState<FormValue>(defaultsFor(schema))
  return (
    <SchemaForm
      schema={schema}
      value={v}
      errors={validate(schema, v)}
      onChange={(n) => {
        setV(n)
        onValue(n)
      }}
    />
  )
}

describe('SchemaForm', () => {
  it('pre-fills defaults and validates required fields and bounds', () => {
    expect(defaultsFor(schema)).toEqual({ min_severity: 'critical', limit: 10, dry_run: false })
    expect(validate(schema, defaultsFor(schema))).toEqual({ product: 'Required' })
    expect(validate(schema, { product: 'shop', limit: 99 })).toEqual({ limit: 'At most 50' })
    expect(validate(schema, { product: 'shop', limit: 2.5 })).toEqual({ limit: 'A whole number' })
  })
  it('renders typed controls and returns typed values', async () => {
    const user = userEvent.setup()
    let last: FormValue = {}
    render(<Harness onValue={(v) => (last = v)} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Required')
    await user.type(screen.getByLabelText(/Product/), 'infra-boutiques')
    await user.selectOptions(screen.getByLabelText(/Min severity/), 'high')
    await user.clear(screen.getByLabelText(/Limit/))
    await user.type(screen.getByLabelText(/Limit/), '8')
    await user.click(screen.getByLabelText(/Dry run/))
    await user.type(screen.getByLabelText(/Repos/), 'api, billing')
    expect(last).toEqual({
      product: 'infra-boutiques',
      min_severity: 'high',
      limit: 8,
      dry_run: true,
      repos: ['api', 'billing'],
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('StackedBars', () => {
  const series = [
    { key: 'z-heavy', label: 'z-heavy', values: [100, 200, 300] },
    { key: 'z-fast', label: 'z-fast', values: [50, 50, 50] },
  ]
  it('summarises the chart for screen readers', () => {
    render(<StackedBars title="Tokens by model" labels={['08:00', '09:00', '10:00']} series={series} />)
    expect(
      screen.getByRole('img', { name: /Tokens by model: z-heavy 600, z-fast 150; peak 350 at 10:00/ }),
    ).toBeInTheDocument()
  })
  it('has a table view with totals', () => {
    render(
      <StackedBars title="Tokens by model" labels={['08:00', '09:00', '10:00']} series={series} showTable />,
    )
    expect(screen.getByRole('table', { name: 'Tokens by model' })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(4)
    expect(screen.getByText('350')).toBeInTheDocument()
  })
})

const options: MentionOption[] = [
  { trigger: '@', insert: 'vega', label: 'Vega' },
  { trigger: '@', insert: 'atlas', label: 'Atlas' },
  { trigger: '#', insert: 'jira', label: 'Jira' },
]

describe('@agent and #connector completion', () => {
  it('finds the token being typed and the mentions in a text', () => {
    expect(activeToken('triage with @ve', 15)).toEqual({ trigger: '@', query: 've', start: 12 })
    expect(activeToken('mail me@x', 9)).toBeNull()
    expect(
      parseMentions('Ask @atlas to open #jira tickets, then @vega.', options).map((o) => o.insert),
    ).toEqual(['atlas', 'jira', 'vega'])
  })
  it('completes with the keyboard', async () => {
    const user = userEvent.setup()
    function C() {
      const [v, setV] = useState('')
      return <MentionTextarea label="Ask" value={v} onChange={setV} options={options} />
    }
    renderWithProviders(<C />)
    const box = screen.getByRole('combobox', { name: 'Ask' })
    await user.type(box, 'Hand it to @at')
    expect(screen.getByRole('listbox', { name: 'Agents' })).toBeInTheDocument()
    await user.keyboard('{Enter}')
    expect(box).toHaveValue('Hand it to @atlas ')
    await user.type(box, 'and #')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('permission checks', () => {
  it('grants org-wide and team-scoped permissions only where bound', () => {
    const me = makeMe([])
    me.permissions = [
      { permission: 'missions:read', scope: 'org' },
      { permission: 'actions:approve', scope: 'team:t1' },
    ]
    expect(can(me, 'missions:read')).toBe(true)
    expect(can(me, 'actions:approve', 't1')).toBe(true)
    expect(can(me, 'actions:approve', 't2')).toBe(false)
    expect(can(me, 'users:manage')).toBe(false)
    expect(can(null, 'missions:read')).toBe(false)
  })
})
