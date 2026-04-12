/**
 * Compare two strings: Cyrillic names first (А→Я), then Latin (A→Z).
 * Case-insensitive. Used for channel and group name sorting.
 */

const CYR_RE = /^[а-яёА-ЯЁ]/

export function cyrFirstCompare(a: string, b: string): number {
  const aCyr = CYR_RE.test(a)
  const bCyr = CYR_RE.test(b)
  if (aCyr && !bCyr) return -1
  if (!aCyr && bCyr) return 1
  return a.localeCompare(b, aCyr ? 'ru' : 'en', { sensitivity: 'base' })
}
