import { parsePatch, structuredPatch, type StructuredPatch } from 'diff'
import { useMemo, useState } from 'react'
import { Icon } from './Icon'

export interface DiffLine {
  kind: 'add' | 'del' | 'ctx' | 'meta'
  text: string
  oldNo: number | null
  newNo: number | null
}

export interface DiffFile {
  name: string
  added: number
  removed: number
  hunks: { header: string; lines: DiffLine[] }[]
}

function fileName(p: StructuredPatch): string {
  const strip = (n: string | undefined) => (n ?? '').replace(/^[ab]\//, '')
  const n = strip(p.newFileName)
  return n && n !== '/dev/null' ? n : strip(p.oldFileName) || 'file'
}

export function toFiles(patches: StructuredPatch[]): DiffFile[] {
  return patches.map((p) => {
    let added = 0
    let removed = 0
    const hunks = p.hunks.map((h) => {
      let o = h.oldStart
      let n = h.newStart
      const lines: DiffLine[] = []
      for (const raw of h.lines) {
        const c = raw[0]
        const text = raw.slice(1)
        if (c === '+') {
          added++
          lines.push({ kind: 'add', text, oldNo: null, newNo: n++ })
        } else if (c === '-') {
          removed++
          lines.push({ kind: 'del', text, oldNo: o++, newNo: null })
        } else if (c === '\\') {
          // "\ No newline at end of file": not a change, not shown
        } else {
          lines.push({ kind: 'ctx', text, oldNo: o++, newNo: n++ })
        }
      }
      return { header: `@@ -${h.oldStart},${h.oldLines} +${h.newStart},${h.newLines} @@`, lines }
    })
    return { name: fileName(p), added, removed, hunks }
  })
}

/** Parse a unified diff (git or plain). Unparseable input falls back to one file of raw lines. */
export function parseUnified(text: string): DiffFile[] {
  try {
    const files = toFiles(parsePatch(text)).filter((f) => f.hunks.length > 0)
    if (files.length > 0) return files
  } catch {
    // fall through to the raw view
  }
  const lines: DiffLine[] = text.split('\n').map((l) => ({
    kind: l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : 'ctx',
    text: l.startsWith('+') || l.startsWith('-') || l.startsWith(' ') ? l.slice(1) : l,
    oldNo: null,
    newNo: null,
  }))
  return [
    {
      name: 'diff',
      added: lines.filter((l) => l.kind === 'add').length,
      removed: lines.filter((l) => l.kind === 'del').length,
      hunks: [{ header: '', lines }],
    },
  ]
}

export function diffTexts(name: string, before: string, after: string, context = 3): DiffFile[] {
  return toFiles([structuredPatch(name, name, before, after, undefined, undefined, { context })])
}

const ROW: Record<DiffLine['kind'], string> = {
  add: 'bg-ok-bg text-ok',
  del: 'bg-err-bg text-err',
  ctx: 'text-text-2',
  meta: 'text-text-3 italic',
}
const SIGN: Record<DiffLine['kind'], string> = { add: '+', del: '−', ctx: ' ', meta: ' ' }

/**
 * Unified diff with line numbers, per-file collapse and +/- counts. Text only (no HTML from the diff),
 * long lines scroll horizontally, every change is also marked by a sign for color-blind users.
 */
export function DiffViewer({
  diff,
  files: given,
  maxHeight,
  showFileHeader = true,
  lineNumbers = true,
  className = '',
}: {
  diff?: string
  files?: DiffFile[]
  maxHeight?: number
  showFileHeader?: boolean
  lineNumbers?: boolean
  className?: string
}) {
  const files = useMemo(() => given ?? (diff ? parseUnified(diff) : []), [given, diff])
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  if (files.length === 0) return <div className="text-meta text-text-3">No changes.</div>
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {files.map((f) => {
        const isCollapsed = collapsed.has(f.name)
        return (
          <div key={f.name} className="overflow-hidden rounded-btn border border-line bg-code">
            {showFileHeader && (
              <button
                type="button"
                aria-expanded={!isCollapsed}
                onClick={() => {
                  setCollapsed((cur) => {
                    const next = new Set(cur)
                    if (next.has(f.name)) next.delete(f.name)
                    else next.add(f.name)
                    return next
                  })
                }}
                className="flex w-full items-center gap-2 border-b border-line px-3 py-2 text-left font-mono text-[12px] text-text-2 hover:text-text"
              >
                <Icon name={isCollapsed ? 'chevronRight' : 'chevronDown'} size={12} />
                <span className="min-w-0 flex-1 truncate">{f.name}</span>
                <span className="text-ok">+{f.added}</span>
                <span className="text-err">−{f.removed}</span>
              </button>
            )}
            {!isCollapsed && (
              <div
                className="scrollbar-thin overflow-auto"
                style={maxHeight ? { maxHeight } : undefined}
                role="table"
                aria-label={`Changes in ${f.name}`}
              >
                <div className="min-w-max py-1.5 font-mono text-[12px] leading-[1.7]">
                  {f.hunks.map((h, hi) => (
                    <div key={hi} role="rowgroup">
                      {h.header && hi > 0 && <div className="px-3 text-text-3">{h.header}</div>}
                      {h.lines.map((l, li) => (
                        <div key={li} role="row" className={`flex ${ROW[l.kind]}`}>
                          {lineNumbers && (
                            <>
                              <span role="cell" className="w-10 flex-none pr-2 text-right text-text-3 select-none">
                                {l.oldNo ?? ''}
                              </span>
                              <span role="cell" className="w-10 flex-none pr-2 text-right text-text-3 select-none">
                                {l.newNo ?? ''}
                              </span>
                            </>
                          )}
                          <span role="cell" className="w-5 flex-none pl-2 select-none" aria-label={l.kind === 'add' ? 'added' : l.kind === 'del' ? 'removed' : undefined}>
                            {SIGN[l.kind]}
                          </span>
                          <span role="cell" className="pr-4 whitespace-pre">
                            {l.text || ' '}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
