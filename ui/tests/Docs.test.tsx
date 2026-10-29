import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { Docs } from '@/screens/Docs'
import { renderRouted } from './utils'

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
})
