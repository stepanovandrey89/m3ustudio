import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, EyeOff, RefreshCw, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { KEY_DUPLICATES, useDuplicates } from '../hooks/usePlaylist'
import { cn } from '../lib/cn'
import { useI18n } from '../lib/i18n'
import type { Channel, DuplicateGroup } from '../types'
import { ChannelLogo } from './ChannelLogo'

const groupId = (g: DuplicateGroup) => `${g.reason}:${g.key}`

interface DuplicatesModalProps {
  ignored: Set<string>
  onIgnore: (id: string) => void
  onClose: () => void
}

export function DuplicatesModal({ ignored, onIgnore, onClose }: DuplicatesModalProps) {
  const { t } = useI18n()
  const client = useQueryClient()
  const { data, isLoading } = useDuplicates()
  const [deleting, setDeleting] = useState<string | null>(null)

  const allGroups = data?.groups ?? []
  const groups = allGroups.filter((g) => !ignored.has(groupId(g)))
  const totalChannels = groups.reduce((n, g) => n + g.channels.length, 0)

  const handleDelete = async (channelId: string) => {
    setDeleting(channelId)
    try {
      await api.patchSource({ op: 'delete_channel', id: channelId })
      // Refetch everything affected
      await client.invalidateQueries({ queryKey: ['source'] })
      await client.invalidateQueries({ queryKey: ['main'] })
      await client.invalidateQueries({ queryKey: KEY_DUPLICATES })
    } finally {
      setDeleting(null)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm sm:p-8"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 8 }}
        transition={{ duration: 0.18 }}
        className="glass w-full max-w-2xl rounded-2xl shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-amber-500/15">
              <AlertTriangle className="h-4.5 w-4.5 h-[18px] w-[18px] text-amber-400" />
            </div>
            <div>
              <h2 className="text-[14px] font-semibold text-white">{t('possible_duplicates')}</h2>
              <p className="mt-0.5 font-mono text-[10px] text-fog-100/50">
                {isLoading
                  ? t('analyzing')
                  : groups.length === 0
                    ? t('no_duplicates')
                    : `${groups.length} · ${totalChannels} ${t('channels')}`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-fog-100/60 transition hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="scrollbar-thin max-h-[72vh] overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <RefreshCw className="h-5 w-5 animate-spin text-fog-100/40" />
            </div>
          ) : groups.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/10">
                <AlertTriangle className="h-5 w-5 text-emerald-400 opacity-60" />
              </div>
              <p className="text-sm font-medium text-fog-200">{t('no_duplicates')}</p>
              <p className="text-xs text-fog-100/50">{t('all_unique')}</p>
            </div>
          ) : (
            <AnimatePresence initial={false}>
              <div className="flex flex-col gap-3">
                {groups.map((group) => (
                  <DuplicateGroupCard
                    key={group.key}
                    group={group}
                    deleting={deleting}
                    onDelete={handleDelete}
                    onIgnore={() => onIgnore(groupId(group))}
                  />
                ))}
              </div>
            </AnimatePresence>
          )}
        </div>

        {/* Footer hint */}
        {groups.length > 0 && (
          <div className="border-t border-white/5 px-5 py-3">
            <p className="text-[11px] text-fog-100/40">
              {t('deletion_warning')}
            </p>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}

interface DuplicateGroupCardProps {
  group: DuplicateGroup
  deleting: string | null
  onDelete: (id: string) => void
  onIgnore: () => void
}

function DuplicateGroupCard({ group, deleting, onDelete, onIgnore }: DuplicateGroupCardProps) {
  const { t } = useI18n()
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.16 }}
      className="overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.025]"
    >
      {/* Group header */}
      <div className="flex items-center gap-2 border-b border-white/[0.05] px-3 py-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-fog-100/50">
          {group.key}
        </span>
        <span
          className={cn(
            'shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium',
            group.reason === 'tvg_id'
              ? 'bg-[var(--color-cyan-primary)]/10 text-[var(--color-cyan-primary)]'
              : 'bg-amber-500/10 text-amber-400',
          )}
        >
          {group.reason === 'tvg_id' ? 'tvg_id' : t('similar_name')}
        </span>
        <button
          type="button"
          onClick={onIgnore}
          title={t('dismiss_title')}
          className={cn(
            'flex shrink-0 items-center gap-1 rounded-md border border-white/10 px-2 py-0.5 text-[10px] font-medium text-fog-100/40 transition',
            'hover:border-white/20 hover:bg-white/5 hover:text-fog-100/70',
          )}
        >
          <EyeOff className="h-3 w-3" />
          {t('dismiss')}
        </button>
      </div>

      {/* Channel rows */}
      <ul className="divide-y divide-white/[0.04]">
        {group.channels.map((ch) => (
          <ChannelRow
            key={ch.id}
            channel={ch}
            deleting={deleting === ch.id}
            onDelete={() => onDelete(ch.id)}
          />
        ))}
      </ul>
    </motion.div>
  )
}

function ChannelRow({ channel, deleting, onDelete }: {
  channel: Channel
  deleting: boolean
  onDelete: () => void
}) {
  const { t } = useI18n()
  return (
    <li className="group/row flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-white/[0.03]">
      <ChannelLogo id={channel.id} name={channel.name} hasLogo={channel.has_logo} size={30} />

      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium text-white">{channel.name}</p>
        <p className="mt-0.5 truncate text-[11px] text-fog-100/40">
          {channel.group}
          {channel.tvg_id && (
            <span className="ml-2 font-mono text-fog-100/30">{channel.tvg_id}</span>
          )}
        </p>
      </div>

      <button
        type="button"
        onClick={onDelete}
        disabled={deleting}
        title={t('remove_from_source')}
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border transition',
          'border-transparent text-fog-100/20',
          'group-hover/row:text-fog-100/50',
          'hover:!border-[var(--color-rose-primary)]/40 hover:!bg-[var(--color-rose-primary)]/10 hover:!text-[var(--color-rose-primary)]',
          deleting && 'cursor-wait opacity-50',
        )}
      >
        {deleting
          ? <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          : <Trash2 className="h-3.5 w-3.5" />
        }
      </button>
    </li>
  )
}

