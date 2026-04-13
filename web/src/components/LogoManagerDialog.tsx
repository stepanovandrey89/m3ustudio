import { motion } from 'framer-motion'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Image,
  Link,
  Loader2,
  RefreshCw,
  Search,
  SkipForward,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type LogoRegistryItem, type LogoRegistryResponse } from '../lib/api'
import { cn } from '../lib/cn'
import { useI18n } from '../lib/i18n'
import { ChannelLogo } from './ChannelLogo'

interface LogoManagerDialogProps {
  onClose: () => void
}

const PER_PAGE = 40

const STATUS_KEYS: Record<string, string> = {
  '': 'all',
  found: 'found',
  missing: 'missing',
  pending: 'pending',
  skipped: 'skipped',
}

export function LogoManagerDialog({ onClose }: LogoManagerDialogProps) {
  const { t } = useI18n()
  const [data, setData] = useState<LogoRegistryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [retryingAll, setRetryingAll] = useState(false)
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [overrideId, setOverrideId] = useState<string | null>(null)
  const [overrideUrl, setOverrideUrl] = useState('')

  const fetchData = useCallback(async (p: number, q: string, s: string) => {
    setLoading(true)
    try {
      const res = await api.getLogoRegistry(p, PER_PAGE, q, s)
      setData(res)
    } catch { /* network error */ }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData(page, query, statusFilter)
  }, [page, query, statusFilter, fetchData])

  const handleRetryAll = async () => {
    setRetryingAll(true)
    try {
      await api.retryAllLogos()
      await fetchData(page, query, statusFilter)
    } catch { /* */ }
    setRetryingAll(false)
  }

  const handleRetry = async (id: string) => {
    setRetryingId(id)
    try {
      await api.retryLogo(id)
      await fetchData(page, query, statusFilter)
    } catch { /* */ }
    setRetryingId(null)
  }

  const handleSkip = async (id: string) => {
    try {
      await api.skipLogo(id)
      await fetchData(page, query, statusFilter)
    } catch { /* */ }
  }

  const handleOverride = async (id: string) => {
    if (!overrideUrl.trim()) return
    try {
      await api.overrideLogo(id, overrideUrl.trim())
      setOverrideId(null)
      setOverrideUrl('')
      await fetchData(page, query, statusFilter)
    } catch (err) {
      alert(`Error: ${err}`)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 12 }}
        transition={{ duration: 0.18 }}
        className="glass flex h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
          <div className="flex items-center gap-3">
            <Image className="h-4 w-4 text-[var(--color-indigo-primary)]" />
            <div>
              <h2 className="text-[15px] font-semibold text-white">{t('channel_logos')}</h2>
              {data && (
                <p className="mt-0.5 font-mono text-[11px] text-fog-100/50">
                  <span className="text-[var(--color-green-primary)]">{data.found}</span> {t('found')}
                  {' · '}
                  <span className="text-[var(--color-rose-primary)]">{data.missing}</span> {t('missing')}
                  {' · '}
                  <span className="text-[var(--color-amber-primary)]">{data.pending}</span> {t('pending')}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRetryAll}
              disabled={retryingAll}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] font-medium text-fog-200 transition hover:bg-white/10"
            >
              <RefreshCw className={cn('h-3 w-3', retryingAll && 'animate-spin')} />
              {t('retry_failed')}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="flex h-7 w-7 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-fog-100/60 transition hover:bg-white/10 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Search + filter */}
        <div className="flex items-center gap-3 border-b border-white/5 px-5 py-3">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fog-100/40" />
            <input
              type="search"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1) }}
              placeholder={t('search_channels')}
              className={cn(
                'w-full rounded-lg border border-white/10 bg-white/[0.03] py-2 pl-9 pr-3 text-[13px] text-white',
                'placeholder:text-fog-100/30',
                'focus:border-[var(--color-indigo-primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--color-indigo-primary)]/20',
              )}
            />
          </div>
          <div className="flex shrink-0 gap-1">
            {Object.entries(STATUS_KEYS).map(([val, key]) => (
              <button
                key={val}
                type="button"
                onClick={() => { setStatusFilter(val); setPage(1) }}
                className={cn(
                  'rounded-md px-2.5 py-1.5 text-[11px] font-medium transition',
                  statusFilter === val
                    ? 'bg-[var(--color-indigo-primary)]/20 text-[var(--color-indigo-primary)]'
                    : 'text-fog-100/50 hover:bg-white/5 hover:text-fog-200',
                )}
              >
                {t(key)}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
          {loading && !data ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-fog-100/40" />
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-fog-100/40">
              <Image className="h-8 w-8" />
              <p className="text-sm">{t('nothing_found')}</p>
            </div>
          ) : (
            <ul className="divide-y divide-white/[0.04]">
              {data.items.map((item) => (
                <LogoRow
                  key={item.id}
                  item={item}
                  retrying={retryingId === item.id}
                  showOverride={overrideId === item.id}
                  overrideUrl={overrideId === item.id ? overrideUrl : ''}
                  onRetry={() => handleRetry(item.id)}
                  onSkip={() => handleSkip(item.id)}
                  onToggleOverride={() => {
                    setOverrideId(overrideId === item.id ? null : item.id)
                    setOverrideUrl('')
                  }}
                  onOverrideUrlChange={setOverrideUrl}
                  onOverrideSubmit={() => handleOverride(item.id)}
                />
              ))}
            </ul>
          )}
        </div>

        {/* Pagination */}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between border-t border-white/5 px-5 py-3">
            <span className="font-mono text-[11px] text-fog-100/50">
              {data.total} channels · page {data.page}/{data.pages}
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 text-fog-100/60 transition hover:bg-white/10 disabled:opacity-30"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
                disabled={page >= data.pages}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 text-fog-100/60 transition hover:bg-white/10 disabled:opacity-30"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}

interface LogoRowProps {
  item: LogoRegistryItem
  retrying: boolean
  showOverride: boolean
  overrideUrl: string
  onRetry: () => void
  onSkip: () => void
  onToggleOverride: () => void
  onOverrideUrlChange: (url: string) => void
  onOverrideSubmit: () => void
}

function LogoRow({
  item,
  retrying,
  showOverride,
  overrideUrl,
  onRetry,
  onSkip,
  onToggleOverride,
  onOverrideUrlChange,
  onOverrideSubmit,
}: LogoRowProps) {
  const { t } = useI18n()
  return (
    <li className="px-5 py-2.5">
      <div className="flex items-center gap-3">
        <ChannelLogo id={item.id} name={item.name} hasLogo={item.cached} size={32} />

        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-white">{item.name}</p>
          <div className="mt-0.5 flex items-center gap-2 text-[10px]">
            <StatusBadge status={item.status} />
            {item.source && (
              <span className="text-fog-100/40">{item.source}</span>
            )}
            {item.attempts > 0 && (
              <span className="text-fog-100/30">
                {item.attempts}/5 {t('tries')}
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onToggleOverride}
            className="flex h-7 w-7 items-center justify-center rounded-md text-fog-100/30 transition hover:bg-white/10 hover:text-fog-100"
            title={t('set_logo_url')}
          >
            <Link className="h-3 w-3" />
          </button>
          {item.status !== 'found' && item.status !== 'skipped' && (
            <button
              type="button"
              onClick={onSkip}
              className="flex h-7 w-7 items-center justify-center rounded-md text-fog-100/30 transition hover:bg-white/10 hover:text-fog-100"
              title={t('skip')}
            >
              <SkipForward className="h-3 w-3" />
            </button>
          )}
          {(item.status === 'missing' || item.status === 'pending' || item.status === 'skipped') && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="flex h-7 w-7 items-center justify-center rounded-md text-fog-100/30 transition hover:bg-white/10 hover:text-fog-100"
              title={t('retry')}
            >
              <RefreshCw className={cn('h-3 w-3', retrying && 'animate-spin')} />
            </button>
          )}
        </div>
      </div>

      {showOverride && (
        <div className="mt-2 flex items-center gap-2">
          <input
            type="url"
            value={overrideUrl}
            onChange={(e) => onOverrideUrlChange(e.target.value)}
            placeholder="https://example.com/logo.png"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-[12px] text-white placeholder:text-fog-100/30 focus:border-[var(--color-indigo-primary)]/60 focus:outline-none"
            onKeyDown={(e) => { if (e.key === 'Enter') onOverrideSubmit() }}
          />
          <button
            type="button"
            onClick={onOverrideSubmit}
            disabled={!overrideUrl.trim()}
            className="rounded-lg bg-[var(--color-indigo-primary)]/20 px-3 py-1.5 text-[11px] font-medium text-[var(--color-indigo-primary)] transition hover:bg-[var(--color-indigo-primary)]/30 disabled:opacity-40"
          >
            {t('save')}
          </button>
        </div>
      )}
    </li>
  )
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'found') {
    return (
      <span className="flex items-center gap-1 text-[var(--color-green-primary)]">
        <CheckCircle2 className="h-2.5 w-2.5" />
        found
      </span>
    )
  }
  if (status === 'missing') {
    return (
      <span className="flex items-center gap-1 text-[var(--color-rose-primary)]">
        <AlertTriangle className="h-2.5 w-2.5" />
        missing
      </span>
    )
  }
  if (status === 'skipped') {
    return (
      <span className="flex items-center gap-1 text-fog-100/40">
        <Ban className="h-2.5 w-2.5" />
        skipped
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-[var(--color-amber-primary)]">
      <Clock className="h-2.5 w-2.5" />
      pending
    </span>
  )
}
