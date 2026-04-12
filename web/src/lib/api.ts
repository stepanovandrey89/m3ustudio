/**
 * Minimal typed client. Returns parsed JSON or throws a useful error.
 * All calls go through Vite's /api proxy to the local FastAPI app.
 */

import type { DuplicatesResponse, EpgResponse, MainOperation, MainResponse, SourceOperation, SourceResponse } from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { 'content-type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail = text
    try {
      const body = JSON.parse(text)
      if (typeof body?.detail === 'string') detail = body.detail
    } catch { /* not JSON — use raw text */ }
    throw new Error(`${resp.status} ${resp.statusText}: ${detail}`)
  }
  return resp.json() as Promise<T>
}

export const api = {
  getSource: () => request<SourceResponse>('/api/source'),
  patchSource: (op: SourceOperation) =>
    request<SourceResponse>('/api/source', { method: 'PATCH', body: JSON.stringify(op) }),
  getMain: () => request<MainResponse>('/api/main'),
  patchMain: (op: MainOperation) =>
    request<MainResponse>('/api/main', { method: 'PATCH', body: JSON.stringify(op) }),
  reload: () =>
    request<{ ok: boolean; total: number }>('/api/reload', { method: 'POST' }),
  getEpg: (channelId: string) =>
    request<EpgResponse>(`/api/epg/${encodeURIComponent(channelId)}`),
  startTranscode: (channelId: string) =>
    request<{ ok: boolean; manifest_url: string; started_at: number }>(
      `/api/transcode/${encodeURIComponent(channelId)}/start`,
      { method: 'POST' },
    ),
  stopTranscode: (channelId: string) =>
    request<{ stopped: boolean }>(
      `/api/transcode/${encodeURIComponent(channelId)}`,
      { method: 'DELETE' },
    ),
  transcodeManifestUrl: (channelId: string) =>
    `/api/transcode/${encodeURIComponent(channelId)}/index.m3u8`,
  exportUrl: () => '/api/export.m3u8',
  exportNamesUrl: () => '/api/export/names.txt',
  logoUrl: (channelId: string) => `/api/logo/${channelId}?v=2`,
  proxyUrl: (upstream: string) =>
    `/api/proxy?u=${encodeURIComponent(upstream)}`,
  importPlaylist: async (file: File, names?: string): Promise<{ ok: boolean; total: number }> => {
    const form = new FormData()
    form.append('file', file)
    if (names) form.append('names', names)
    const resp = await fetch('/api/import', { method: 'POST', body: form })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(String(body?.detail ?? resp.statusText))
    }
    return resp.json() as Promise<{ ok: boolean; total: number }>
  },
  clearState: () =>
    request<{ ok: boolean }>('/api/state/clear', { method: 'POST' }),
  getDuplicates: () => request<DuplicatesResponse>('/api/duplicates'),
  getDefaultNames: () => request<{ names: string[] }>('/api/defaults/names'),
  setDefaultNames: (names: string[]) =>
    request<{ ok: boolean; count: number }>('/api/defaults/names', {
      method: 'PUT',
      body: JSON.stringify({ names }),
    }),
  getGroupOrder: () => request<{ order: string[] }>('/api/groups/order'),
  setGroupOrder: (order: string[]) =>
    request<{ ok: boolean }>('/api/groups/order', {
      method: 'PUT',
      body: JSON.stringify({ order }),
    }),
}
