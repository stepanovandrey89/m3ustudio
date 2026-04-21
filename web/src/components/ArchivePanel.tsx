import { AnimatePresence, motion } from 'framer-motion'
import {
  Archive as ArchiveIcon,
  CircleAlert,
  Download,
  Film,
  Loader2,
  Pause,
  Play,
  PlayCircle,
  Sparkles,
  Trash2,
  Trophy,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { useNow } from '../hooks/useNow'
import { cn } from '../lib/cn'
import type { DigestTheme, Recording } from '../types'

// Visual status used by the card/pill — adds an explicit "finalising" state
// on top of the server-reported status so the chip stops pulsing "Recording…"
// the moment the scheduled stop time passes. The poll loop picks up the real
// server status (done/failed) within a few seconds after that.
type VisualStatus = Recording['status'] | 'finalising'

// ms past `rec.stop` after which we stop trusting the "running"/"queued"
// badge. Server's wall-clock watchdog terminates ffmpeg within ~10s of
// stop_dt; this grace window needs to be a touch longer than that so we
// don't flip the pill to "finalising" before the server has had time to
// mark the recording done on its own.
const STATUS_CLOCK_GRACE_MS = 15_000

function effectiveStatus(rec: Recording, now: Date): VisualStatus {
  if (rec.status !== 'running' && rec.status !== 'queued') return rec.status
  if (!rec.stop) return rec.status
  const stopMs = new Date(rec.stop).getTime()
  if (!Number.isFinite(stopMs)) return rec.status
  if (now.getTime() >= stopMs + STATUS_CLOCK_GRACE_MS) return 'finalising'
  return rec.status
}

type ThemeFilter = 'all' | DigestTheme

const THEME_ICONS: Record<DigestTheme, React.ComponentType<{ className?: string }>> = {
  sport: Trophy,
  cinema: Film,
  assistant: Sparkles,
}

const THEME_ACCENTS: Record<DigestTheme, string> = {
  sport: 'from-amber-400/40 to-rose-500/30',
  cinema: 'from-indigo-500/40 to-violet-600/30',
  assistant: 'from-sky-400/40 to-indigo-500/30',
}

// Legacy records may carry "other" or "news" — remap to "assistant" so they
// surface under the renamed bucket rather than disappearing.
function toTheme(value: string): DigestTheme {
  if (value === 'sport' || value === 'cinema' || value === 'assistant') {
    return value
  }
  return 'assistant'
}

function formatSize(bytes: number, unit: { gb: string; mb: string }): string {
  if (!bytes) return '—'
  if (bytes > 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} ${unit.gb}`
  return `${Math.round(bytes / 1_000_000)} ${unit.mb}`
}

function formatDuration(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return ''
  const total = Math.round(totalSeconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }
  return `${m}:${String(s).padStart(2, '0')}`
}

export function ArchivePanel() {
  const { t, lang } = useI18n()
  const [items, setItems] = useState<Recording[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<ThemeFilter>('all')
  const [playing, setPlaying] = useState<Recording | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await api.listRecordings()
        if (cancelled) return
        setItems(res.items)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    // Poll while there's a running/queued recording so progress shows up live.
    const interval = setInterval(load, 5000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const filtered = useMemo(() => {
    if (filter === 'all') return items
    return items.filter((r) => toTheme(r.theme) === filter)
  }, [filter, items])

  const counts = useMemo(() => {
    const out: Record<ThemeFilter, number> = {
      all: items.length,
      sport: 0,
      cinema: 0,
      assistant: 0,
    }
    for (const r of items) out[toTheme(r.theme)] += 1
    return out
  }, [items])

  async function handleDelete(rec: Recording) {
    const previous = rec
    setItems((prev) => prev.filter((r) => r.id !== rec.id))
    if (playing?.id === rec.id) setPlaying(null)
    try {
      await api.deleteRecording(rec.id)
    } catch (err) {
      // Put the card back where it was — order-stability via created_at sort
      // on the next poll is acceptable.
      setItems((prev) => [previous, ...prev])
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleCancel(rec: Recording) {
    // Cancel on a recording that already captured content (paused with
    // non-zero size, or running with segments on disk) finalises into `done`
    // on the backend — not `failed`. Reflect the right target state right
    // now so we don't flash `failed → paused → done` over 5-10s while the
    // server finishes its ffmpeg concat pass and the next poll fires.
    const hasContent = (rec.parts?.length ?? 0) > 0 && rec.bytes > 0
    const optimistic: Recording['status'] = hasContent ? 'done' : 'failed'
    const previous = rec
    setItems((prev) =>
      prev.map((r) =>
        r.id === rec.id
          ? { ...r, status: optimistic, error: hasContent ? '' : 'cancelled' }
          : r,
      ),
    )
    try {
      await api.cancelRecording(rec.id)
    } catch (err) {
      // Roll back so the card isn't stuck in a fake done/failed state.
      setItems((prev) => prev.map((r) => (r.id === rec.id ? previous : r)))
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handlePause(rec: Recording) {
    const previous = rec
    setItems((prev) =>
      prev.map((r) => (r.id === rec.id ? { ...r, status: 'paused' } : r)),
    )
    try {
      await api.pauseRecording(rec.id)
    } catch (err) {
      setItems((prev) => prev.map((r) => (r.id === rec.id ? previous : r)))
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleResume(rec: Recording) {
    const previous = rec
    setItems((prev) =>
      prev.map((r) => (r.id === rec.id ? { ...r, status: 'running' } : r)),
    )
    try {
      await api.resumeRecording(rec.id)
    } catch (err) {
      setItems((prev) => prev.map((r) => (r.id === rec.id ? previous : r)))
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-6">
      <div className="flex items-end justify-between gap-3 pb-6">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.28em] text-fog-200/50">
            <ArchiveIcon className="h-3 w-3" />
            {t('section_archive')}
          </div>
          <h1 className="mt-1 text-[clamp(1.6rem,1.2rem+1.4vw,2.6rem)] font-semibold leading-[1.05] tracking-tight text-white">
            {t('archive_title')}
          </h1>
          <p className="mt-1 text-[13px] text-fog-200/60">{t('archive_subtitle')}</p>
        </div>
        <div className="text-right text-[11px] uppercase tracking-[0.2em] text-fog-200/40">
          {items.length}
        </div>
      </div>

      {/* Filter chips */}
      <div className="mb-5 flex flex-wrap gap-2">
        {(['all', 'sport', 'cinema', 'assistant'] as const).map((id) => {
          const Icon = id === 'all' ? ArchiveIcon : THEME_ICONS[id]
          const isActive = filter === id
          return (
            <button
              key={id}
              type="button"
              onClick={() => setFilter(id)}
              className={cn(
                'flex items-center gap-2 rounded-full border px-4 py-2 text-[12px] font-medium transition',
                isActive
                  ? 'border-white/25 bg-white/[0.08] text-white'
                  : 'border-white/10 bg-white/[0.02] text-fog-200/60 hover:text-white',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>
                {id === 'all' ? t('all') : t(`digest_theme_${id}`)}
              </span>
              <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10px] tabnum">
                {counts[id]}
              </span>
            </button>
          )
        })}
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-2xl border border-[var(--color-rose-primary)]/30 bg-[var(--color-rose-primary)]/[0.08] px-4 py-3 text-[13px] text-[var(--color-rose-primary)]">
          <CircleAlert className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-fog-200/60">
          <Loader2 className="h-4 w-4 animate-spin" /> …
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass flex flex-1 items-center justify-center rounded-3xl border-white/10 py-16 text-[13px] text-fog-200/60">
          {t('archive_empty')}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((rec, i) => (
            <RecordingCard
              key={rec.id}
              rec={rec}
              index={i}
              onPlay={() => setPlaying(rec)}
              onDelete={() => handleDelete(rec)}
              onCancel={() => handleCancel(rec)}
              onPause={() => handlePause(rec)}
              onResume={() => handleResume(rec)}
            />
          ))}
        </div>
      )}

      <AnimatePresence>
        {playing && (
          <PlayerOverlay
            rec={playing}
            onClose={() => setPlaying(null)}
            sizeLabels={{ gb: t('archive_size_gb'), mb: t('archive_size_mb') }}
            lang={lang}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

function RecordingCard({
  rec,
  index,
  onPlay,
  onDelete,
  onCancel,
  onPause,
  onResume,
}: {
  rec: Recording
  index: number
  onPlay: () => void
  onDelete: () => void
  onCancel: () => void
  onPause: () => void
  onResume: () => void
}) {
  const { t } = useI18n()
  const theme = toTheme(rec.theme)
  const Icon = THEME_ICONS[theme]
  const accent = THEME_ACCENTS[theme]
  // Tick the card once per 10s so `effectiveStatus` can promote a stale
  // "running" badge to "finalising" without waiting for the 5s archive
  // poll. Once the server catches up (wall-clock watchdog fires and flips
  // status to done), the poll overwrites it.
  const now = useNow(10_000)
  const visual = effectiveStatus(rec, now)
  const isPlayable = rec.status === 'done' || rec.status === 'paused'
  const poster = rec.poster_url || ''

  return (
    <motion.article
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.04 }}
      className="group relative flex h-[230px] flex-col overflow-hidden rounded-3xl border border-white/10 bg-white/[0.02] transition hover:border-white/25"
    >
      {/* Background — real poster if available, gradient+logo fallback otherwise */}
      <div className="absolute inset-0">
        <div className={cn('absolute inset-0 bg-gradient-to-br opacity-80', accent)} />
        {poster ? (
          <img
            src={poster}
            alt=""
            aria-hidden
            className="absolute inset-0 h-full w-full object-cover opacity-55"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        ) : (
          <img
            src={api.logoUrl(rec.channel_id)}
            alt=""
            aria-hidden
            className="absolute inset-0 h-full w-full scale-150 object-contain opacity-25 blur-2xl"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-black/10" />
      </div>

      <div className="relative flex flex-1 flex-col justify-between p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 rounded-full border border-white/15 bg-black/40 px-2 py-1 text-[11px] text-white/80 backdrop-blur-sm">
            <Icon className="h-3 w-3" />
            {t(`digest_theme_${theme}`)}
          </div>
          <StatusPill status={visual} />
        </div>

        <div>
          <h3 className="line-clamp-2 text-[15px] font-semibold leading-tight text-white">
            {rec.title}
          </h3>
          <div className="mt-1 text-[11.5px] text-white/60">{rec.channel_name}</div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {rec.status === 'done' && (
              <>
                <button
                  type="button"
                  onClick={onPlay}
                  className="flex items-center gap-1.5 rounded-full bg-white/95 px-3 py-1.5 text-[12px] font-medium text-black transition hover:bg-white"
                >
                  <PlayCircle className="h-3.5 w-3.5" />
                  {t('archive_play')}
                </button>
                <a
                  href={api.recordingDownloadUrl(rec.id)}
                  download
                  className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/90 backdrop-blur-sm transition hover:border-white/40 hover:text-white"
                >
                  <Download className="h-3.5 w-3.5" />
                  {t('archive_download')}
                </a>
                <button
                  type="button"
                  onClick={onDelete}
                  title={t('archive_delete')}
                  className="ml-auto flex h-7 w-7 items-center justify-center rounded-full border border-white/10 text-white/60 transition hover:border-[var(--color-rose-primary)]/40 hover:text-[var(--color-rose-primary)]"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </>
            )}
            {visual === 'running' && (
              <>
                {/* Play what's on disk right now — the MKV ffmpeg is writing
                    into. Browser reads whatever bytes are present at fetch
                    time (growing-file playback), so the user sees the
                    recording-in-progress, not a live stream. */}
                <button
                  type="button"
                  onClick={onPlay}
                  className="flex items-center gap-1.5 rounded-full bg-[var(--color-rose-primary)] px-3 py-1.5 text-[12px] font-medium text-white transition hover:brightness-110"
                >
                  <PlayCircle className="h-3.5 w-3.5" />
                  {t('archive_play')}
                </button>
                <button
                  type="button"
                  onClick={onPause}
                  className="flex items-center gap-1.5 rounded-full border border-white/25 bg-black/40 px-3 py-1.5 text-[12px] text-white/90 backdrop-blur-sm transition hover:bg-black/60 hover:text-white"
                >
                  <Pause className="h-3.5 w-3.5" />
                  {t('archive_pause')}
                </button>
                <button
                  type="button"
                  onClick={onCancel}
                  className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/80 transition hover:border-[var(--color-rose-primary)]/40 hover:text-[var(--color-rose-primary)]"
                >
                  <X className="h-3.5 w-3.5" />
                  {t('archive_cancel')}
                </button>
              </>
            )}
            {visual === 'finalising' && (
              <div className="flex items-center gap-1.5 rounded-full border border-white/15 bg-black/40 px-3 py-1.5 text-[12px] text-white/70 backdrop-blur-sm">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t('archive_finalising')}
              </div>
            )}
            {rec.status === 'paused' && (
              <>
                <button
                  type="button"
                  onClick={onResume}
                  className="flex items-center gap-1.5 rounded-full bg-[var(--color-amber-primary)]/90 px-3 py-1.5 text-[12px] font-medium text-black transition hover:bg-[var(--color-amber-primary)]"
                >
                  <Play className="h-3.5 w-3.5" />
                  {t('archive_resume')}
                </button>
                {isPlayable && (
                  <button
                    type="button"
                    onClick={onPlay}
                    className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/90 backdrop-blur-sm transition hover:border-white/40 hover:text-white"
                  >
                    <PlayCircle className="h-3.5 w-3.5" />
                    {t('archive_play')}
                  </button>
                )}
                <button
                  type="button"
                  onClick={onCancel}
                  className="ml-auto flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/80 transition hover:border-[var(--color-rose-primary)]/40 hover:text-[var(--color-rose-primary)]"
                >
                  <X className="h-3.5 w-3.5" />
                  {t('archive_cancel')}
                </button>
              </>
            )}
            {rec.status === 'queued' && (
              <button
                type="button"
                onClick={onCancel}
                className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/80 transition hover:text-white"
              >
                <X className="h-3.5 w-3.5" />
                {t('archive_cancel')}
              </button>
            )}
            {rec.status === 'failed' && (
              <button
                type="button"
                onClick={onDelete}
                className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/30 px-3 py-1.5 text-[12px] text-white/80 transition hover:text-white"
              >
                <Trash2 className="h-3.5 w-3.5" />
                {t('archive_delete')}
              </button>
            )}
          </div>
        </div>
      </div>
    </motion.article>
  )
}

function StatusPill({ status }: { status: VisualStatus }) {
  const { t } = useI18n()
  if (status === 'running') {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-[var(--color-rose-primary)]/40 bg-black/40 px-2 py-1 text-[11px] text-[var(--color-rose-primary)] backdrop-blur-sm">
        <motion.span
          animate={{ opacity: [0.2, 1, 0.2] }}
          transition={{ duration: 1.2, repeat: Infinity }}
          className="h-2 w-2 rounded-full bg-current"
        />
        {t('archive_running')}
      </div>
    )
  }
  if (status === 'finalising') {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/40 px-2 py-1 text-[11px] text-white/75 backdrop-blur-sm">
        <Loader2 className="h-2.5 w-2.5 animate-spin" />
        {t('archive_finalising')}
      </div>
    )
  }
  if (status === 'paused') {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-[var(--color-amber-primary)]/40 bg-black/40 px-2 py-1 text-[11px] text-[var(--color-amber-primary)] backdrop-blur-sm">
        <Pause className="h-2.5 w-2.5" />
        {t('archive_paused')}
      </div>
    )
  }
  if (status === 'queued') {
    return (
      <div className="rounded-full border border-white/15 bg-black/40 px-2 py-1 text-[11px] text-white/70 backdrop-blur-sm">
        {t('archive_queued')}
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="rounded-full border border-[var(--color-rose-primary)]/40 bg-black/40 px-2 py-1 text-[11px] text-[var(--color-rose-primary)] backdrop-blur-sm">
        {t('archive_failed')}
      </div>
    )
  }
  return (
    <div className="rounded-full border border-[var(--color-cyan-primary)]/40 bg-black/40 px-2 py-1 text-[11px] text-[var(--color-cyan-primary)] backdrop-blur-sm">
      {t('archive_done')}
    </div>
  )
}

function PlayerOverlay({
  rec,
  onClose,
  sizeLabels,
  lang,
}: {
  rec: Recording
  onClose: () => void
  sizeLabels: { gb: string; mb: string }
  lang: string
}) {
  const { t } = useI18n()
  // A single recording may consist of multiple MKV segments (pause/resume or
  // restart hops). We play them sequentially: on `ended`, advance to the next
  // segment; the <video> element is remounted via `key` so the new src loads
  // cleanly without fighting the previous media stream.
  const parts = rec.parts && rec.parts.length > 0 ? rec.parts : [rec.file]
  const [partIdx, setPartIdx] = useState(0)
  const videoRef = useRef<HTMLVideoElement | null>(null)

  useEffect(() => {
    setPartIdx(0)
  }, [rec.id])

  const handleEnded = () => {
    if (partIdx < parts.length - 1) {
      setPartIdx((i) => i + 1)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-xl"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        transition={{ type: 'spring', damping: 26, stiffness: 240 }}
        onClick={(e) => e.stopPropagation()}
        className="relative flex w-full max-w-4xl flex-col gap-3"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-lg font-semibold text-white">{rec.title}</h3>
            <p className="text-[12px] text-fog-200/60">
              {rec.channel_name} ·{' '}
              {new Date(rec.start).toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-GB')} ·{' '}
              {formatSize(rec.bytes, sizeLabels)}
              {rec.duration_seconds && rec.duration_seconds > 0
                ? ` · ${formatDuration(rec.duration_seconds)}`
                : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/20 bg-white/[0.08] text-white/80 hover:bg-white/[0.15] hover:text-white"
            aria-label="close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <video
          key={`${rec.id}-${partIdx}`}
          ref={videoRef}
          src={api.recordingPartUrl(rec.id, partIdx)}
          controls
          autoPlay
          onEnded={handleEnded}
          className="aspect-video w-full rounded-2xl bg-black"
        />
        {parts.length > 1 && (
          <div className="flex items-center justify-between gap-3 text-[11px] text-white/50">
            <span>
              {t('archive_segment_label')} {partIdx + 1} / {parts.length}
            </span>
            <div className="flex gap-1.5">
              {parts.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setPartIdx(i)}
                  className={cn(
                    'h-1.5 w-6 rounded-full transition',
                    i === partIdx
                      ? 'bg-white/80'
                      : i < partIdx
                        ? 'bg-white/30'
                        : 'bg-white/10 hover:bg-white/25',
                  )}
                  aria-label={`Segment ${i + 1}`}
                />
              ))}
            </div>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}
