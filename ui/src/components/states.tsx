import type { ReactNode } from 'react'
import { isApiError, isForbidden, isNotServed, problemMessage } from '@/api/problem'
import { Icon, type IconName } from './Icon'
import { Skeleton } from './ui'

/**
 * The contract has the endpoint but this z-server does not serve it yet. Shown instead of invented
 * numbers; names the endpoint so an admin knows what is missing.
 */
export function NotServed({
  what,
  endpoint,
  compact = false,
  className = '',
}: {
  what: string
  endpoint: string
  compact?: boolean
  className?: string
}) {
  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1.5 text-meta text-text-3 ${className}`}
        title={endpoint}
      >
        <Icon name="ban" size={12} />
        Not available on this server yet
      </span>
    )
  }
  return (
    <div
      role="note"
      className={`flex flex-col gap-1.5 rounded-panel border border-dashed border-line-2 px-5 py-4 ${className}`}
    >
      <div className="flex items-center gap-2 font-medium">
        <Icon name="ban" size={14} className="text-text-3" />
        {what}: not available on this server yet
      </div>
      <div className="text-meta text-text-3">
        This z-server does not serve <code className="font-mono text-text-2">{endpoint}</code>. The screen
        fills in as soon as it does; nothing here is estimated.
      </div>
    </div>
  )
}

export function Forbidden({
  permission,
  what,
  className = '',
}: {
  permission?: string
  what?: string
  className?: string
}) {
  return (
    <div
      role="note"
      className={`flex flex-col gap-1.5 rounded-panel border border-line px-5 py-4 ${className}`}
    >
      <div className="flex items-center gap-2 font-medium">
        <Icon name="lock" size={14} className="text-text-3" />
        {what ? `You cannot see ${what}` : 'Not allowed'}
      </div>
      <div className="text-meta text-text-3">
        {permission ? (
          <>
            This needs the <code className="font-mono text-text-2">{permission}</code> permission. Your roles
            come from your GitLab groups; ask an admin if you need it.
          </>
        ) : (
          'Your roles do not include this. Ask an admin if you need it.'
        )}
      </div>
    </div>
  )
}

export function ErrorState({ error, className = '' }: { error: unknown; className?: string }) {
  return (
    <div
      role="alert"
      className={`flex flex-col gap-1 rounded-panel border border-line bg-err-bg px-5 py-4 ${className}`}
    >
      <div className="flex items-center gap-2 font-medium text-err">
        <Icon name="x" size={14} />
        {isApiError(error) ? error.problem.title : 'Something went wrong'}
      </div>
      <div className="text-meta text-text-2">
        {problemMessage(error)}
        {isApiError(error) && error.problem.request_id && (
          <span className="ml-2 font-mono text-text-3">request {error.problem.request_id.slice(0, 12)}</span>
        )}
      </div>
    </div>
  )
}

export function EmptyState({
  icon = 'check',
  title,
  children,
  action,
  className = '',
}: {
  icon?: IconName
  title: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={`flex flex-col items-start gap-2 px-1 py-6 ${className}`}>
      <div className="flex items-center gap-2 font-medium">
        <Icon name={icon} size={14} className="text-text-3" />
        {title}
      </div>
      {children && <div className="max-w-[560px] text-meta text-text-3">{children}</div>}
      {action && <div className="pt-1">{action}</div>}
    </div>
  )
}

/** Standard handling of a query's loading / not served / forbidden / error states. */
export function QueryState({
  query,
  what,
  endpoint,
  skeleton,
  children,
  compact = false,
}: {
  query: { isPending: boolean; error: unknown; data?: unknown }
  what: string
  endpoint: string
  skeleton?: ReactNode
  children: () => ReactNode
  compact?: boolean
}) {
  if (query.error && query.data === undefined) {
    if (isNotServed(query.error)) return <NotServed what={what} endpoint={endpoint} compact={compact} />
    if (isForbidden(query.error) && isApiError(query.error))
      return <Forbidden permission={query.error.problem.permission} what={what.toLowerCase()} />
    return <ErrorState error={query.error} />
  }
  if (query.isPending) {
    return (
      <>
        {skeleton ?? (
          <div className="flex flex-col gap-2" aria-busy="true" aria-label={`Loading ${what.toLowerCase()}`}>
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
          </div>
        )}
      </>
    )
  }
  return <>{children()}</>
}
