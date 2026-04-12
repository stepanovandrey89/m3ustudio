import {
  useDroppable,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { AnimatePresence, motion } from 'framer-motion'
import { CheckSquare, GripVertical, Sparkles, Square, Trash2, X } from 'lucide-react'
import { useCallback, useMemo, useRef } from 'react'
import { cn } from '../lib/cn'
import { useIsMobile } from '../hooks/useIsMobile'
import type { Channel } from '../types'
import { ChannelLogo } from './ChannelLogo'

interface MainPanelProps {
  channels: Channel[]
  multiMode: boolean
  selected: Set<string>
  onEnterMulti: (id: string) => void
  onExitMulti: () => void
  onToggleSelect: (id: string) => void
  onRemoveSelected: () => void
  onRemove: (channelId: string) => void
  onPreview: (channel: Channel) => void
  isSourceDragging: boolean
}

const LONG_PRESS_MS = 500

export function MainPanel({
  channels,
  multiMode,
  selected,
  onEnterMulti,
  onExitMulti,
  onToggleSelect,
  onRemoveSelected,
  onRemove,
  onPreview,
  isSourceDragging,
}: MainPanelProps) {
  const isMobile = useIsMobile()
  const ids = useMemo(() => channels.map((ch) => ch.id), [channels])

  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: 'main-panel' })

  return (
    <section
      className={cn(
        'glass panel-enter-delay flex min-h-0 flex-col overflow-hidden rounded-2xl transition-all duration-200',
        isSourceDragging && 'ring-2 ring-[var(--color-indigo-primary)]/40',
        isSourceDragging && isOver && 'ring-[var(--color-indigo-primary)]/80 shadow-[0_0_32px_-4px_rgba(212,165,86,0.3)]',
      )}
    >
      <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
        <div>
          <h2 className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-[var(--color-indigo-primary)]">
            Main
          </h2>
          {multiMode ? (
            <p className="mt-1 font-mono text-[10px] text-[var(--color-cyan-primary)]/80">
              selected {selected.size} / {channels.length}
            </p>
          ) : !isMobile ? (
            <p className="mt-1 font-mono text-[10px] text-fog-100/50">
              {isSourceDragging
                ? 'drop to add'
                : 'hold to select · drag to reorder'}
            </p>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          {multiMode && selected.size > 0 && (
            <motion.button
              type="button"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              onClick={onRemoveSelected}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-rose-primary)]/40 bg-[var(--color-rose-primary)]/15 px-3 py-1.5 text-[12px] font-medium text-[var(--color-rose-primary)] transition hover:bg-[var(--color-rose-primary)]/25"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Remove {selected.size}
            </motion.button>
          )}
          {multiMode && (
            <motion.button
              type="button"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              onClick={onExitMulti}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-[12px] font-medium text-fog-200 transition hover:bg-white/10"
            >
              <X className="h-3.5 w-3.5" />
              Done
            </motion.button>
          )}
          {!multiMode && !isMobile && (
            <div className="tabnum font-mono text-[11px] text-fog-100/60">
              {channels.length} <span className="text-fog-100/40">channels in favorites</span>
            </div>
          )}
        </div>
      </div>

      <div ref={setDropRef} className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {channels.length === 0 ? (
          <EmptyState isOver={isSourceDragging && isOver} />
        ) : (
          <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <ul className="flex flex-col gap-1.5">
              <AnimatePresence initial={false}>
                {channels.map((ch, idx) => (
                  <SortableRow
                    key={ch.id}
                    channel={ch}
                    index={idx + 1}
                    multiMode={multiMode}
                    selected={selected.has(ch.id)}
                    onToggleSelect={() => onToggleSelect(ch.id)}
                    onEnterMulti={() => onEnterMulti(ch.id)}
                    onRemove={() => onRemove(ch.id)}
                    onPreview={() => onPreview(ch)}
                  />
                ))}
              </AnimatePresence>
            </ul>
          </SortableContext>
        )}
      </div>
    </section>
  )
}

function EmptyState({ isOver }: { isOver: boolean }) {
  return (
    <div
      className={cn(
        'flex h-full flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed text-center transition-all duration-200',
        isOver
          ? 'border-[var(--color-indigo-primary)]/60 bg-[var(--color-indigo-primary)]/[0.06]'
          : 'border-white/[0.06]',
      )}
    >
      <div className={cn(
        'flex h-12 w-12 items-center justify-center rounded-xl transition-colors',
        isOver ? 'bg-[var(--color-indigo-primary)]/20' : 'bg-white/5',
      )}>
        <Sparkles className={cn(
          'h-5 w-5 transition-colors',
          isOver ? 'text-[var(--color-indigo-primary)]' : 'text-[var(--color-indigo-primary)]',
        )} />
      </div>
      <div>
        <p className="text-sm font-medium text-fog-200">
          {isOver ? 'Drop to add' : 'Empty for now'}
        </p>
        <p className="mt-1 text-xs text-fog-100/60">
          {isOver
            ? ''
            : 'Pick channels from the source on the left — they\'ll appear here'}
        </p>
      </div>
    </div>
  )
}

interface SortableRowProps {
  channel: Channel
  index: number
  multiMode: boolean
  selected: boolean
  onToggleSelect: () => void
  onEnterMulti: () => void
  onRemove: () => void
  onPreview: () => void
}

function SortableRow({
  channel,
  index,
  multiMode,
  selected,
  onToggleSelect,
  onEnterMulti,
  onRemove,
  onPreview,
}: SortableRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: channel.id, data: { type: 'main-channel' } })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    ...(isDragging ? { position: 'relative', zIndex: 50 } : {}),
  }

  const longPressRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handlePointerDown = useCallback(() => {
    if (multiMode) return
    longPressRef.current = setTimeout(onEnterMulti, LONG_PRESS_MS)
  }, [multiMode, onEnterMulti])

  const cancelLongPress = useCallback(() => {
    if (longPressRef.current) {
      clearTimeout(longPressRef.current)
      longPressRef.current = null
    }
  }, [])

  return (
    <motion.li
      ref={setNodeRef}
      style={style}
      initial={false}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.18 }}
      onDoubleClick={multiMode ? undefined : onPreview}
      onPointerDown={handlePointerDown}
      onPointerUp={cancelLongPress}
      onPointerLeave={cancelLongPress}
      className={cn(
        'group relative flex items-center gap-3 rounded-xl border px-2 py-2 transition-all duration-150',
        !isDragging && !selected && 'border-white/5 bg-white/[0.025] hover:border-white/15 hover:bg-white/[0.05]',
        selected && !isDragging && 'border-[var(--color-indigo-primary)]/40 bg-[var(--color-indigo-primary)]/[0.06]',
        isDragging && 'border-[var(--color-indigo-primary)]/50 bg-white/[0.08] opacity-50 shadow-[0_16px_48px_-8px_rgba(212,165,86,0.35)]',
      )}
    >
      {/* Drag handle */}
      <button
        type="button"
        {...attributes}
        {...listeners}
        className={cn(
          'flex h-8 w-5 shrink-0 cursor-grab items-center justify-center rounded transition-colors active:cursor-grabbing',
          'text-fog-100/20 group-hover:text-fog-100/60 hover:text-[var(--color-indigo-primary)]',
        )}
        aria-label="Drag"
      >
        <GripVertical className="h-4 w-4" />
      </button>

      {/* Checkbox (multi-mode) */}
      {multiMode && (
        <button
          type="button"
          onClick={onToggleSelect}
          className="flex h-8 w-7 shrink-0 items-center justify-center rounded text-fog-100/60 transition hover:text-white"
          aria-label={selected ? 'Deselect' : 'Select'}
        >
          {selected ? (
            <CheckSquare className="h-4 w-4 text-[var(--color-indigo-primary)]" />
          ) : (
            <Square className="h-4 w-4" />
          )}
        </button>
      )}

      <span className="tabnum w-7 shrink-0 text-right font-mono text-[11px] font-medium text-fog-100/50">
        {String(index).padStart(2, '0')}
      </span>

      <ChannelLogo id={channel.id} name={channel.name} hasLogo={channel.has_logo} size={36} />

      <div className="min-w-0 flex-1">
        <p className="truncate text-[13.5px] font-medium text-white">{channel.name}</p>
        <p className="mt-0.5 truncate text-[11px] text-fog-100/50">
          {channel.group}
          {channel.tvg_id && ` · ${channel.tvg_id}`}
        </p>
      </div>

      {!multiMode && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRemove() }}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-md text-fog-100/0 transition',
            'group-hover:text-fog-100/60 hover:bg-[var(--color-rose-primary)]/15 hover:!text-[var(--color-rose-primary)]',
          )}
          title="Remove from main"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </motion.li>
  )
}
