import { useEffect, useState } from 'react'

interface PosterState {
  url: string | null
  source: 'tmdb' | 'wikipedia' | 'none'
}

// Module-level cache so flipping between theme tabs doesn't re-fetch.
const memoryCache = new Map<string, PosterState>()
const inflight = new Map<string, Promise<PosterState>>()

/**
 * Resolve a poster URL from the backend. Returns `null` while the request is
 * in flight. Cache is keyed by `<lang>::<keywords>` to match the server.
 */
export function usePoster(
  keywords: string,
  lang: string,
  fallback: string = '',
): PosterState | null {
  const key = `${lang}::${keywords.trim().toLowerCase()}::${fallback.trim().toLowerCase()}`
  const [state, setState] = useState<PosterState | null>(() => memoryCache.get(key) ?? null)

  useEffect(() => {
    if (!keywords.trim()) {
      setState({ url: null, source: 'none' })
      return
    }
    const cached = memoryCache.get(key)
    if (cached) {
      setState(cached)
      return
    }
    let cancelled = false
    const existing = inflight.get(key)
    const qs = new URLSearchParams({ keywords, lang })
    if (fallback && fallback.trim() !== keywords.trim()) qs.set('fallback', fallback)
    const promise =
      existing ??
      fetch(`/api/ai/poster?${qs.toString()}`)
        .then((r) => (r.ok ? r.json() : { url: null, source: 'none' }))
        .then((data: PosterState) => {
          memoryCache.set(key, data)
          inflight.delete(key)
          return data
        })
        .catch(() => {
          inflight.delete(key)
          return { url: null, source: 'none' as const }
        })
    if (!existing) inflight.set(key, promise)
    void promise.then((data) => {
      if (!cancelled) setState(data)
    })
    return () => {
      cancelled = true
    }
  }, [key, keywords, lang, fallback])

  return state
}
