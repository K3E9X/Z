import type { ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Markdown from agents, tickets and tool results is untrusted data (CLAUDE.md invariant 6):
 * raw HTML is dropped (`skipHtml`, no rehype-raw), URLs go through the safe-scheme transform,
 * images are shown as their alt text (no request to arbitrary hosts), links never get `opener`.
 */
function ExternalLink({ href, children }: { href?: string; children?: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="underline underline-offset-2"
    >
      {children}
    </a>
  )
}

const components: Components = {
  a: ({ href, children }) => <ExternalLink href={href}>{children}</ExternalLink>,
  img: ({ alt }) => <span className="text-text-3">[image{alt ? `: ${alt}` : ''}]</span>,
  code: ({ children, className }) => (
    <code className={`rounded-[4px] bg-code px-1 py-px font-mono text-[0.92em] ${className ?? ''}`}>
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="scrollbar-thin overflow-x-auto rounded-btn border border-line bg-code p-3 font-mono text-[12px] leading-[1.6] [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-ui [&_td]:border-b [&_td]:border-line [&_td]:px-2 [&_td]:py-1.5 [&_th]:border-b [&_th]:border-line-2 [&_th]:px-2 [&_th]:py-1.5">
        {children}
      </table>
    </div>
  ),
}

interface HastNode {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

function hastText(n: HastNode): string {
  return n.type === 'text' ? (n.value ?? '') : (n.children ?? []).map(hastText).join('')
}

/** GitHub-style heading slug: "4. Tool gateway (MCP)" -> "4-tool-gateway-mcp". */
export function headingSlug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-')
}

/** rehype transform: an id on every heading, repeated titles get -1, -2... (computed per document). */
function rehypeHeadingIds() {
  return (tree: HastNode) => {
    const seen = new Map<string, number>()
    const visit = (n: HastNode) => {
      if (n.type === 'element' && n.tagName && /^h[1-6]$/.test(n.tagName)) {
        const base = headingSlug(hastText(n)) || 'section'
        const k = seen.get(base) ?? 0
        seen.set(base, k + 1)
        n.properties = { ...n.properties, id: k ? `${base}-${k}` : base }
      }
      n.children?.forEach(visit)
    }
    visit(tree)
  }
}

/** Same components, but a same-page #anchor link stays in the page instead of opening a tab. */
const anchorComponents: Components = {
  ...components,
  a: ({ href, children }) =>
    href?.startsWith('#') ? (
      <a href={href} className="underline underline-offset-2">
        {children}
      </a>
    ) : (
      <ExternalLink href={href}>{children}</ExternalLink>
    ),
}

/**
 * `headingIds` gives headings slug ids and keeps #anchor links on the page. Only for first-party
 * documents (the Docs screen): ids taken from untrusted text could shadow the app's own.
 */
export function Markdown({
  children,
  className = '',
  headingIds = false,
}: {
  children: string
  className?: string
  headingIds?: boolean
}) {
  return (
    <div
      className={`zmd flex flex-col gap-2 leading-[1.55] [&_h1]:m-0 [&_h1]:text-[17px] [&_h1]:font-semibold [&_h2]:m-0 [&_h2]:text-[15px] [&_h2]:font-semibold [&_h3]:m-0 [&_h3]:font-semibold [&_li]:my-0.5 [&_ol]:m-0 [&_ol]:pl-5 [&_p]:m-0 [&_ul]:m-0 [&_ul]:list-disc [&_ul]:pl-5 [&_blockquote]:m-0 [&_blockquote]:border-l-2 [&_blockquote]:border-line-2 [&_blockquote]:pl-3 [&_blockquote]:text-text-2 ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={defaultUrlTransform}
        rehypePlugins={headingIds ? [rehypeHeadingIds] : []}
        components={headingIds ? anchorComponents : components}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
