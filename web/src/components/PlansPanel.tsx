import { AnimatePresence, motion } from 'framer-motion'
import {
  BookmarkCheck,
  CheckCircle2,
  Clock,
  Loader2,
  PlayCircle,
  Radio,
  Send,
  Trash2,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useI18n } from '../lib/i18n'
import { useNow, formatCountdown } from '../hooks/useNow'
import { cn } from '../lib/cn'
import type { Plan, PlanStatus, PlansStatusResponse } from '../types'

interface PlansPanelProps {
  onPlay: (channelId: string) => void
}

export function PlansPanel({ onPlay }: PlansPanelProps) {
  const { t, lang } = useI18n()
  const [plans, setPlans] = useState<Plan[]>([])
  const [status, setStatus] = useState<PlansStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const now = useNow(30_000)

  const reload = useCallback(async () => {
    try {
      const [list, st] = await Promise.all([api.listPlans(), api.plansStatus()])
      setPlans(list.items)
      setStatus(st)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
    // Poll every 15 s so status changes (live → done) surface without a manual refresh.
    const id = setInterval(reload, 15_000)
    return () => clearInterval(id)
  }, [reload])

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await api.deletePlan(id)
        setPlans((prev) => prev.filter((p) => p.id !== id))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [],
  )

  const handleCancel = useCallback(
    async (id: string) => {
      try {
        await api.cancelPlan(id)
        await reload()
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [reload],
  )

  const handleTest = useCallback(async () => {
    setTesting(true)
    setError(null)
    try {
      await api.testTelegram()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setTesting(false)
    }
  }, [])

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 pb-8 pt-6 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3 pb-6">
        <div>
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.28em] text-fog-200/50">
            <BookmarkCheck className="h-3 w-3" />
            {t('section_plans')}
          </div>
          <h1 className="mt-1 text-[clamp(1.6rem,1.2rem+1.4vw,2.4rem)] font-semibold leading-[1.05] tracking-tight text-white">
            {t('plans_title')}
          </h1>
          <p className="mt-1 text-[13px] text-fog-200/60">{t('plans_subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <TelegramBadge
            enabled={status?.telegram_enabled ?? false}
            label={status?.telegram_enabled ? t('plans_notify_sent') : t('plans_notify_fail')}
          />
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !status?.telegram_enabled}
            className="glass flex items-center gap-1.5 rounded-full border-white/10 px-3 py-1.5 text-[12px] text-fog-200/80 transition hover:text-white disabled:opacity-40"
          >
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            {t('plans_test')}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-2xl border border-[var(--color-rose-primary)]/30 bg-[var(--color-rose-primary)]/[0.08] px-4 py-3 text-[13px] text-[var(--color-rose-primary)]">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-fog-200/60">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : plans.length === 0 ? (
        <div className="glass flex flex-1 items-center justify-center rounded-3xl border-white/10 py-16 text-[13px] text-fog-200/60">
          {t('plans_empty')}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <AnimatePresence initial={false}>
            {plans.map((plan, i) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                index={i}
                now={now}
                lang={lang}
                onPlay={() => onPlay(plan.channel_id)}
                onCancel={() => handleCancel(plan.id)}
                onDelete={() => handleDelete(plan.id)}
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

function TelegramBadge({ enabled, label }: { enabled: boolean; label: string }) {
  return (
    <div
      className={cn(
        'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]',
        enabled
          ? 'border-[var(--color-cyan-primary)]/40 bg-[var(--color-cyan-primary)]/[0.08] text-[var(--color-cyan-primary)]'
          : 'border-[var(--color-rose-primary)]/40 bg-[var(--color-rose-primary)]/[0.08] text-[var(--color-rose-primary)]',
      )}
    >
      <span
        className={cn('h-1.5 w-1.5 rounded-full', enabled ? 'bg-current' : 'bg-current opacity-70')}
      />
      {label}
    </div>
  )
}

interface PlanCardProps {
  plan: Plan
  index: number
  now: Date
  lang: string
  onPlay: () => void
  onCancel: () => void
  onDelete: () => void
}

function PlanCard({ plan, index, now, lang, onPlay, onCancel, onDelete }: PlanCardProps) {
  const { t } = useI18n()
  const start = new Date(plan.start)
  const startLabel = start.toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-GB', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
  const countdown = formatCountdown(plan.start, now, lang)
  const isLive = plan.status === 'live_notified'
  const isDone = plan.status === 'done' || plan.status === 'missed'
  const isCancelled = plan.status === 'cancelled'
  const canCancel = !isCancelled && !isDone

  return (
    <motion.article
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.3, delay: Math.min(index * 0.03, 0.2) }}
      className={cn(
        'group relative flex min-h-[240px] flex-col overflow-hidden rounded-3xl border transition',
        isLive
          ? 'border-[var(--color-rose-primary)]/40'
          : 'border-white/10 hover:border-white/25',
        (isCancelled || isDone) && 'opacity-60',
      )}
    >
      {/* Background: poster as hero, with gradient readability layer */}
      <div className="absolute inset-0">
        <div
          className={cn(
            'absolute inset-0 bg-gradient-to-br opacity-80',
            isLive
              ? 'from-rose-500/35 to-amber-400/25'
              : 'from-indigo-500/30 to-slate-700/30',
          )}
        />
        {plan.poster_url ? (
          <img
            src={plan.poster_url}
            alt=""
            aria-hidden
            loading="lazy"
            className="absolute inset-0 h-full w-full scale-[1.02] object-cover opacity-70"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        ) : (
          <img
            src={api.logoUrl(plan.channel_id)}
            alt=""
            aria-hidden
            loading="lazy"
            className="absolute inset-0 h-full w-full scale-150 object-contain opacity-30 blur-2xl"
            onError={(e) => {
              ;(e.currentTarget as HTMLImageElement).style.display = 'none'
            }}
          />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/45 to-black/20" />
      </div>

      <div className="relative flex flex-1 flex-col justify-between gap-3 p-4">
        {/* Top: status + channel + time */}
        <div className="flex items-start justify-between gap-2">
          <StatusChip status={plan.status} />
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-1 rounded-full border border-white/15 bg-black/40 px-2 py-1 text-[10.5px] text-white/80 backdrop-blur-sm">
              <Clock className="h-3 w-3" />
              {startLabel}
            </div>
            {countdown && !isDone && !isCancelled && (
              <div className="rounded-full border border-[var(--color-indigo-primary)]/40 bg-black/40 px-2 py-0.5 text-[10px] font-medium text-[var(--color-indigo-primary)] backdrop-blur-sm">
                {countdown}
              </div>
            )}
          </div>
        </div>

        {/* Main: channel + title + blurb */}
        <div className="flex flex-1 flex-col justify-end">
          <div className="mb-1.5 flex items-center gap-1.5">
            <img
              src={api.logoUrl(plan.channel_id)}
              alt=""
              aria-hidden
              loading="lazy"
              className="h-4 w-4 shrink-0 rounded-sm bg-black/40 object-contain p-[1px]"
              onError={(e) => {
                ;(e.currentTarget as HTMLImageElement).style.display = 'none'
              }}
            />
            <span className="truncate text-[11px] uppercase tracking-[0.12em] text-white/70">
              {plan.channel_name}
            </span>
          </div>
          <h3 className="line-clamp-2 text-[15px] font-semibold leading-tight text-white">
            {plan.title}
          </h3>
          {plan.blurb && (
            <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-snug text-white/75">
              {plan.blurb}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          {isLive && (
            <button
              type="button"
              onClick={onPlay}
              className="flex items-center gap-1 rounded-full bg-white/95 px-2.5 py-1 text-[11px] font-medium text-black transition hover:bg-white"
            >
              <PlayCircle className="h-3.5 w-3.5" />
              {t('return_to_live')}
            </button>
          )}
          {canCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="flex items-center gap-1 rounded-full border border-white/20 bg-black/30 px-2.5 py-1 text-[11px] text-white/85 backdrop-blur-sm transition hover:border-white/40 hover:text-white"
            >
              <XCircle className="h-3 w-3" />
              {t('plans_cancel')}
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            title={t('plans_delete')}
            className="ml-auto flex h-6 w-6 items-center justify-center rounded-full border border-white/15 bg-black/30 text-white/70 backdrop-blur-sm transition hover:border-[var(--color-rose-primary)]/40 hover:text-[var(--color-rose-primary)]"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>
    </motion.article>
  )
}

function StatusChip({ status }: { status: PlanStatus }) {
  const { t } = useI18n()
  const cfg: Record<PlanStatus, { icon: React.ComponentType<{ className?: string }>; cls: string; label: string }> = {
    scheduled: {
      icon: BookmarkCheck,
      cls: 'border-white/15 bg-white/[0.04] text-white/75',
      label: t('plans_status_scheduled'),
    },
    live_notified: {
      icon: Radio,
      cls: 'border-[var(--color-rose-primary)]/40 bg-[var(--color-rose-primary)]/[0.1] text-[var(--color-rose-primary)]',
      label: t('plans_status_live'),
    },
    done: {
      icon: CheckCircle2,
      cls: 'border-[var(--color-cyan-primary)]/40 bg-[var(--color-cyan-primary)]/[0.08] text-[var(--color-cyan-primary)]',
      label: t('plans_status_done'),
    },
    cancelled: {
      icon: XCircle,
      cls: 'border-white/15 bg-white/[0.04] text-white/50',
      label: t('plans_status_cancelled'),
    },
    missed: {
      icon: XCircle,
      cls: 'border-white/15 bg-white/[0.04] text-white/50',
      label: t('plans_status_missed'),
    },
  }
  const c = cfg[status] ?? cfg.scheduled
  const Icon = c.icon
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 normal-case tracking-normal',
        c.cls,
      )}
    >
      <Icon className="h-3 w-3" />
      {c.label}
    </span>
  )
}
