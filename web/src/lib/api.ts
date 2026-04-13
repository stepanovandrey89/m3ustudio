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
  exportUrl: (lang = 'ru') => `/api/export.m3u8?lang=${lang}`,
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
  getLogoRegistry: (page = 1, perPage = 50, q = '', status = '') =>
    request<LogoRegistryResponse>(
      `/api/logos/registry?page=${page}&per_page=${perPage}&q=${encodeURIComponent(q)}&status=${status}`,
    ),
  retryLogo: (channelId: string) =>
    request<{ ok: boolean; found: boolean }>(`/api/logos/retry/${encodeURIComponent(channelId)}`, { method: 'POST' }),
  retryAllLogos: () =>
    request<{ ok: boolean; reset: number }>('/api/logos/retry-all', { method: 'POST' }),
  skipLogo: (channelId: string) =>
    request<{ ok: boolean }>(`/api/logos/skip/${encodeURIComponent(channelId)}`, { method: 'POST' }),
  overrideLogo: (channelId: string, url: string) =>
    request<{ ok: boolean }>(`/api/logos/override/${encodeURIComponent(channelId)}?url=${encodeURIComponent(url)}`, { method: 'POST' }),
}

export interface LogoRegistryItem {
  id: string
  name: string
  epg_url: string
  source: string
  status: 'found' | 'missing' | 'pending' | 'skipped'
  attempts: number
  cached: boolean
}

export interface LogoRegistryResponse {
  items: LogoRegistryItem[]
  total: number
  page: number
  pages: number
  found: number
  missing: number
  pending: number
}
