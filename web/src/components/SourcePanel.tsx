import {
  useDraggable,
  useDroppable,
} from '@dnd-kit/core'
import { arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowDown, ArrowUp, ChevronDown, FolderPlus, GripVertical, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMemo } from 'react'
import type { Channel } from '../types'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { useI18n, translateGroup } from '../lib/i18n'
import { cyrFirstCompare } from '../lib/sort'
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
  const { lang, t } = useI18n()
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

  // Sort channels: Cyrillic А→Я first, then Latin A→Z (except "Основное" — user-curated order)
  const sorted = useMemo(() => {
    const out: Record<string, Channel[]> = {}
    for (const [name, channels] of Object.entries(filtered)) {
      out[name] = name.toLowerCase() === 'основное'
        ? channels
        : [...channels].sort((a, b) => cyrFirstCompare(a.name, b.name))
    }
    return out
  }, [filtered])

  // Persisted group order from server
  const [groupOrder, setGroupOrder] = useState<string[]>([])
  useEffect(() => {
    api.getGroupOrder().then((r) => setGroupOrder(r.order)).catch(() => {})
  }, [])

  const entries = useMemo(() => {
    const raw = Object.entries(sorted)
    // Include empty groups from groupOrder so they appear as drop targets
    const existing = new Set(raw.map(([name]) => name))
    const withEmpty: [string, Channel[]][] = [
      ...raw,
      ...groupOrder.filter((g) => !existing.has(g)).map((g) => [g, []] as [string, Channel[]]),
    ]
    if (groupOrder.length === 0) return withEmpty
    const orderMap = new Map(groupOrder.map((name, i) => [name, i]))
    const fallback = groupOrder.length
    return [...withEmpty].sort(([a], [b]) => (orderMap.get(a) ?? fallback) - (orderMap.get(b) ?? fallback))
  }, [sorted, groupOrder])

  const groupNames = useMemo(() => entries.map(([name]) => name), [entries])

  const handleGroupReorder = useCallback((from: number, to: number) => {
    const newOrder = arrayMove(groupNames, from, to)
    setGroupOrder(newOrder)
    api.setGroupOrder(newOrder).catch(() => {})
  }, [groupNames])

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

  const [creatingGroup, setCreatingGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const newGroupRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (creatingGroup) newGroupRef.current?.focus()
  }, [creatingGroup])

  const commitNewGroup = () => {
    const name = newGroupName.trim()
    setCreatingGroup(false)
    setNewGroupName('')
    if (!name) return
    if (Object.keys(groups).some((g) => g.toLowerCase() === name.toLowerCase())) return
    // Create the group by moving a dummy — but actually groups don't exist
    // without channels. So we just expand it and let the user drag channels in.
    // For now, add it to the group order so it appears in the list.
    const newOrder = [...groupNames, name]
    setGroupOrder(newOrder)
    api.setGroupOrder(newOrder).catch(() => {})
    setExpanded((prev) => new Set([...prev, name]))
  }

  return (
    <section className="glass flex min-h-0 flex-col overflow-hidden rounded-2xl">
      <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
        <div>
          <h2 className="font-display text-[9px] font-semibold uppercase tracking-[0.12em] text-fog-100/80">
            {t('source')}
          </h2>
          <p className="mt-1 font-mono text-[10px] text-fog-100/50">
            playlist.m3u8
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="tabnum text-right font-mono text-[11px] text-fog-100/60">
            {totalCount} <span className="text-fog-100/40">{t('channels')}</span>
          </div>
          <button
            type="button"
            onClick={() => setCreatingGroup(true)}
            className="flex h-6 w-6 items-center justify-center rounded-md border border-white/10 bg-white/5 text-fog-100/50 transition hover:border-white/20 hover:bg-white/10 hover:text-white"
            title={t('new_group')}
          >
            <FolderPlus className="h-3 w-3" />
          </button>
        </div>
      </div>

      <div className="border-b border-white/5 px-3 py-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fog-100/50" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('search_channels')}
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

      <div className="list-reveal scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {entries.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Search className="h-8 w-8 text-fog-100/30" />
            <p className="text-sm text-fog-100/60">{t('nothing_found')}</p>
          </div>
        )}

        {creatingGroup && (
          <div className="mb-1 flex items-center gap-2 rounded-lg bg-white/5 px-2 py-1.5">
            <FolderPlus className="h-3.5 w-3.5 shrink-0 text-[var(--color-indigo-primary)]" />
            <input
              ref={newGroupRef}
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onBlur={commitNewGroup}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); commitNewGroup() }
                if (e.key === 'Escape') { setCreatingGroup(false); setNewGroupName('') }
              }}
              placeholder={t('new_group')}
              className="min-w-0 flex-1 bg-transparent text-[12px] font-medium uppercase tracking-wider text-white outline-none placeholder:normal-case placeholder:text-fog-100/30"
            />
          </div>
        )}

        {entries.map(([groupName, channels], idx) => (
          <SourceGroup
            key={groupName}
            name={groupName}
            displayName={translateGroup(groupName, lang)}
            channels={channels}
            mainIds={mainIds}
            open={isExpanded(groupName)}
            isMobile={isMobile}
            onToggle={() => toggle(groupName)}
            onRename={(newName) => handleRenameGroup(groupName, newName)}
            onMoveUp={idx > 0 ? () => handleGroupReorder(idx, idx - 1) : undefined}
            onMoveDown={idx < entries.length - 1 ? () => handleGroupReorder(idx, idx + 1) : undefined}
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
  displayName: string
  channels: Channel[]
  mainIds: Set<string>
  open: boolean
  isMobile: boolean
  onToggle: () => void
  onRename: (newName: string) => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  onAdd: (channelId: string) => void
  onDelete: (channelId: string) => void
  onPreview: (channel: Channel) => void
  highlight: string
}

function SourceGroup({
  name,
  displayName,
  channels,
  mainIds,
  open,
  isMobile,
  onToggle,
  onRename,
  onMoveUp,
  onMoveDown,
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
              {displayName}
            </span>
          )}
        </button>

        {!editing && (
          <button type="button" onClick={(e) => { e.stopPropagation(); onMoveUp?.() }}
            disabled={!onMoveUp}
            className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded transition',
              onMoveUp ? 'text-fog-100/0 group-hover/header:text-fog-100/30 hover:!text-fog-100' : 'invisible'
            )} title="Move up">
            <ArrowUp className="h-3 w-3" />
          </button>
        )}
        {!editing && (
          <button type="button" onClick={(e) => { e.stopPropagation(); onMoveDown?.() }}
            disabled={!onMoveDown}
            className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded transition',
              onMoveDown ? 'text-fog-100/0 group-hover/header:text-fog-100/30 hover:!text-fog-100' : 'invisible'
            )} title="Move down">
            <ArrowDown className="h-3 w-3" />
          </button>
        )}

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

        <span className="ml-auto tabnum shrink-0 pr-3 font-mono text-[10px] text-fog-100/50">
          {channels.length}
        </span>
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
