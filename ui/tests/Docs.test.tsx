import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { headingSlug, Markdown } from '@/components/Markdown'
import { Docs } from '@/screens/Docs'
import { renderRoute, renderRouted, renderWithProviders } from './utils'

const pages = [
  { slug: 'cli', title: 'z, the command line', section: 'guide', path: 'cli/README.md' },
  { slug: '01-product', title: 'Product', section: 'specification', path: 'docs/01-product.md' },
  { slug: '07-security', title: 'Security', section: 'specification', path: 'docs/07-security.md' },
  { slug: 'deploy', title: 'Deploying Z', section: 'operations', path: 'deploy/README.md' },
  {
    slug: 'adr-0030-recorded-future-connector',
    title: 'ADR 0030',
    section: 'decision',
    path: 'docs/adr/0030.md',
  },
]
const server = setupServer(
  http.get('*/api/v1/docs', () => HttpResponse.json(pages)),
  http.get('*/api/v1/docs/07-security', () =>
    HttpResponse.json({
      ...pages[2],
      markdown:
        '# Security\n\nRead [the data classes](#data-classes) first.\n\n## Data classes\n\nC0 to C3.\n\n## Data classes\n\nAgain.',
    }),
  ),
  http.get('*/api/v1/docs/01-product', () =>
    HttpResponse.json({
      ...pages[1],
      markdown:
        '# Product\n\nZ runs agents. See [the security model](/docs/07-security).\n\n| a | b |\n|---|---|\n| 1 | 2 |',
    }),
  ),
)
beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe('Docs', () => {
  it('lists the pages by section and shows the start page as Markdown', async () => {
    const user = userEvent.setup()
    await renderRouted(<Docs />)
    expect(await screen.findByRole('heading', { name: 'Product' })).toBeInTheDocument()
    expect(screen.getByText('docs/01-product.md')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'the security model' })).toHaveAttribute(
      'href',
      '/docs/07-security',
    )
    for (const section of ['Guides', 'Specification', 'Operations', 'Decisions (ADR)'])
      expect(screen.getByRole('heading', { name: section })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Filter pages'), 'deploy')
    expect(screen.getByRole('link', { name: 'Deploying Z' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Security' })).not.toBeInTheDocument()
  })

  it('gives headings ids and scrolls to a same-page anchor in place', async () => {
    const user = userEvent.setup()
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const { router } = await renderRoute('/docs/$slug', '/docs/07-security', Docs)
    const heading = (await screen.findAllByRole('heading', { name: 'Data classes' }))[0]
    expect(heading).toHaveAttribute('id', 'data-classes')
    expect(screen.getAllByRole('heading', { name: 'Data classes' })[1]).toHaveAttribute(
      'id',
      'data-classes-1',
    )
    const link = screen.getByRole('link', { name: 'the data classes' })
    expect(link).not.toHaveAttribute('target')
    await user.click(link)
    expect(scroll.mock.contexts[0]).toBe(heading)
    expect(router.state.location.pathname).toBe('/docs/07-security')
    expect(router.state.location.hash).toBe('data-classes')
  })
})

describe('Markdown', () => {
  it('adds heading ids only when asked, and keeps other links in a new tab', () => {
    expect(headingSlug('4. Tool gateway (MCP)')).toBe('4-tool-gateway-mcp')
    const md = '## Scope\n\n[top](#scope) and [site](https://example.org)'
    const { unmount } = renderWithProviders(<Markdown>{md}</Markdown>)
    expect(screen.getByRole('heading', { name: 'Scope' })).not.toHaveAttribute('id')
    expect(screen.getByRole('link', { name: 'top' })).toHaveAttribute('target', '_blank')
    unmount()
    renderWithProviders(<Markdown headingIds>{md}</Markdown>)
    expect(screen.getByRole('heading', { name: 'Scope' })).toHaveAttribute('id', 'scope')
    expect(screen.getByRole('link', { name: 'top' })).not.toHaveAttribute('target')
    expect(screen.getByRole('link', { name: 'site' })).toHaveAttribute('target', '_blank')
  })
})
