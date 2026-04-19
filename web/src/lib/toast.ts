export interface ToastAction {
  label: string
  onClick: () => void
}

export interface Toast {
  id: string
  title: string
  description?: string
  action?: ToastAction
  tone?: 'success' | 'error'
  durationMs?: number
}

type Handler = (toast: Toast) => void

const handlers = new Set<Handler>()

export function notify(input: Omit<Toast, 'id'>): string {
  const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const toast: Toast = { id, ...input }
  handlers.forEach((h) => h(toast))
  return id
}

export function subscribe(h: Handler): () => void {
  handlers.add(h)
  return () => handlers.delete(h)
}
