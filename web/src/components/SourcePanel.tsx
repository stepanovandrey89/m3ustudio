import {
  useDraggable,
  useDroppable,
} from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { ChevronDown, GripVertical, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { useMemo } from 'react'
import type { Channel } from '../types'
import { cn } from '../lib/cn'
import { useIsMobile } from '../hooks/useIsMobile'
import { useSourceMutation } from '../hooks/usePlaylist'
import { ChannelLogo } from './ChannelLogo'

const DROP_PREFIX = 'grp:'

interface SourcePanelProps {
  groups: Record<string, Channel[]>
  mainIds: Set<string>
  onAdd: (channelId: string) => void
  onPreview: (channel: Channel) => void
}

export function SourcePanel({ groups, mainIds, onAdd, onPreview }: SourcePanelProps) {
  const isMobile = useIsMobile()
  const sourceMutate = useSourceMutation()
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const q = query.trim().toLowerCase()

  const filtered = useMemo(() => {
    if (!q) return groups
    const out: Record<string, Channel[]> = {}
    for (const [name, channels] of Object.entries(groups)) {
      const matches = channels.filter((ch) =>
        ch.name.toLowerCase().includes(q) || ch.tvg_id.toLowerCase().includes(q),
      )
      if (matches.length > 0) out[name] = matches
    }
    return out
  }, [groups, q])

  // Sort channels A→Я within every group except "Основное"
  const sorted = useMemo(() => {
    const out: Record<string, Channel[]> = {}
    for (const [name, channels] of Object.entries(filtered)) {
      out[name] = name.toLowerCase() === 'основное'
        ? channels
        : [...channels].sort((a, b) => a.name.localeCompare(b.name, 'ru', { sensitivity: 'base' }))
    }
    return out
  }, [filtered])

  const entries = useMemo(() => Object.entries(sorted), [sorted])
  const totalCount = useMemo(
    () => Object.values(groups).reduce((sum, list) => sum + list.length, 0),
    [groups],
  )

  const isExpanded = (name: string) => (q ? true : expanded.has(name))

  const toggle = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const handleRenameGroup = (oldName: string, newName: string) => {
    if (!newName.trim() || newName.trim() === oldName) return
    sourceMutate.mutate({ op: 'rename_group', old: oldName, new: newName.trim() })
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(oldName)) { next.delete(oldName); next.add(newName.trim()) }
      return next
    })
  }

  const handleDeleteChannel = (id: string) => {
    sourceMutate.mutate({ op: 'delete_channel', id })
  }

  return (
    <section className="glass panel-enter flex min-h-0 flex-col overflow-hidden rounded-2xl">
      <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
        <div>
          <h2 className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-fog-100/80">
            Source
          </h2>
          <p className="mt-1 font-mono text-[10px] text-fog-100/50">
            playlist.m3u8
          </p>
        </div>
        <div className="tabnum text-right font-mono text-[11px] text-fog-100/60">
          {totalCount} <span className="text-fog-100/40">channels</span>
        </div>
      </div>

      <div className="border-b border-white/5 px-3 py-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fog-100/50" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search channels…"
            className={cn(
              'w-full rounded-lg border border-white/10 bg-white/[0.03] py-2 pl-9 pr-9 text-[13px] text-white',
              'placeholder:text-fog-100/40',
              'focus:border-[var(--color-indigo-primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--color-indigo-primary)]/20',
            )}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-fog-100/60 hover:bg-white/5 hover:text-fog-200"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {entries.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Search className="h-8 w-8 text-fog-100/30" />
            <p className="text-sm text-fog-100/60">Nothing found</p>
          </div>
        )}

        {entries.map(([groupName, channels]) => (
          <SourceGroup
            key={groupName}
            name={groupName}
            channels={channels}
            mainIds={mainIds}
            open={isExpanded(groupName)}
            isMobile={isMobile}
            onToggle={() => toggle(groupName)}
            onRename={(newName) => handleRenameGroup(groupName, newName)}
            onAdd={onAdd}
            onDelete={handleDeleteChannel}
            onPreview={onPreview}
            highlight={q}
          />
        ))}
      </div>
    </section>
  )
}

interface SourceGroupProps {
  name: string
  channels: Channel[]
  mainIds: Set<string>
  open: boolean
  isMobile: boolean
  onToggle: () => void
  onRename: (newName: string) => void
  onAdd: (channelId: string) => void
  onDelete: (channelId: string) => void
  onPreview: (channel: Channel) => void
  highlight: string
}

function SourceGroup({
  name,
  channels,
  mainIds,
  open,
  isMobile,
  onToggle,
  onRename,
  onAdd,
  onDelete,
  onPreview,
  highlight,
}: SourceGroupProps) {
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(name)
  const inputRef = useRef<HTMLInputElement>(null)

  const { setNodeRef, isOver } = useDroppable({ id: `${DROP_PREFIX}${name}` })

  const commitRename = () => {
    setEditing(false)
    onRename(editValue)
  }

  const cancelRename = () => {
    setEditing(false)
    setEditValue(name)
  }

  const startEdit = (e: React.MouseEvent) => {
    e.stopPropagation()
    setEditValue(name)
    setEditing(true)
  }

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'mb-1 rounded-lg transition-all duration-150',
        isOver && 'bg-[var(--color-indigo-primary)]/8 ring-1 ring-inset ring-[var(--color-indigo-primary)]/30',
      )}
    >
      {/* Group header */}
      <div className="group/header flex items-center gap-1 rounded-lg hover:bg-white/5">
        <button
          type="button"
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left"
        >
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-fog-100/50 transition-transform',
              !open && '-rotate-90',
            )}
          />
          {editing ? (
            <input
              ref={inputRef}
              autoFocus
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); commitRename() }
                if (e.key === 'Escape') { e.preventDefault(); cancelRename() }
              }}
              onClick={(e) => e.stopPropagation()}
              className="min-w-0 flex-1 bg-transparent text-[12px] font-medium uppercase tracking-wider text-white outline-none border-b border-[var(--color-indigo-primary)]/60 focus:border-[var(--color-indigo-primary)]"
            />
          ) : (
            <span className="min-w-0 flex-1 truncate text-[12px] font-medium uppercase tracking-wider text-fog-100/85">
              {name}
            </span>
          )}
        </button>

        <span className="tabnum shrink-0 font-mono text-[10px] text-fog-100/50">
          {channels.length}
        </span>

        {!editing && (
          <button
            type="button"
            onClick={startEdit}
            title="Rename group"
            className={cn(
              'mr-1.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-fog-100/0 transition',
              'group-hover/header:text-fog-100/40 hover:!text-fog-100',
            )}
          >
            <Pencil className="h-3 w-3" />
          </button>
        )}
      </div>

      {open && (
        <ul className="mt-1 space-y-0.5 pl-1.5">
          {channels.map((ch) => (
            <DraggableChannelRow
              key={ch.id}
              ch={ch}
              groupName={name}
              inMain={mainIds.has(ch.id)}
              isMobile={isMobile}
              highlight={highlight}
              onAdd={onAdd}
              onDelete={onDelete}
              onPreview={onPreview}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

interface DraggableChannelRowProps {
  ch: Channel
  groupName: string
  inMain: boolean
  isMobile: boolean
  highlight: string
  onAdd: (id: string) => void
  onDelete: (id: string) => void
  onPreview: (ch: Channel) => void
}

function DraggableChannelRow({
  ch,
  groupName,
  inMain,
  isMobile,
  highlight,
  onAdd,
  onDelete,
  onPreview,
}: DraggableChannelRowProps) {
  // Namespaced id prevents collisions with MainPanel's sortable ids when the
  // same channel exists in both panels (e.g. when "Основное" is expanded).
  const { setNodeRef, attributes, listeners, isDragging, transform } = useDraggable({
    id: `src:${ch.id}`,
    data: { type: 'source-channel', fromGroup: groupName, channelId: ch.id },
  })

  return (
    <li
      ref={setNodeRef}
      style={transform ? {
        transform: CSS.Translate.toString(transform),
        position: 'relative',
        zIndex: 50,
      } : undefined}
      onClick={isMobile ? () => onPreview(ch) : undefined}
      onDoubleClick={!isMobile ? () => onPreview(ch) : undefined}
      className={cn(
        'group/item flex items-center gap-2 rounded-lg px-1.5 py-1.5 transition',
        'hover:bg-white/5',
        isMobile && !inMain && 'cursor-pointer',
        inMain && 'opacity-40',
        isDragging && 'opacity-50 shadow-lg ring-1 ring-[var(--color-indigo-primary)]/40',
      )}
    >
      {/* Drag handle */}
      <button
        type="button"
        {...attributes}
        {...listeners}
        onClick={(e) => e.stopPropagation()}
        className="flex h-5 w-4 shrink-0 cursor-grab items-center justify-center text-fog-100/20 transition hover:text-fog-100/60 active:cursor-grabbing"
        aria-label="Drag"
        tabIndex={-1}
      >
        <GripVertical className="h-3.5 w-3.5" />
      </button>

      <ChannelLogo id={ch.id} name={ch.name} hasLogo={ch.has_logo} size={28} />
      <span className="min-w-0 flex-1 truncate text-[13px] text-fog-200">
        <HighlightedText text={ch.name} query={highlight} />
      </span>

      {/* Add to main */}
      <button
        type="button"
        disabled={inMain}
        onClick={(e) => { e.stopPropagation(); onAdd(ch.id) }}
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition',
          isMobile
            ? 'border-white/15 text-fog-100/50 hover:border-[var(--color-indigo-primary)]/40 hover:bg-[var(--color-indigo-primary)]/15 hover:text-[var(--color-indigo-primary)]'
            : 'border-transparent text-fog-100/0 group-hover/item:border-[var(--color-indigo-primary)]/40 group-hover/item:bg-[var(--color-indigo-primary)]/15 group-hover/item:text-[var(--color-indigo-primary)]',
          'disabled:cursor-not-allowed disabled:opacity-0',
        )}
        title={inMain ? 'Already in main' : 'Add to main'}
      >
        <Plus className="h-4 w-4" />
      </button>

      {/* Delete from source */}
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onDelete(ch.id) }}
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border transition',
          isMobile
            ? 'border-white/10 text-fog-100/30 hover:border-[var(--color-rose-primary)]/40 hover:bg-[var(--color-rose-primary)]/10 hover:text-[var(--color-rose-primary)]'
            : 'border-transparent text-fog-100/0 group-hover/item:border-[var(--color-rose-primary)]/30 group-hover/item:text-fog-100/30 hover:!border-[var(--color-rose-primary)]/40 hover:!bg-[var(--color-rose-primary)]/10 hover:!text-[var(--color-rose-primary)]',
        )}
        title="Remove channel from source"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </li>
  )
}

function HighlightedText({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>
  const idx = text.toLowerCase().indexOf(query)
  if (idx === -1) return <>{text}</>
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded bg-[var(--color-cyan-primary)]/20 px-0.5 text-[var(--color-cyan-primary)]">
        {text.slice(idx, idx + query.length)}
      </mark>
      {text.slice(idx + query.length)}
    </>
  )
}
