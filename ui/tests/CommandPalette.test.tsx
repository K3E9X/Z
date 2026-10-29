import { screen } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { CommandPalette } from '@/shell/CommandPalette'
import { emptyApi } from './fixtures'
import { makeMe, renderRouted } from './utils'

const server = setupServer(...emptyApi())
beforeAll(() => {
  server.listen()
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
  Element.prototype.scrollIntoView ??= () => undefined
})
afterEach(() => {
  server.resetHandlers()
})
afterAll(() => {
  server.close()
})

describe('CommandPalette', () => {
  it('offers only the destinations the user may open', async () => {
    await renderRouted(<CommandPalette open initial="" onClose={() => undefined} />, {
      me: makeMe(['missions:read']),
    })
    expect(await screen.findByText('Go to Review')).toBeInTheDocument()
    expect(screen.queryByText('Go to Admin')).toBeNull()
    expect(screen.queryByText('Go to Connectors')).toBeNull()
  })
})
