import { useEffect, useState } from 'react'

/**
 * Returns the current Date, refreshed every `intervalMs` (default 30s).
 * Used by countdown badges so they stay in sync without manual refresh.
 */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState<Date>(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}

/**
 * Format a future ISO timestamp as "Xч Yм" (ru) or "Xh Ym" (en).
 * Returns an empty string once the target is in the past.
 */
export function formatCountdown(
  targetIso: string,
  now: Date,
  lang: string,
): string {
  const target = new Date(targetIso).getTime()
  const diff = target - now.getTime()
  if (!Number.isFinite(diff) || diff <= 0) return ''
  const totalMin = Math.round(diff / 60_000)
  if (totalMin < 1) return lang === 'ru' ? 'меньше минуты' : 'under a minute'
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  if (lang === 'ru') {
    if (h === 0) return `через ${m}м`
    if (m === 0) return `через ${h}ч`
    return `через ${h}ч ${m}м`
  }
  if (h === 0) return `in ${m}m`
  if (m === 0) return `in ${h}h`
  return `in ${h}h ${m}m`
}
