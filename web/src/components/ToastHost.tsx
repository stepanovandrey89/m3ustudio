import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, X, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { subscribe, type Toast } from '../lib/toast'
import { cn } from '../lib/cn'

const DEFAULT_DURATION = 6000

export function ToastHost() {
  const [items, setItems] = useState<Toast[]>([])

  useEffect(() => {
    return subscribe((t) => setItems((prev) => [...prev, t]))
  }, [])

  useEffect(() => {
    if (items.length === 0) return
    const timers = items.map((t) =>
      setTimeout(
        () => setItems((prev) => prev.filter((p) => p.id !== t.id)),
        t.durationMs ?? DEFAULT_DURATION,
      ),
    )
    return () => timers.forEach(clearTimeout)
  }, [items])

  const dismiss = (id: string) =>
    setItems((prev) => prev.filter((p) => p.id !== id))

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,360px)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {items.map((toast) => {
          const tone = toast.tone ?? 'success'
          const Icon = tone === 'error' ? XCircle : CheckCircle2
          return (
            <motion.div
              key={toast.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.25 }}
              className={cn(
                'glass-strong pointer-events-auto flex items-start gap-3 rounded-2xl border px-3.5 py-3 shadow-[0_16px_40px_-20px_rgba(0,0,0,0.7)]',
                tone === 'error'
                  ? 'border-[var(--color-rose-primary)]/40'
                  : 'border-[var(--color-cyan-primary)]/30',
              )}
            >
              <Icon
                className={cn(
                  'mt-0.5 h-4 w-4 shrink-0',
                  tone === 'error'
                    ? 'text-[var(--color-rose-primary)]'
                    : 'text-[var(--color-cyan-primary)]',
                )}
              />
              <div className="min-w-0 flex-1 text-[13px] leading-snug text-fog-100">
                <div className="font-medium text-white">{toast.title}</div>
                {toast.description && (
                  <div className="mt-0.5 text-[12px] text-fog-200/70">
                    {toast.description}
                  </div>
                )}
                {toast.action && (
                  <button
                    type="button"
                    onClick={() => {
                      toast.action?.onClick()
                      dismiss(toast.id)
                    }}
                    className="mt-1.5 text-[12px] font-medium text-[var(--color-indigo-primary)] transition hover:brightness-125"
                  >
                    {toast.action.label} →
                  </button>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(toast.id)}
                className="text-fog-200/60 transition hover:text-white"
                aria-label="close"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
