import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { problemMessage } from '@/api/problem'
import { Icon } from './Icon'

interface Toast {
  id: number
  tone: 'ok' | 'err' | 'info'
  text: string
  action?: { label: string; run: () => void }
}

interface ToastApi {
  ok: (text: string, action?: Toast['action']) => void
  error: (e: unknown, prefix?: string) => void
  info: (text: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)
let seq = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((t: Omit<Toast, 'id'>) => {
    const id = seq++
    setToasts((cur) => [...cur.slice(-3), { ...t, id }])
    setTimeout(
      () => {
        setToasts((cur) => cur.filter((x) => x.id !== id))
      },
      t.tone === 'err' ? 9000 : 5000,
    )
  }, [])
  const api = useMemo<ToastApi>(
    () => ({
      ok: (text, action) => {
        push(action ? { tone: 'ok', text, action } : { tone: 'ok', text })
      },
      info: (text) => {
        push({ tone: 'info', text })
      },
      error: (e, prefix) => {
        push({ tone: 'err', text: prefix ? `${prefix}: ${problemMessage(e)}` : problemMessage(e) })
      },
    }),
    [push],
  )
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        role="status"
        className="pointer-events-none fixed top-[60px] right-4 z-50 flex w-[min(420px,calc(100vw-32px))] flex-col gap-2"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className="pointer-events-auto flex items-start gap-2.5 rounded-panel border border-line-2 bg-raised px-4 py-3 text-ui"
          >
            <Icon
              name={t.tone === 'err' ? 'x' : t.tone === 'ok' ? 'check' : 'bell'}
              size={14}
              className={`mt-[2px] ${t.tone === 'err' ? 'text-err' : t.tone === 'ok' ? 'text-ok' : 'text-text-2'}`}
            />
            <span className="flex-1">{t.text}</span>
            {t.action && (
              <button
                type="button"
                className="text-ui font-medium underline underline-offset-2"
                onClick={() => {
                  t.action?.run()
                  setToasts((cur) => cur.filter((x) => x.id !== t.id))
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast outside ToastProvider')
  return ctx
}
