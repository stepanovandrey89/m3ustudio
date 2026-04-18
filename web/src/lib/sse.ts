/**
 * Minimal fetch-based Server-Sent Events reader.
 *
 * Emits parsed JSON objects one at a time. Handles partial chunks and the
 * literal `[DONE]` sentinel used by /api/ai/chat. Caller is expected to stop
 * iteration either by breaking the loop or by aborting the signal.
 */

export interface SSEOptions {
  signal?: AbortSignal
  body?: unknown
  headers?: Record<string, string>
  method?: 'GET' | 'POST'
}

export async function* sseStream<T = unknown>(
  url: string,
  opts: SSEOptions = {},
): AsyncGenerator<T, void, void> {
  const resp = await fetch(url, {
    method: opts.method ?? 'POST',
    headers: {
      'content-type': 'application/json',
      accept: 'text/event-stream',
      ...opts.headers,
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  })
  if (!resp.ok || !resp.body) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`SSE ${resp.status} ${resp.statusText}: ${detail}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''
    for (const part of parts) {
      const dataLine = part
        .split('\n')
        .find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      const payload = dataLine.slice(5).trim()
      if (payload === '[DONE]') return
      try {
        yield JSON.parse(payload) as T
      } catch {
        // silently skip malformed chunks
      }
    }
  }
}
