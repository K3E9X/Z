import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Markdown from agents, tickets and tool results is untrusted data (CLAUDE.md invariant 6):
 * raw HTML is dropped (`skipHtml`, no rehype-raw), URLs go through the safe-scheme transform,
 * images are shown as their alt text (no request to arbitrary hosts), links never get `opener`.
 */
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline underline-offset-2">
      {children}
    </a>
  ),
  img: ({ alt }) => <span className="text-text-3">[image{alt ? `: ${alt}` : ''}]</span>,
  code: ({ children, className }) => (
    <code className={`rounded-[4px] bg-code px-1 py-px font-mono text-[0.92em] ${className ?? ''}`}>{children}</code>
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

export function Markdown({ children, className = '' }: { children: string; className?: string }) {
  return (
    <div
      className={`zmd flex flex-col gap-2 leading-[1.55] [&_h1]:m-0 [&_h1]:text-[17px] [&_h1]:font-semibold [&_h2]:m-0 [&_h2]:text-[15px] [&_h2]:font-semibold [&_h3]:m-0 [&_h3]:font-semibold [&_li]:my-0.5 [&_ol]:m-0 [&_ol]:pl-5 [&_p]:m-0 [&_ul]:m-0 [&_ul]:list-disc [&_ul]:pl-5 [&_blockquote]:m-0 [&_blockquote]:border-l-2 [&_blockquote]:border-line-2 [&_blockquote]:pl-3 [&_blockquote]:text-text-2 ${className}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={defaultUrlTransform} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
