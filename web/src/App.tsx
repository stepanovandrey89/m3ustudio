import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  DndContext,
  type DragEndEvent,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  pointerWithin,
  rectIntersection,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { arrayMove, sortableKeyboardCoordinates } from '@dnd-kit/sortable'
import { Library, ListMusic } from 'lucide-react'
import { DuplicatesModal } from './components/DuplicatesModal'
import { Header } from './components/Header'
import { MainPanel } from './components/MainPanel'
import { PlayerModal, type PreviewContext } from './components/PlayerModal'
import { SourcePanel } from './components/SourcePanel'
import {
  useDuplicates,
  useLogoWarming,
  useMain,
  useMainMutation,
  useSource,
  useSourceMutation,
} from './hooks/usePlaylist'
import { useIsMobile } from './hooks/useIsMobile'
import { useTheme } from './hooks/useTheme'
import { cn } from './lib/cn'
import { useI18n } from './lib/i18n'
import { cyrFirstCompare } from './lib/sort'
import type { Channel } from './types'

// ---------------------------------------------------------------------------
// Decorative background — ambient blobs + crisp ellipses + grid
// ---------------------------------------------------------------------------


interface FloatingShapeProps {
  className?: string
  delay?: number
  width?: number
  height?: number
  rotate?: number
}

function FloatingShape({ className, delay = 0, width = 400, height = 100, rotate = 0 }: FloatingShapeProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -120, rotate: rotate - 15 }}
      animate={{ opacity: 1, y: 0, rotate }}
      transition={{ duration: 2.6, delay, ease: [0.23, 0.86, 0.39, 0.96], opacity: { duration: 1.4 } }}
      className={cn('absolute pointer-events-none', className)}
    >
      <motion.div
        animate={{ y: [0, 18, 0] }}
        transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
        style={{ width, height }}
      >
        <div className="absolute inset-0 rounded-full bg-gradient-to-r from-[var(--color-indigo-primary)]/[0.28] to-transparent border border-[var(--color-indigo-primary)]/[0.35] shadow-[0_0_40px_0_rgba(212,165,86,0.18)] after:absolute after:inset-0 after:rounded-full after:bg-[radial-gradient(circle_at_50%_50%,rgba(212,165,86,0.35),transparent_70%)]" />
      </motion.div>
    </motion.div>
  )
}

function App() {
  const { theme, toggleTheme } = useTheme()
  const { t } = useI18n()
  const [mousePos, setMousePos] = useState({ x: 0, y: 0, visible: false })
  useEffect(() => {
    const onMove = (e: MouseEvent) => setMousePos({ x: e.clientX, y: e.clientY, visible: true })
    const onLeave = () => setMousePos((p) => ({ ...p, visible: false }))
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseleave', onLeave)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseleave', onLeave)
    }
  }, [])
  const source = useSource()
  const main = useMain()
  const mutate = useMainMutation()
  const sourceMutate = useSourceMutation()
  useLogoWarming()

  const duplicates = useDuplicates()
  const [showDuplicates, setShowDuplicates] = useState(false)

  // Ignored duplicate groups — persisted in localStorage
  const [ignoredDuplicates, setIgnoredDuplicates] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem('ignored_duplicates_v1')
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch {
      return new Set()
    }
  })

  const handleIgnoreDuplicate = useCallback((groupId: string) => {
    setIgnoredDuplicates((prev) => {
      const next = new Set(prev)
      next.add(groupId)
      localStorage.setItem('ignored_duplicates_v1', JSON.stringify([...next]))
      return next
    })
  }, [])

  const activeDuplicatesCount = (duplicates.data?.groups ?? []).filter(
    (g) => !ignoredDuplicates.has(`${g.reason}:${g.key}`),
  ).length
  const [preview, setPreview] = useState<PreviewContext | null>(null)
  const isMobile = useIsMobile()
  const [activeTab, setActiveTab] = useState<'source' | 'main'>('main')

  // Multi-select state (lifted from MainPanel so App can coordinate with DnD)
  const [multiMode, setMultiMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // Track active drag type for cross-panel visual feedback
  const [activeDragType, setActiveDragType] = useState<string | null>(null)
  // Ref version used inside collision detection (runs every frame, must not stale)
  const activeDragTypeRef = useRef<string | null>(null)

  const isLoading = source.isLoading || main.isLoading
  const error = source.error ?? main.error

  const mainChannels = main.data?.channels ?? []
  const mainIds = useMemo(
    () => new Set<string>(main.data?.ids ?? []),
    [main.data?.ids],
  )
  const ids = useMemo(() => mainChannels.map((ch) => ch.id), [mainChannels])

  // Flattened source list used for prev/next navigation — same sort order as SourcePanel
  const flatSource = useMemo<Channel[]>(() => {
    if (!source.data) return []
    const out: Channel[] = []
    for (const [name, list] of Object.entries(source.data.groups)) {
      const sorted = name.toLowerCase() === 'основное'
        ? list
        : [...list].sort((a, b) => cyrFirstCompare(a.name, b.name))
      out.push(...sorted)
    }
    return out
  }, [source.data])

  const sourceCount = source.data?.total ?? 0
  const mainCount = mainChannels.length

  const openFromMain = useCallback(
    (channel: Channel) => setPreview({ channel, list: mainChannels }),
    [mainChannels],
  )
  const openFromSource = useCallback(
    (channel: Channel) => setPreview({ channel, list: flatSource }),
    [flatSource],
  )
  const handleNavigate = useCallback(
    (next: Channel) => setPreview((prev) => (prev ? { ...prev, channel: next } : prev)),
    [],
  )
  const handleFavorite = useCallback(
    (channelId: string) => {
      if (mainIds.has(channelId)) {
        mutate.mutate({ op: 'remove', id: channelId })
      } else {
        mutate.mutate({ op: 'add', id: channelId })
      }
    },
    [mainIds, mutate],
  )

  // Multi-select handlers
  const exitMulti = useCallback(() => {
    setMultiMode(false)
    setSelected(new Set())
  }, [])

  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const removeSelected = useCallback(() => {
    selected.forEach((id) => mutate.mutate({ op: 'remove', id }))
    exitMulti()
  }, [selected, mutate, exitMulti])

  const handleEnterMulti = useCallback((id: string) => {
    setMultiMode(true)
    setSelected(new Set([id]))
    navigator.vibrate?.(30)
  }, [])

  // Shared DnD handlers
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  // Custom collision: for source-channel drags, prefer specific rows over the
  // container droppable so the pointer position maps to the correct insert slot.
  const idsRef = useRef(ids)
  idsRef.current = ids
  const collisionDetection = useCallback(
    (args: Parameters<typeof closestCenter>[0]) => {
      if (activeDragTypeRef.current === 'source-channel') {
        const hits = pointerWithin(args)
        if (hits.length > 0) {
          // Prefer individual channel rows over the 'main-panel' container
          const rowHits = hits.filter(({ id }) => id !== 'main-panel')
          return rowHits.length > 0 ? [rowHits[0]] : hits
        }
        return rectIntersection(args)
      }
      return closestCenter(args)
    },
    [],
  )

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const t = event.active.data.current?.type ?? null
    setActiveDragType(t)
    activeDragTypeRef.current = t
  }, [])

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDragType(null)
      activeDragTypeRef.current = null
      const { active, over } = event
      if (!over) return

      const activeType = active.data.current?.type as string | undefined
      const activeId = String(active.id)
      const overId = String(over.id)

      if (activeType === 'source-channel') {
        // Source draggables use a "src:" prefix — unwrap to the real channel id.
        const channelId = (active.data.current?.channelId as string | undefined) ?? activeId
        // Dropped on the list or a specific row → add at that position
        const overIdx = ids.indexOf(overId)
        const isMainDrop = overId === 'main-panel' || overIdx !== -1
        if (isMainDrop) {
          if (!mainIds.has(channelId)) {
            mutate.mutate({
              op: 'add',
              id: channelId,
              position: overIdx !== -1 ? overIdx : undefined,
            })
          }
          return
        }
        // Dropped on a source group → move between groups
        if (overId.startsWith('grp:')) {
          const fromGroup = active.data.current?.fromGroup as string | undefined
          const toGroup = overId.slice(4)
          if (fromGroup && fromGroup !== toGroup) {
            sourceMutate.mutate({ op: 'move_channel', id: channelId, group: toGroup })
          }
          return
        }
        return
      }

      if (activeType === 'main-channel') {
        const oldIdx = ids.indexOf(activeId)
        const newIdx = ids.indexOf(overId)
        if (oldIdx === -1 || newIdx === -1 || oldIdx === newIdx) return

        if (multiMode && selected.size > 1 && selected.has(activeId)) {
          const orderedSelected = ids.filter((id) => selected.has(id))
          const rest = ids.filter((id) => !selected.has(id))
          const overInRest = rest.indexOf(overId)
          const insertAt =
            overInRest === -1
              ? rest.length
              : newIdx > oldIdx
                ? overInRest + 1
                : overInRest
          mutate.mutate({
            op: 'reorder',
            ids: [...rest.slice(0, insertAt), ...orderedSelected, ...rest.slice(insertAt)],
          })
        } else {
          mutate.mutate({ op: 'reorder', ids: arrayMove(ids, oldIdx, newIdx) })
        }
      }
    },
    [ids, mainIds, multiMode, selected, mutate, sourceMutate],
  )

  const reloadAll = async () => {
    await fetch('/api/reload', { method: 'POST' })
    await Promise.all([source.refetch(), main.refetch()])
  }

  const refetchData = async () => {
    await Promise.all([source.refetch(), main.refetch()])
  }

  return (
    <>
      {/* ── Decorative background layer ── */}
      <div className="fixed inset-0 overflow-hidden" style={{ zIndex: 0, pointerEvents: 'none' }}>

        {/* Gold grid */}
        <svg className="absolute inset-0 h-full w-full" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern id="app-grid" width="64" height="64" patternUnits="userSpaceOnUse">
              <path d="M 64 0 L 0 0 0 64" fill="none" stroke="var(--grid-stroke)" strokeWidth="0.7" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#app-grid)" />
        </svg>

        {/* Floating ellipses */}
        <FloatingShape delay={0.3} width={620} height={140} rotate={13}  className="left-[-10%] top-[16%]" />
        <FloatingShape delay={0.5} width={480} height={115} rotate={-17} className="right-[-6%] top-[62%]" />
        <FloatingShape delay={0.4} width={300} height={78}  rotate={-8}  className="left-[6%] bottom-[6%]" />
        <FloatingShape delay={0.6} width={210} height={58}  rotate={21}  className="right-[16%] top-[7%]" />
      </div>

      {/* Mouse-follow radial glow */}
      <div
        className="fixed rounded-full"
        style={{
          zIndex: 1,
          pointerEvents: 'none',
          width: 600,
          height: 600,
          left: mousePos.x,
          top: mousePos.y,
          transform: 'translate(-50%, -50%)',
          opacity: mousePos.visible ? 1 : 0,
          filter: 'blur(80px)',
          background: 'radial-gradient(circle, rgba(212,165,86,0.12), rgba(212,165,86,0.04), transparent 65%)',
          transition: 'opacity 0.4s ease, left 0.5s ease-out, top 0.5s ease-out',
        }}
      />

    <div className="relative flex h-full flex-col overflow-hidden" style={{ zIndex: 2 }}>
      <Header
        duplicatesCount={activeDuplicatesCount}
        theme={theme}
        onToggleTheme={toggleTheme}
        onReload={reloadAll}
        onShowDuplicates={() => setShowDuplicates(true)}
        onRefetchData={refetchData}
      />

      <div className={cn('flex min-h-0 flex-1 flex-col', !isMobile && 'py-3')}>
      {error && (
        <div className={cn(
          'glass rounded-xl border-[var(--color-rose-primary)]/30 px-4 py-3 text-sm text-[var(--color-rose-primary)]',
          isMobile ? 'mx-4 mt-2 mb-2' : 'mx-auto mb-3 w-full max-w-5xl px-4',
        )}>
          {t('error')}: {String(error)}
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <main className={cn(
          'min-h-0 flex-1',
          isMobile
            ? 'flex flex-col overflow-hidden px-2 pt-2'
            : 'mx-auto w-full max-w-5xl px-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]',
        )}>
          {isLoading ? (
            <LoadingPlaceholder />
          ) : isMobile ? (
            activeTab === 'source' ? (
              <SourcePanel
                groups={source.data?.groups ?? {}}
                mainIds={mainIds}
                onAdd={(id) => mutate.mutate({ op: 'add', id })}
                onPreview={openFromSource}
              />
            ) : (
              <MainPanel
                channels={mainChannels}
                multiMode={multiMode}
                selected={selected}
                onEnterMulti={handleEnterMulti}
                onExitMulti={exitMulti}
                onToggleSelect={toggleSelect}
                onRemoveSelected={removeSelected}
                onRemove={(id) => mutate.mutate({ op: 'remove', id })}
                onPreview={openFromMain}
                isSourceDragging={activeDragType === 'source-channel'}
              />
            )
          ) : (
            <>
              <SourcePanel
                groups={source.data?.groups ?? {}}
                mainIds={mainIds}
                onAdd={(id) => mutate.mutate({ op: 'add', id })}
                onPreview={openFromSource}
              />
              <MainPanel
                channels={mainChannels}
                multiMode={multiMode}
                selected={selected}
                onEnterMulti={handleEnterMulti}
                onExitMulti={exitMulti}
                onToggleSelect={toggleSelect}
                onRemoveSelected={removeSelected}
                onRemove={(id) => mutate.mutate({ op: 'remove', id })}
                onPreview={openFromMain}
                isSourceDragging={activeDragType === 'source-channel'}
              />
            </>
          )}
        </main>
      </DndContext>

      {/* Mobile bottom tab bar */}
      {isMobile && (
        <nav
          className="glass-strong flex shrink-0 border-t border-white/5"
          style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        >
          <MobileTab
            active={activeTab === 'source'}
            label={t('source')}
            count={sourceCount}
            onClick={() => setActiveTab('source')}
          >
            <Library className="h-5 w-5" />
          </MobileTab>
          <MobileTab
            active={activeTab === 'main'}
            label={t('main')}
            count={mainCount}
            onClick={() => setActiveTab('main')}
          >
            <ListMusic className="h-5 w-5" />
          </MobileTab>
        </nav>
      )}
      </div>

      <PlayerModal
        preview={preview}
        mainIds={mainIds}
        onNavigate={handleNavigate}
        onFavorite={handleFavorite}
        onRemoveChannel={(id) => sourceMutate.mutate({ op: 'delete_channel', id })}
        onClose={() => setPreview(null)}
      />

      <AnimatePresence>
        {showDuplicates && (
          <DuplicatesModal
            ignored={ignoredDuplicates}
            onIgnore={handleIgnoreDuplicate}
            onClose={() => setShowDuplicates(false)}
          />
        )}
      </AnimatePresence>
    </div>
    </>
  )
}

interface MobileTabProps {
  active: boolean
  label: string
  count: number
  onClick: () => void
  children: React.ReactNode
}

function MobileTab({ active, label, count, onClick, children }: MobileTabProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex flex-1 flex-col items-center gap-1 py-3 text-[11px] font-medium transition',
        active
          ? 'text-[var(--color-indigo-primary)]'
          : 'text-fog-100/50 hover:text-fog-100',
      )}
    >
      {children}
      <span>{label}</span>
      <span className={cn('tabnum font-mono text-[10px]', active ? 'opacity-70' : 'opacity-40')}>
        {count}
      </span>
    </button>
  )
}

function SkeletonLine({ w = 'w-full', delay = 0 }: { w?: string; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.4, delay }}
      className={cn('h-3 rounded-full bg-white/[0.07]', w)}
    />
  )
}

function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay }}
      className="skeleton-shimmer flex items-center gap-3 rounded-xl border border-white/[0.04] bg-white/[0.02] px-3 py-2.5"
    >
      <div className="h-8 w-8 shrink-0 rounded-lg bg-white/[0.06]" />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <SkeletonLine w="w-3/5" delay={delay + 0.05} />
        <SkeletonLine w="w-2/5" delay={delay + 0.1} />
      </div>
    </motion.div>
  )
}

function SkeletonGroup({ label, rows, delay = 0 }: { label: string; rows: number; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay }}
    >
      <div className="mb-2 flex items-center gap-2 px-2">
        <div className="h-3 w-3 rounded bg-white/[0.06]" />
        <motion.span
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.35 }}
          transition={{ duration: 0.6, delay: delay + 0.15 }}
          className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fog-100"
        >
          {label}
        </motion.span>
        <div className="ml-auto h-3 w-6 rounded-full bg-white/[0.05]" />
      </div>
      <div className="space-y-1.5">
        {Array.from({ length: rows }, (_, i) => (
          <SkeletonRow key={i} delay={delay + 0.08 * i} />
        ))}
      </div>
    </motion.div>
  )
}

function LoadingPlaceholder() {
  return (
    <>
      {/* Source skeleton */}
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5 }}
        className="glass flex min-h-0 flex-col overflow-hidden rounded-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
          <div className="flex flex-col gap-1.5">
            <SkeletonLine w="w-14" delay={0.1} />
            <SkeletonLine w="w-20" delay={0.15} />
          </div>
          <div className="h-4 w-12 rounded-full bg-white/[0.05]" />
        </div>
        {/* Search */}
        <div className="border-b border-white/5 px-3 py-3">
          <div className="h-9 rounded-lg border border-white/[0.06] bg-white/[0.02]" />
        </div>
        {/* Groups */}
        <div className="space-y-4 px-3 py-3">
          <SkeletonGroup label="loading" rows={3} delay={0.15} />
          <SkeletonGroup label="" rows={2} delay={0.35} />
          <SkeletonGroup label="" rows={2} delay={0.5} />
        </div>
      </motion.div>

      {/* Main skeleton */}
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5, delay: 0.1 }}
        className="glass flex min-h-0 flex-col overflow-hidden rounded-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
          <div className="flex flex-col gap-1.5">
            <SkeletonLine w="w-10" delay={0.2} />
            <SkeletonLine w="w-24" delay={0.25} />
          </div>
          <div className="h-4 w-16 rounded-full bg-white/[0.05]" />
        </div>
        {/* Rows */}
        <div className="space-y-1.5 px-3 py-3">
          {Array.from({ length: 7 }, (_, i) => (
            <SkeletonRow key={i} delay={0.2 + 0.06 * i} />
          ))}
        </div>
      </motion.div>
    </>
  )
}

export default App
