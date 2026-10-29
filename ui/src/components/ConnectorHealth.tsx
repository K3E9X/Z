import type { Connector } from '@/api/types'
import { clock, relative } from '@/lib/format'
import { Dot } from './StatusMark'

/** Health cell: dot + text for healthy, a tinted pill for anything that needs attention. */
export function ConnectorHealth({ connector }: { connector: Connector }) {
  const h = connector.health
  if (connector.review_status === 'pending_review') {
    return <Pill tone="wait">Waiting for exposure review</Pill>
  }
  if (!connector.enabled || h.status === 'disabled') {
    return (
      <span className="inline-flex items-center gap-1.5 text-text-3">
        <Dot tone="idle" />
        Disabled{h.detail ? ` · ${h.detail}` : ''}
      </span>
    )
  }
  switch (h.status) {
    case 'healthy':
      return (
        <span className="inline-flex items-center gap-1.5">
          <Dot tone="ok" />
          Healthy
        </span>
      )
    case 'degraded':
      return <Pill tone="wait">Degraded{h.error_rate ? ` · ${(h.error_rate * 100).toFixed(1)}% errors` : ''}</Pill>
    case 'token_expiring': {
      const exp = connector.credentials?.map((c) => c.expires_at).filter(Boolean).sort()[0]
      return <Pill tone="wait">Token expires {exp ? relative(exp).replace(/^in /, 'in ') : 'soon'}</Pill>
    }
    case 'unreachable':
      return <Pill tone="err">Unreachable{h.last_success_at ? ` since ${clock(h.checked_at ?? h.last_success_at)}` : ''}</Pill>
  }
}

function Pill({ tone, children }: { tone: 'wait' | 'err'; children: React.ReactNode }) {
  return (
    <span className={`inline-flex h-6 items-center gap-1.5 rounded-[6px] px-2 ${tone === 'wait' ? 'bg-wait-bg text-wait' : 'bg-err-bg text-err'}`}>
      <Dot tone={tone} />
      {children}
    </span>
  )
}
