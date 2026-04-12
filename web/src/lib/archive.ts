/**
 * Build a Flussonic-style timeshift URL from a live HLS URL and a past
 * moment. Appends `?utc=<start>&lutc=<now>` (or `&utc=…` if the URL already
 * carries query parameters). This is the convention used by TV Club, Edem,
 * Ilook and most other Russian IPTV providers that set `tvg-rec` > 0.
 *
 * Both `utc` and `lutc` are standard Unix timestamps (seconds since
 * 1970-01-01 00:00:00 UTC). The provider computes `offset = lutc - utc`
 * and seeks that far back in the archive.
 *
 * `offsetSec` compensates for EPG data that is time-shifted relative to
 * the provider's actual archive. Positive = EPG is ahead of reality
 * (most common), negative = EPG is behind.
 */
export function buildArchiveUrl(liveUrl: string, startIso: string, offsetSec = 0): string {
  const startSec = Math.floor(new Date(startIso).getTime() / 1000) - offsetSec
  const nowSec = Math.floor(Date.now() / 1000)
  if (!Number.isFinite(startSec)) return liveUrl
  const separator = liveUrl.includes('?') ? '&' : '?'
  return `${liveUrl}${separator}utc=${startSec}&lutc=${nowSec}`
}

// ---------------------------------------------------------------------------
// Per-channel EPG offset persistence (localStorage)
// ---------------------------------------------------------------------------

const EPG_OFFSET_KEY = 'm3u_epg_offsets_v1'
const EPG_DEFAULT_OFFSET_KEY = 'm3u_epg_default_offset_v1'

/** Global default offset (seconds). 0 = no shift. */
const BUILTIN_DEFAULT_OFFSET = 0

export function loadEpgOffsets(): Record<string, number> {
  try {
    const raw = localStorage.getItem(EPG_OFFSET_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

export function getDefaultEpgOffset(): number {
  try {
    const raw = localStorage.getItem(EPG_DEFAULT_OFFSET_KEY)
    return raw !== null ? Number(raw) : BUILTIN_DEFAULT_OFFSET
  } catch {
    return BUILTIN_DEFAULT_OFFSET
  }
}

export function saveDefaultEpgOffset(sec: number): void {
  try {
    localStorage.setItem(EPG_DEFAULT_OFFSET_KEY, String(sec))
  } catch { /* localStorage full */ }
}

export function saveEpgOffset(channelId: string, offsetSec: number): void {
  const all = loadEpgOffsets()
  const def = getDefaultEpgOffset()
  if (offsetSec === def) {
    // Same as default — remove per-channel override
    delete all[channelId]
  } else {
    all[channelId] = offsetSec
  }
  try {
    localStorage.setItem(EPG_OFFSET_KEY, JSON.stringify(all))
  } catch { /* localStorage full */ }
}

export function getEpgOffset(channelId: string): number {
  const perChannel = loadEpgOffsets()
  return channelId in perChannel ? perChannel[channelId] : getDefaultEpgOffset()
}
