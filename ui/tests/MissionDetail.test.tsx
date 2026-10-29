import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Header } from '@/screens/MissionDetail'
import { mission } from './fixtures'
import { makeMe, renderRouted } from './utils'

describe('MissionDetail header', () => {
  it('offers Start with missions:start, not with missions:control alone', async () => {
    const planned = mission({ status: 'planned' })
    await renderRouted(<Header mission={planned} onStop={() => undefined} />, {
      me: makeMe(['missions:read', 'missions:control']),
    })
    expect(screen.queryByRole('button', { name: 'Start' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  it('shows Start to a user with missions:start only', async () => {
    await renderRouted(<Header mission={mission({ status: 'draft' })} onStop={() => undefined} />, {
      me: makeMe(['missions:read', 'missions:start']),
    })
    expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })

  it('gates Run again on missions:start', async () => {
    const done = mission({ status: 'succeeded', ended_at: new Date().toISOString() })
    const { unmount } = await renderRouted(<Header mission={done} onStop={() => undefined} />, {
      me: makeMe(['missions:read']),
    })
    expect(screen.getByRole('button', { name: 'Replay' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run again' })).toBeNull()
    unmount()
    await renderRouted(<Header mission={done} onStop={() => undefined} />, {
      me: makeMe(['missions:read', 'missions:start']),
    })
    expect(screen.getByRole('button', { name: 'Run again' })).toBeInTheDocument()
  })
})
