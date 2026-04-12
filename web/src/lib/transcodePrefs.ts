/**
 * Persistent per-channel "needs transcode" preference.
 *
 * Channels that use AC-3 audio or other browser-unfriendly codecs are
 * remembered here so the player automatically re-enables the ffmpeg
 * transcode session each time the user opens them.
 */

const STORAGE_KEY = 'm3u-studio.transcode-channels'

export function loadTranscodePrefs(): Set<string> {
  if (typeof window === 'undefined') return new Set()
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return new Set(parsed.filter((x) => typeof x === 'string'))
  } catch {
    /* ignore malformed storage */
  }
  return new Set()
}

export function saveTranscodePrefs(prefs: Set<string>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...prefs]))
  } catch {
    /* storage quota / private mode */
  }
}
