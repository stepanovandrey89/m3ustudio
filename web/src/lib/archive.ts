/**
 * Build a Flussonic-style timeshift URL from a live HLS URL and a past
 * moment. Appends `?utc=<start>&lutc=<now>` (or `&utc=…` if the URL already
 * carries query parameters). This is the convention used by TV Club, Edem,
 * Ilook and most other Russian IPTV providers that set `tvg-rec` > 0.
 *
 * The server replaces `/iptv/` with `/arch/` in segment URLs when it sees a
 * valid `utc` timestamp within the catch-up window, effectively returning
 * archive segments instead of the live edge.
 */
export function buildArchiveUrl(liveUrl: string, startIso: string): string {
  const startSec = Math.floor(new Date(startIso).getTime() / 1000)
  const nowSec = Math.floor(Date.now() / 1000)
  if (!Number.isFinite(startSec)) return liveUrl
  const separator = liveUrl.includes('?') ? '&' : '?'
  return `${liveUrl}${separator}utc=${startSec}&lutc=${nowSec}`
}
