import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MobileBar } from '@/shell/AppShell'
import { makeMe, renderRouted } from './utils'

describe('MobileBar', () => {
  it('lists only the destinations the user may open, like the sidebar', async () => {
    const user = userEvent.setup()
    await renderRouted(<MobileBar />, { me: makeMe(['missions:read', 'audit:read_own']) })
    await user.click(screen.getByRole('button', { name: 'Menu' }))
    const nav = screen.getByRole('navigation', { name: 'Main navigation' })
    const labels = within(nav)
      .getAllByRole('link')
      .map((a) => a.textContent)
    expect(labels).toEqual(['Mission control', 'Review', 'History', 'Documentation', 'Settings'])
  })
})
