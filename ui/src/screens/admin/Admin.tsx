import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { api, unwrap } from '@/api/client'
import { qk } from '@/api/queries'
import type { AuditVerify } from '@/api/types'
import { Forbidden } from '@/components/states'
import { Dot } from '@/components/StatusMark'
import { PageTitle, Tabs } from '@/components/ui'
import { stepUpRemaining, useCan, useCurrentMe } from '@/lib/auth'
import { useNow } from '@/lib/useMedia'
import { GroupMappingTab, RolesTab, TeamsTab } from './AccessTabs'
import { NotificationsTab } from './NotificationsTab'
import { AuditTab, PlatformTab, SessionsTab, SignInTab } from './PlatformTabs'
import { UsersTab } from './Users'

type Tab =
  | 'users'
  | 'teams'
  | 'roles'
  | 'mapping'
  | 'signin'
  | 'sessions'
  | 'audit'
  | 'notifications'
  | 'platform'
const TABS: { id: Tab; label: string }[] = [
  { id: 'users', label: 'Users' },
  { id: 'teams', label: 'Teams' },
  { id: 'roles', label: 'Roles and permissions' },
  { id: 'mapping', label: 'Group mapping' },
  { id: 'signin', label: 'Sign-in' },
  { id: 'sessions', label: 'Sessions and tokens' },
  { id: 'audit', label: 'Audit log' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'platform', label: 'Platform' },
]
/** The bootstrap admin only sees Sign-in and Group mapping (docs/12 §1). */
const BOOTSTRAP_TABS: Tab[] = ['signin', 'mapping']

function StatusStrip({ verify }: { verify: AuditVerify | null }) {
  const auth = useQuery({
    queryKey: qk.admin.authSettings,
    queryFn: () => unwrap(api.GET('/admin/auth-settings')),
  })
  const users = useQuery({
    queryKey: qk.admin.users({}),
    queryFn: () => unwrap(api.GET('/admin/users', { params: { query: { limit: 200 } } })),
  })
  const mapping = useQuery({
    queryKey: qk.admin.groupMapping,
    queryFn: () => unwrap(api.GET('/admin/group-mapping')),
  })
  const a = auth.data
  const local = (a?.local_accounts ?? {}) as {
    breakglass?: { enabled?: boolean }
    bootstrap?: { enabled?: boolean; state?: string }
  }
  const n = users.data?.items.length ?? 0
  const mfa = users.data?.items.filter((u) => u.mfa_enforced).length ?? 0
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-6 gap-y-1.5 rounded-panel border border-line px-5 py-3 text-ui"
    >
      <span className="flex items-center gap-2 font-medium">
        <Dot tone={a?.mode === 'oidc' ? (a.jwks?.ok ? 'ok' : 'wait') : 'wait'} />
        {a?.mode === 'dev' ? 'Development sign-in' : 'GitLab OIDC'}
      </span>
      {a?.issuer_url && (
        <span className="font-mono text-[12.5px] text-text-2">
          {a.issuer_url.replace(/^https?:\/\//, '')}
        </span>
      )}
      <span className="text-text-2">
        {a?.jwks ? (a.jwks.ok ? 'JWKS ok' : 'JWKS failing') : 'JWKS not fetched'} · groups from{' '}
        {a?.groups_claim ?? 'userinfo'} · re-evaluated hourly
      </span>
      <span className="text-text-2">
        {n} users{mfa ? ` · 2FA enforced for ${mfa}` : ''}
        {mapping.data ? ` · allowed: ${mapping.data.allowed_groups.join(', ')}` : ''}
      </span>
      <span className={local.breakglass?.enabled ? 'text-err' : 'text-text-2'}>
        Break-glass: {local.breakglass?.enabled ? 'enabled' : 'disabled'}
      </span>
      {local.bootstrap?.enabled && (
        <span className="text-wait">Bootstrap account {local.bootstrap.state ?? 'active'}</span>
      )}
      <span className="ml-auto text-text-2">
        {verify
          ? verify.ok
            ? `Audit chain verified · ${verify.checked} events · 0 gaps`
            : `Audit chain broken at #${verify.first_bad_seq ?? '?'}`
          : 'Audit chain: verify in the Audit log tab'}
      </span>
    </div>
  )
}

export function Admin() {
  const me = useCurrentMe()
  const search = useSearch({ from: '/admin' })
  const navigate = useNavigate()
  const allowed = useCan('users:manage')
  const now = useNow(15_000)
  const [verify, setVerify] = useState<AuditVerify | null>(null)
  const bootstrap = me.user.local_kind === 'bootstrap'
  const tabs = bootstrap ? TABS.filter((t) => BOOTSTRAP_TABS.includes(t.id)) : TABS
  const fallback: Tab = bootstrap ? 'signin' : 'users'
  const tab = (tabs.some((t) => t.id === search.tab) ? search.tab : fallback) as Tab
  const left = stepUpRemaining(me, 300, now)
  const roles =
    [...new Set(me.user.effective_roles.map((r) => r.role))].join(', ') || me.user.local_kind || 'viewer'

  if (!allowed) {
    return (
      <main className="flex w-full max-w-[1440px] flex-col gap-6 px-10 pt-11">
        <PageTitle title="Admin" />
        <Forbidden permission="users:manage" what="the admin area" />
      </main>
    )
  }
  return (
    <main className="flex w-full max-w-[1440px] flex-col gap-6 px-10 pt-11 pb-14 max-md:px-5">
      <PageTitle
        title="Admin"
        lead="People sign in with GitLab and get their roles from GitLab groups. Every change here shows a diff, asks you to re-authenticate, and lands in the audit log."
        right={
          <span className="text-meta text-text-2">
            Signed in as {me.user.name || me.user.username} · {roles} ·{' '}
            {me.user.local_kind === 'dev'
              ? 'development mode, no step-up'
              : left > 0
                ? `step-up valid ${Math.ceil(left / 60)} min`
                : 'step-up needed for changes'}
          </span>
        }
      />
      {bootstrap && (
        <div role="note" className="rounded-panel bg-wait-bg px-5 py-3 text-ui text-wait">
          You are signed in with the bootstrap account: configure GitLab sign-in and the admin group mapping.
          It is disabled when the first GitLab admin signs in.
        </div>
      )}
      <StatusStrip verify={verify} />
      <Tabs<Tab>
        label="Admin sections"
        value={tab}
        onChange={(t) => void navigate({ to: '/admin', search: { tab: t }, replace: true })}
        items={tabs}
      />
      {tab === 'users' && <UsersTab />}
      {tab === 'teams' && <TeamsTab />}
      {tab === 'roles' && <RolesTab />}
      {tab === 'mapping' && <GroupMappingTab />}
      {tab === 'signin' && <SignInTab />}
      {tab === 'sessions' && <SessionsTab />}
      {tab === 'audit' && <AuditTab verify={verify} onVerified={setVerify} />}
      {tab === 'notifications' && <NotificationsTab />}
      {tab === 'platform' && <PlatformTab />}
    </main>
  )
}
