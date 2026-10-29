import { Link, useNavigate, useParams, useRouterState } from '@tanstack/react-router'
import { useEffect, useMemo, useState, type MouseEvent } from 'react'
import { useDoc, useDocs } from '@/api/queries'
import type { DocSummary } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { QueryState } from '@/components/states'
import { Input } from '@/components/ui'

const SECTIONS: { id: DocSummary['section']; label: string }[] = [
  { id: 'guide', label: 'Guides' },
  { id: 'specification', label: 'Specification' },
  { id: 'operations', label: 'Operations' },
  { id: 'decision', label: 'Decisions (ADR)' },
]
const START = '01-product'

/** The documentation shipped with the running version of Z (GET /docs), for every signed-in user. */
export function Docs() {
  const { slug } = useParams({ strict: false })
  const current = slug ?? START
  const docs = useDocs()
  const page = useDoc(current)
  const navigate = useNavigate()
  const [filter, setFilter] = useState('')

  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const items = (docs.data ?? []).filter((d) => !q || `${d.title} ${d.slug}`.toLowerCase().includes(q))
    return SECTIONS.map((s) => ({ ...s, items: items.filter((d) => d.section === s.id) })).filter(
      (g) => g.items.length > 0,
    )
  }, [docs.data, filter])

  const hash = useRouterState({ select: (s) => s.location.hash })

  // Once the page is shown, bring its #anchor (from a link to /docs/page#section) into view.
  useEffect(() => {
    if (!hash || !page.data) return
    document.getElementById(decodeURIComponent(hash))?.scrollIntoView({ block: 'start' })
  }, [hash, page.data])

  // links between pages stay in the app, #anchors scroll in place; every other link keeps the safe
  // new-tab behaviour of Markdown
  const followInternal = (e: MouseEvent<HTMLDivElement>) => {
    const anchor = (e.target as HTMLElement).closest('a')
    const href = anchor?.getAttribute('href') ?? ''
    if (href.startsWith('#')) {
      e.preventDefault()
      const id = decodeURIComponent(href.slice(1))
      document.getElementById(id)?.scrollIntoView({ block: 'start' })
      void navigate({
        to: '/docs/$slug',
        params: { slug: current },
        hash: id,
        replace: true,
        resetScroll: false,
      })
      return
    }
    if (!href.startsWith('/docs/')) return
    e.preventDefault()
    const [target, section] = href.slice(6).split('#')
    void navigate({
      to: '/docs/$slug',
      params: { slug: target || current },
      ...(section ? { hash: section } : {}),
    })
  }

  return (
    <main className="flex w-full max-w-[1440px] gap-6 px-8 pt-7 pb-14 max-md:flex-col max-md:px-4">
      <nav aria-label="Documentation pages" className="flex w-72 shrink-0 flex-col gap-4 max-md:w-full">
        <h1 className="m-0 text-xl font-semibold">Documentation</h1>
        <Input
          aria-label="Filter pages"
          placeholder="Filter pages"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <QueryState query={docs} what="Documentation" endpoint="GET /docs" compact>
          {() => (
            <div className="flex flex-col gap-4">
              {groups.map((g) => (
                <section key={g.id} className="flex flex-col gap-1">
                  <h2 className="m-0 text-meta font-medium tracking-wide text-text-3 uppercase">{g.label}</h2>
                  {g.items.map((d) => (
                    <Link
                      key={d.slug}
                      to="/docs/$slug"
                      params={{ slug: d.slug }}
                      aria-current={d.slug === current ? 'page' : undefined}
                      className={`rounded-btn px-2 py-1 text-ui ${
                        d.slug === current ? 'bg-raised font-medium text-text' : 'text-text-2 hover:bg-raised'
                      }`}
                    >
                      {d.title}
                    </Link>
                  ))}
                </section>
              ))}
            </div>
          )}
        </QueryState>
      </nav>
      <article className="min-w-0 flex-1" onClickCapture={followInternal}>
        <QueryState query={page} what="This page" endpoint={`GET /docs/${current}`}>
          {() =>
            page.data && (
              <div className="flex flex-col gap-3">
                <div className="font-mono text-meta text-text-3">{page.data.path}</div>
                <Markdown className="max-w-[860px]" headingIds>
                  {page.data.markdown}
                </Markdown>
              </div>
            )
          }
        </QueryState>
      </article>
    </main>
  )
}
