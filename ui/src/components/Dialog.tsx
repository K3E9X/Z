import { useEffect, useRef, type ReactNode } from 'react'
import { IconButton } from './ui'

/**
 * Modal on the native <dialog> element: focus is trapped by the browser, Esc closes, the page behind is
 * inert. No portal library (keeps the build free of injected <style> tags under a strict CSP).
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  width = 520,
  eyebrow,
  labelledBy,
  className = '',
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  eyebrow?: ReactNode
  children: ReactNode
  footer?: ReactNode
  width?: number
  labelledBy?: string
  className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) {
      if (typeof d.showModal === 'function') d.showModal()
      else d.setAttribute('open', '')
    } else if (!open && d.open) {
      if (typeof d.close === 'function') d.close()
      else d.removeAttribute('open')
    }
  }, [open])

  useEffect(() => {
    const d = ref.current
    if (!d) return
    const onCancel = (e: Event) => {
      e.preventDefault()
      onCloseRef.current()
    }
    d.addEventListener('cancel', onCancel)
    return () => {
      d.removeEventListener('cancel', onCancel)
    }
  }, [])

  const titleId = labelledBy ?? 'dialog-title'
  return (
    <dialog
      ref={ref}
      aria-labelledby={title ? titleId : undefined}
      className={`m-auto max-h-[calc(100vh-48px)] max-w-[calc(100vw-24px)] overflow-visible rounded-card border border-line-2 bg-panel p-0 text-text ${className}`}
      style={{ width }}
      onClick={(e) => {
        if (e.target === ref.current) onClose()
      }}
    >
      {open && (
        <div className="flex max-h-[calc(100vh-48px)] flex-col">
          {(title || eyebrow) && (
            <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
              <div className="flex flex-col gap-1">
                {eyebrow && <div className="text-meta text-text-3">{eyebrow}</div>}
                {title && (
                  <h2 id={titleId} className="m-0 text-[17px] font-semibold">
                    {title}
                  </h2>
                )}
              </div>
              <IconButton icon="x" label="Close" onClick={onClose} size={28} />
            </div>
          )}
          <div className="scrollbar-thin min-h-0 flex-1 overflow-auto px-6 pb-5">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>}
        </div>
      )}
    </dialog>
  )
}
