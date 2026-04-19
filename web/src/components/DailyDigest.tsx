import { AnimatePresence, motion } from 'framer-motion'
import {
  BookmarkCheck,
  Check,
  CheckCircle2,
  Clock,
  Film,
  Loader2,
  PlayCircle,
  RefreshCw,
  Sparkles,
  Sunrise,
  Trophy,
  Video,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { useNow, formatCountdown } from '../hooks/useNow'
import { usePoster } from '../hooks/usePoster'
import { cn } from '../lib/cn'
import type { DigestEntry, DigestResponse, DigestTheme } from '../types'

function formatGeneratedAt(
  d: { date: string; generated_at?: string },
  lang: string,
): string {
  const raw = d.generated_at || d.date
  if (!raw) return ''
  const dt = new Date(raw)
  if (Number.isNaN(dt.getTime())) return raw
  // Show `<date>, <HH:MM>` — absolute time so user sees the cache age.
  return dt.toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-GB', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Module-level state: survives DailyDigest unmount so navigating away and back
// doesn't restart a background generation or lose already-fetched digests.
const digestMemory = new Map<string, DigestResponse>()
const digestInflight = new Map<string, Promise<DigestResponse>>()
const CACHE_STORAGE_PREFIX = 'm3u_digest_v1:'

function cacheKey(theme: DigestTheme, lang: string): string {
  return `${theme}::${lang}`
}

function loadCachedDigest(theme: DigestTheme, lang: string): DigestResponse | null {
  const mem = digestMemory.get(cacheKey(theme, lang))
  if (mem) return mem
  try {
    const raw = localStorage.getItem(CACHE_STORAGE_PREFIX + cacheKey(theme, lang))
    if (!raw) return null
    const parsed = JSON.parse(raw) as DigestResponse
    digestMemory.set(cacheKey(theme, lang), parsed)
    return parsed
  } catch {
    return null
  }
}

function storeCachedDigest(
  theme: DigestTheme,
  lang: string,
  res: DigestResponse,
): void {
  digestMemory.set(cacheKey(theme, lang), res)
  try {
    localStorage.setItem(
      CACHE_STORAGE_PREFIX + cacheKey(theme, lang),
      JSON.stringify(res),
    )
  } catch {
    /* quota — ignore */
  }
}

const THEMES: { id: DigestTheme; icon: React.ComponentType<{ className?: string }>; accent: string }[] = [
  { id: 'sport', icon: Trophy, accent: 'from-amber-400/40 to-rose-500/30' },
  { id: 'cinema', icon: Film, accent: 'from-indigo-500/40 to-violet-600/30' },
  { id: 'assistant', icon: Sparkles, accent: 'from-sky-400/40 to-indigo-500/30' },
]

interface DailyDigestProps {
  enabled: boolean
  onPlan: (entry: DigestEntry, theme: DigestTheme) => void | Promise<void>
  onRecord: (entry: DigestEntry, theme: DigestTheme) => void | Promise<void>
  onWatch: (entry: DigestEntry) => void
}

export function DailyDigest({ enabled, onPlan, onRecord, onWatch }: DailyDigestProps) {
  const { t, lang } = useI18n()
  const [active, setActive] = useState<DigestTheme>('sport')
  // Seed local state from the module-level cache so a remount after
  // navigating away shows the previously-fetched digests instantly.
  const [cache, setCache] = useState<Partial<Record<DigestTheme, DigestResponse>>>(
    () => {
      const initial: Partial<Record<DigestTheme, DigestResponse>> = {}
      for (const th of ['sport', 'cinema', 'assistant'] as const) {
        const hit = loadCachedDigest(th, lang)
        if (hit) initial[th] = hit
      }
      return initial
    },
  )
  const [loading, setLoading] = useState<Partial<Record<DigestTheme, boolean>>>(
    () => {
      // If a generation started before we mounted and is still running, show
      // the spinner on remount too.
      const initial: Partial<Record<DigestTheme, boolean>> = {}
      for (const th of ['sport', 'cinema', 'assistant'] as const) {
        if (digestInflight.has(cacheKey(th, lang))) initial[th] = true
      }
      return initial
    },
  )
  const [error, setError] = useState<string | null>(null)

  const fetchDigest = useCallback(
    async (theme: DigestTheme, refresh = false) => {

      if (!enabled) return
      const key = cacheKey(theme, lang)

      // Dedupe across unmounts: if another call is already in flight for
      // this (theme, lang), piggy-back on it instead of firing a duplicate
      // OpenAI generation.
      let promise = refresh ? undefined : digestInflight.get(key)
      if (!promise) {
        promise = api.getDigest(theme, lang, refresh)
        digestInflight.set(key, promise)
        promise
          .then((res) => storeCachedDigest(theme, lang, res))
          .catch(() => {})
          .finally(() => {
            if (digestInflight.get(key) === promise) digestInflight.delete(key)
          })
      }

      setLoading((l) => ({ ...l, [theme]: true }))
      setError(null)
      try {
        const res = await promise
        setCache((c) => ({ ...c, [theme]: res }))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading((l) => ({ ...l, [theme]: false }))
      }
    },
    [enabled, lang],
  )

  useEffect(() => {
    // Always call fetchDigest when content is missing — its internal dedupe
    // piggy-backs on any in-flight request from a previous mount instead of
    // launching a new one.
    if (enabled && !cache[active]) {
      void fetchDigest(active)
    }
  }, [active, cache, enabled, fetchDigest])

  const digest = cache[active]
  const isLoading = Boolean(loading[active])
  const activeTheme = THEMES.find((th) => th.id === active) ?? THEMES[0]

  if (!enabled) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-1 items-center justify-center px-6 py-12">
        <div className="glass rounded-3xl border-white/10 p-8 text-center">
          <Sunrise className="mx-auto mb-3 h-8 w-8 text-[var(--color-indigo-primary)]" />
          <p className="text-sm text-fog-200/80">{t('ai_disabled')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.28em] text-fog-200/50">
            <Sunrise className="h-3 w-3" />
            {t('section_today')}
          </div>
          <h1 className="mt-1 text-[clamp(1.6rem,1.2rem+1.4vw,2.6rem)] font-semibold leading-[1.05] tracking-tight text-white">
            {t('digest_title')}
          </h1>
          <p className="mt-1 text-[13px] text-fog-200/60">{t('digest_subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="text-[11px] text-fog-200/60">
            {digest?.cached === false && t('digest_fresh')}
            {digest?.cached && `${t('digest_cached')}${formatGeneratedAt(digest, lang)}`}
          </div>
          <button
            type="button"
            onClick={() => void fetchDigest(active, true)}
            disabled={isLoading}
            className="glass flex items-center gap-1.5 rounded-full border-white/10 px-3 py-1.5 text-[12px] text-fog-200/80 transition hover:text-white disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isLoading && 'animate-spin')} />
            {t('digest_refresh')}
          </button>
        </div>
      </div>

      {/* Theme tabs */}
      <div className="mb-5 flex flex-wrap gap-2">
        {THEMES.map((th) => {
          const Icon = th.icon
          const isActive = active === th.id
          return (
            <button
              key={th.id}
              type="button"
              onClick={() => setActive(th.id)}
              className={cn(
                'relative flex items-center gap-2 overflow-hidden rounded-full border px-4 py-2 text-[12px] font-medium tracking-tight transition',
                isActive
                  ? 'border-white/20 text-white'
                  : 'border-white/10 bg-white/[0.02] text-fog-200/60 hover:text-white',
              )}
            >
              {isActive && (
                <motion.span
                  layoutId="digest-theme-active"
                  className={cn('absolute inset-0 bg-gradient-to-br', th.accent)}
                  transition={{ type: 'spring', stiffness: 280, damping: 28 }}
                />
              )}
              <Icon className="relative h-3.5 w-3.5" />
              <span className="relative">{t(`digest_theme_${th.id}`)}</span>
            </button>
          )
        })}
      </div>

      {error && (
        <div className="mb-4 rounded-2xl border border-[var(--color-rose-primary)]/30 bg-[var(--color-rose-primary)]/[0.08] px-4 py-3 text-[13px] text-[var(--color-rose-primary)]">
          {error}
        </div>
      )}

      {/* Cards grid */}
      <div className="flex-1">
        <AnimatePresence mode="wait">
          {isLoading && !digest ? (
            <LoadingGrid key="loading" accent={activeTheme.accent} />
          ) : digest && digest.items.length > 0 ? (
            <DigestGrid
              key={active}
              items={digest.items}
              accent={activeTheme.accent}
              onPlan={(e) => onPlan(e, active)}
              onRecord={(e) => onRecord(e, active)}
              onWatch={onWatch}
            />
          ) : digest ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="glass rounded-3xl border-white/10 px-6 py-14 text-center text-[13px] text-fog-200/60"
            >
              {t('digest_empty')}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  )
}

function DigestGrid({
  items,
  accent,
  onPlan,
  onRecord,
  onWatch,
}: {
  items: DigestEntry[]
  accent: string
  onPlan: (entry: DigestEntry) => void | Promise<void>
  onRecord: (entry: DigestEntry) => void | Promise<void>
  onWatch: (entry: DigestEntry) => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
    >
      {items.map((entry, index) => (
        <DigestCard
          key={`${entry.channel_id}-${entry.start}`}
          entry={entry}
          accent={accent}
          index={index}
          onPlan={() => onPlan(entry)}
          onRecord={() => onRecord(entry)}
          onWatch={() => onWatch(entry)}
        />
      ))}
    </motion.div>
  )
}

function DigestCard({
  entry,
  accent,
  index,
  onPlan,
  onRecord,
  onWatch,
}: {
  entry: DigestEntry
  accent: string
  index: number
  onPlan: () => void | Promise<void>
  onRecord: () => void | Promise<void>
  onWatch: () => void
}) {
  const { lang, t } = useI18n()
  const start = useMemo(() => new Date(entry.start), [entry.start])
  const stop = useMemo(() => new Date(entry.stop), [entry.stop])
  const when = start.toLocaleTimeString(lang === 'ru' ? 'ru-RU' : 'en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })
  const durMin = Math.max(1, Math.round((stop.getTime() - start.getTime()) / 60000))
  const isFeatured = index === 0
  // Poster lookup: try the model-picked English keywords first; if TMDB
  // comes back empty (common for esoteric Russian titles or CamelCase
  // transliterations) the backend retries with the native programme title
  // via Wikipedia.
  const poster = usePoster(entry.poster_keywords || entry.title, lang, entry.title)
  const posterUrl = poster?.url ?? null
  const now = useNow(30_000)
  const countdown = formatCountdown(entry.start, now, lang)
  // Programme phase drives which actions the card offers:
  // - upcoming : start > now          → Plan + Record
  // - live     : start <= now < stop  → Watch + Record
  // - ended    : now >= stop          → "Уже прошло" badge, no actions
  const nowMs = now.getTime()
  const phase: 'upcoming' | 'live' | 'ended' =
    nowMs >= stop.getTime() ? 'ended' : nowMs >= start.getTime() ? 'live' : 'upcoming'
  const [planState, setPlanState] = useState<'idle' | 'saving' | 'done'>('idle')
  const [recordState, setRecordState] = useState<'idle' | 'saving' | 'done'>('idle')
  const handlePlanClick = async () => {
    if (planState !== 'idle') return
    setPlanState('saving')
    try {
      await onPlan()
      setPlanState('done')
    } catch {
      setPlanState('idle')
    }
  }
  const handleRecordClick = async () => {
    if (recordState !== 'idle') return
    setRecordState('saving')
    try {
      await onRecord()
      setRecordState('done')
    } catch {
      setRecordState('idle')
    }
  }

  return (
    <motion.article
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.04 }}
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] transition hover:border-white/20',
        isFeatured
          ? 'min-h-[320px] sm:col-span-2 lg:row-span-2'
          : 'min-h-[260px]',
      )}
    >
      {/* Background: poster image if available, else blurred channel logo */}
      <div className="absolute inset-0">
        <div
          className={cn('absolute inset-0 bg-gradient-to-br opacity-80', accent)}
        />
        {posterUrl ? (
          <img
            src={posterUrl}
            alt=""
            aria-hidden
            className="absolute inset-0 h-full w-full scale-[1.02] object-cover opacity-70 transition-opacity duration-500"
            loading="lazy"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        ) : (
          <img
            src={api.logoUrl(entry.channel_id)}
            alt=""
            aria-hidden
            className="absolute inset-0 h-full w-full scale-150 object-contain opacity-30 blur-2xl"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-black/20" />
      </div>

      <div className="relative flex flex-1 flex-col gap-3 p-5">
        {/* Top: channel chip + time */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 rounded-full border border-white/15 bg-black/40 px-2 py-1 text-[11px] text-white/80 backdrop-blur-sm">
            <img
              src={api.logoUrl(entry.channel_id)}
              alt=""
              aria-hidden
              className="h-4 w-4 shrink-0 rounded-sm object-contain"
              onError={(e) => {
                ;(e.currentTarget as HTMLImageElement).style.display = 'none'
              }}
            />
            <span className="max-w-[160px] truncate">{entry.channel_name}</span>
          </div>
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-1 rounded-full border border-white/10 bg-black/40 px-2 py-1 text-[11px] text-white/70 backdrop-blur-sm">
              <Clock className="h-3 w-3" />
              {when} · {durMin}
              {lang === 'ru' ? 'м' : 'm'}
            </div>
            {countdown && (
              <div className="rounded-full border border-[var(--color-indigo-primary)]/40 bg-black/40 px-2 py-0.5 text-[10.5px] font-medium tracking-tight text-[var(--color-indigo-primary)] backdrop-blur-sm">
                {countdown}
              </div>
            )}
          </div>
        </div>

        {/* Title + blurb */}
        <div className="flex-1">
          <h3
            className={cn(
              'font-semibold leading-tight tracking-tight text-white',
              isFeatured
                ? 'text-[clamp(1.3rem,1rem+0.8vw,2rem)]'
                : 'text-[clamp(1.05rem,0.95rem+0.2vw,1.25rem)]',
            )}
          >
            {entry.title}
          </h3>
          {entry.blurb && (
            <p
              className={cn(
                'mt-2 leading-relaxed text-white/75',
                isFeatured ? 'text-[14px]' : 'text-[12.5px]',
              )}
            >
              {entry.blurb}
            </p>
          )}
        </div>

        {/* Actions — phase-aware */}
        <div className="flex items-center gap-2 pt-2">
          {phase === 'ended' ? (
            <div className="flex items-center gap-1.5 rounded-full border border-white/10 bg-black/30 px-3 py-1.5 text-[12px] text-white/50 backdrop-blur-sm">
              <CheckCircle2 className="h-3.5 w-3.5" />
              {t('digest_aired')}
            </div>
          ) : phase === 'live' ? (
            <>
              <button
                type="button"
                onClick={onWatch}
                className="flex items-center gap-1.5 rounded-full bg-[var(--color-rose-primary)] px-3 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110"
              >
                <PlayCircle className="h-3.5 w-3.5" />
                {t('digest_watch_now')}
              </button>
              <button
                type="button"
                onClick={handleRecordClick}
                disabled={recordState !== 'idle'}
                aria-live="polite"
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] backdrop-blur-sm transition',
                  recordState === 'done'
                    ? 'border border-[var(--color-cyan-primary)]/50 bg-[var(--color-cyan-primary)]/[0.15] text-[var(--color-cyan-primary)]'
                    : recordState === 'saving'
                      ? 'border border-white/15 bg-black/30 text-white/60'
                      : 'border border-white/20 bg-black/30 text-white/90 hover:border-white/40 hover:text-white',
                )}
              >
                {recordState === 'saving' ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : recordState === 'done' ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                ) : (
                  <Video className="h-3.5 w-3.5" />
                )}
                {recordState === 'done' ? t('archive_queued') : t('digest_record')}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={handlePlanClick}
                disabled={planState !== 'idle'}
                aria-live="polite"
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium transition',
                  planState === 'done'
                    ? 'border border-[var(--color-cyan-primary)]/50 bg-[var(--color-cyan-primary)]/[0.15] text-[var(--color-cyan-primary)]'
                    : planState === 'saving'
                      ? 'bg-white/60 text-black/70'
                      : 'bg-white/90 text-black hover:bg-white',
                )}
              >
                {planState === 'saving' ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : planState === 'done' ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                ) : (
                  <BookmarkCheck className="h-3.5 w-3.5" />
                )}
                {planState === 'done' ? t('plans_status_scheduled') : t('digest_watch')}
              </button>
              <button
                type="button"
                onClick={handleRecordClick}
                disabled={recordState !== 'idle'}
                aria-live="polite"
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] backdrop-blur-sm transition',
                  recordState === 'done'
                    ? 'border border-[var(--color-cyan-primary)]/50 bg-[var(--color-cyan-primary)]/[0.15] text-[var(--color-cyan-primary)]'
                    : recordState === 'saving'
                      ? 'border border-white/15 bg-black/30 text-white/60'
                      : 'border border-white/20 bg-black/30 text-white/90 hover:border-white/40 hover:text-white',
                )}
              >
                {recordState === 'saving' ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : recordState === 'done' ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                ) : (
                  <Video className="h-3.5 w-3.5" />
                )}
                {recordState === 'done' ? t('archive_queued') : t('digest_record')}
              </button>
            </>
          )}
        </div>
      </div>
    </motion.article>
  )
}

function LoadingGrid({ accent }: { accent: string }) {
  const { t } = useI18n()
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <motion.div
          key={i}
          animate={{ opacity: [0.4, 0.8, 0.4] }}
          transition={{ duration: 1.6, repeat: Infinity, delay: i * 0.08 }}
          className="relative flex h-[220px] flex-col justify-end overflow-hidden rounded-3xl border border-white/10"
        >
          <div className={cn('absolute inset-0 bg-gradient-to-br opacity-60', accent)} />
          <div className="relative p-5">
            <div className="h-3 w-24 rounded-full bg-white/10" />
            <div className="mt-3 h-5 w-3/4 rounded-full bg-white/15" />
            <div className="mt-2 h-4 w-full rounded-full bg-white/10" />
            <div className="mt-1 h-4 w-2/3 rounded-full bg-white/10" />
          </div>
        </motion.div>
      ))}
      <div className="col-span-full flex items-center justify-center gap-2 py-4 text-[12px] text-fog-200/60">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('digest_refresh')}…
      </div>
    </motion.div>
  )
}
