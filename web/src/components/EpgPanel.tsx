import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { CalendarClock, CircleDot, History, Loader2, Play, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import type { Programme } from '../types'

/**
 * Side panel showing the full-day programme list for a channel. Designed to
 * live as a right-hand column next to the video — fills its container's
 * full height with a sticky header and its own scrollable body.
 */

interface EpgPanelProps {
  channelId: string
  catchupDays: number
  /** Called when the user double-clicks a past (or current) programme to replay it. */
  onPlayProgramme: (programme: Programme) => void
  /** Optional close handler — renders an × in the header that collapses the panel. */
  onClose?: () => void
}

const TICK_INTERVAL_MS = 30_000

export function EpgPanel({ channelId, catchupDays, onPlayProgramme, onClose }: EpgPanelProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['epg', channelId],
    queryFn: () => api.getEpg(channelId),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  })

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), TICK_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [])

  const placeholder = (body: React.ReactNode) => (
    <Shell
      catchupDays={catchupDays}
      count={data?.programmes.length ?? 0}
      onClose={onClose}
    >
      <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-fog-100/60">
        {body}
      </div>
    </Shell>
  )

  if (isLoading) {
    return placeholder(
      <span className="flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Loading guide…
      </span>,
    )
  }

  if (!data) return placeholder(null)

  if (!data.loaded) {
    return placeholder(
      data.loading ? (
        <span className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Preparing guide…
        </span>
      ) : (
        <span className="flex items-center gap-2">
          <CalendarClock className="h-3.5 w-3.5" />
          Guide unavailable
        </span>
      ),
    )
  }

  if (data.programmes.length === 0) {
    return placeholder(
      <span className="flex items-center gap-2 text-fog-100/50">
        <CalendarClock className="h-3.5 w-3.5" />
        No guide available for this channel
      </span>,
    )
  }

  return (
    <Shell catchupDays={catchupDays} count={data.programmes.length} onClose={onClose}>
      <FullDayList
        programmes={data.programmes}
        currentIndex={data.current_index}
        catchupDays={catchupDays}
        nowMs={now}
        onPlayProgramme={onPlayProgramme}
      />
    </Shell>
  )
}

interface ShellProps {
  catchupDays: number
  count: number
  children: React.ReactNode
  onClose?: () => void
}

function Shell({ catchupDays, count, children, onClose }: ShellProps) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-black/20">
      <div className="flex shrink-0 items-center justify-between border-b border-white/5 px-4 py-3">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-fog-100/75">
          <CalendarClock className="h-3 w-3" />
          TV Guide
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-3 font-mono text-[10px] tabnum text-fog-100/50">
            {catchupDays > 0 && (
              <span className="flex items-center gap-1 text-[var(--color-cyan-primary)]/80">
                <History className="h-3 w-3" />
                {catchupDays}d
              </span>
            )}
            {count > 0 && <span>{count}</span>}
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              title="Collapse guide (G)"
              aria-label="Collapse guide"
              className="flex h-7 w-7 items-center justify-center rounded-md text-fog-100/60 transition hover:bg-white/10 hover:text-fog-100"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
      {children}
    </div>
  )
}

interface FullDayListProps {
  programmes: Programme[]
  currentIndex: number | null
  catchupDays: number
  nowMs: number
  onPlayProgramme: (programme: Programme) => void
}

function FullDayList({
  programmes,
  currentIndex,
  catchupDays,
  nowMs,
  onPlayProgramme,
}: FullDayListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const currentRowRef = useRef<HTMLLIElement>(null)

  // Auto-scroll so the current (or nearest future) programme sits near the top.
  useEffect(() => {
    if (!currentRowRef.current || !containerRef.current) return
    const row = currentRowRef.current
    const container = containerRef.current
    container.scrollTo({
      top: Math.max(0, row.offsetTop - container.offsetTop - 6),
      behavior: 'smooth',
    })
  }, [currentIndex, programmes.length])

  const groupedByDay = useMemo(() => groupProgrammesByDay(programmes), [programmes])

  return (
    <div
      ref={containerRef}
      className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto px-3 py-3"
    >
      {groupedByDay.map((group) => (
        <div key={group.label} className="mb-2 last:mb-0">
          <div className="sticky top-0 z-[1] -mx-3 border-b border-white/5 bg-[color:color-mix(in_srgb,var(--color-ink-50)_92%,transparent)] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-fog-100/55 backdrop-blur">
            {group.label}
          </div>
          <ul className="mt-1 flex flex-col gap-0.5">
            {group.items.map(({ programme, index, kind }) => (
              <ProgrammeRow
                key={`${programme.start}-${index}`}
                programme={programme}
                kind={kind}
                catchupDays={catchupDays}
                nowMs={nowMs}
                isCurrent={index === currentIndex}
                onPlay={() => onPlayProgramme(programme)}
                rowRef={index === currentIndex ? currentRowRef : undefined}
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

type ProgrammeKind = 'past' | 'current' | 'future'

interface ProgrammeRowProps {
  programme: Programme
  kind: ProgrammeKind
  catchupDays: number
  nowMs: number
  isCurrent: boolean
  onPlay: () => void
  rowRef?: React.Ref<HTMLLIElement>
}

function ProgrammeRow({
  programme,
  kind,
  catchupDays,
  nowMs,
  isCurrent,
  onPlay,
  rowRef,
}: ProgrammeRowProps) {
  const canReplay = catchupDays > 0 && kind !== 'future'

  const startMs = new Date(programme.start).getTime()
  const stopMs = new Date(programme.stop).getTime()
  const duration = Math.max(1, stopMs - startMs)
  const progress = isCurrent ? ((nowMs - startMs) / duration) * 100 : 0

  const handleClick = () => {
    if (canReplay) onPlay()
  }

  return (
    <motion.li
      ref={rowRef}
      layout="position"
      onDoubleClick={handleClick}
      onClick={handleClick}
      className={cn(
        'group relative flex items-center gap-3 rounded-lg px-3 py-2 transition',
        isCurrent
          ? 'border border-[var(--color-indigo-primary)]/40 bg-[var(--color-indigo-primary)]/10'
          : 'border border-transparent hover:bg-white/[0.04]',
        kind === 'past' && !isCurrent && 'opacity-60',
        canReplay ? 'cursor-pointer' : kind === 'future' ? 'cursor-default' : 'cursor-default',
      )}
      whileHover={canReplay ? { x: 2 } : undefined}
      title={
        canReplay
          ? kind === 'current'
            ? 'Watch from beginning (archive)'
            : 'Play from archive'
          : kind === 'future'
            ? "Hasn't started yet"
            : ''
      }
    >
      <span className="tabnum w-11 shrink-0 text-right font-mono text-[11px] text-fog-100/60">
        {formatTime(programme.start)}
      </span>

      <div className="relative flex h-6 w-6 shrink-0 items-center justify-center">
        {isCurrent ? (
          <CircleDot className="h-4 w-4 animate-pulse text-[var(--color-cyan-primary)]" />
        ) : canReplay ? (
          <span className="flex h-5 w-5 items-center justify-center rounded-full border border-white/10 bg-white/5 opacity-0 transition group-hover:opacity-100">
            <Play className="h-2.5 w-2.5 fill-current text-fog-200" />
          </span>
        ) : (
          <span className="h-1.5 w-1.5 rounded-full bg-fog-100/30" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <p
          className={cn(
            'truncate text-[13px]',
            isCurrent ? 'font-semibold text-white' : 'text-fog-200',
          )}
        >
          {programme.title}
        </p>
        {isCurrent && programme.description && (
          <p className="mt-0.5 line-clamp-2 text-[11px] text-fog-100/70">
            {programme.description}
          </p>
        )}
      </div>

      <span className="tabnum ml-2 shrink-0 font-mono text-[10px] text-fog-100/40">
        {formatDuration(duration)}
      </span>

      {isCurrent && (
        <div className="absolute inset-x-3 bottom-1 h-[2px] overflow-hidden rounded-full bg-white/10">
          <motion.div
            className="h-full rounded-full bg-gradient-to-r from-[var(--color-indigo-primary)] to-[var(--color-cyan-primary)]"
            initial={false}
            animate={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
            transition={{ duration: 0.8, ease: 'easeOut' }}
          />
        </div>
      )}
    </motion.li>
  )
}

interface GroupedProgrammes {
  label: string
  items: {
    programme: Programme
    index: number
    kind: ProgrammeKind
  }[]
}

function groupProgrammesByDay(programmes: Programme[]): GroupedProgrammes[] {
  const now = Date.now()
  const dayFormatter = new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  })

  const groups: GroupedProgrammes[] = []
  let currentGroup: GroupedProgrammes | null = null

  programmes.forEach((programme, index) => {
    const start = new Date(programme.start)
    const dayKey = start.toDateString()
    const label = labelForDay(start, now, dayFormatter)

    if (!currentGroup || currentGroup.label !== label) {
      currentGroup = { label, items: [] }
      groups.push(currentGroup)
    }

    const stopMs = new Date(programme.stop).getTime()
    const kind: ProgrammeKind =
      stopMs < now
        ? 'past'
        : start.getTime() > now
          ? 'future'
          : 'current'

    currentGroup.items.push({ programme, index, kind })
    // `dayKey` is unused after the label is computed, but keep the variable
    // for future grouping logic.
    void dayKey
  })

  return groups
}

function labelForDay(date: Date, nowMs: number, fmt: Intl.DateTimeFormat): string {
  const now = new Date(nowMs)
  const msInDay = 24 * 3600 * 1000
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const dayStart = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const diffDays = Math.round((dayStart - startOfToday) / msInDay)

  if (diffDays === 0) return 'Today'
  if (diffDays === -1) return 'Yesterday'
  if (diffDays === 1) return 'Tomorrow'
  return fmt.format(date)
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatDuration(ms: number): string {
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (m === 0) return `${h}h`
  return `${h}:${String(m).padStart(2, '0')}`
}
