import { AnimatePresence, motion } from 'framer-motion'
import { cn } from '../lib/cn'
import type { Channel } from '../types'
import { ChannelLogo } from './ChannelLogo'

interface PlayerChannelListProps {
  open: boolean
  channels: Channel[]
  currentId: string
  onNavigate: (ch: Channel) => void
}

export function PlayerChannelList({ open, channels, currentId, onNavigate }: PlayerChannelListProps) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="ch-list"
          initial={{ x: -16, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -16, opacity: 0 }}
          transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
          className="absolute inset-y-0 left-0 z-10 flex w-52 flex-col border-r border-white/[0.07] bg-black/88 backdrop-blur-sm"
        >
          <p className="border-b border-white/[0.05] px-3 py-2 text-[9px] font-bold uppercase tracking-[0.15em] text-fog-100/45">
            Channels · {channels.length}
          </p>
          <div className="scrollbar-thin flex-1 overflow-y-auto">
            {channels.map((ch) => (
              <button
                key={ch.id}
                type="button"
                onClick={() => onNavigate(ch)}
                className={cn(
                  'flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition',
                  'hover:bg-white/[0.07]',
                  ch.id === currentId && 'bg-[var(--color-indigo-primary)]/[0.14]',
                )}
              >
                <ChannelLogo id={ch.id} name={ch.name} hasLogo={ch.has_logo} size={22} />
                <span
                  className={cn(
                    'min-w-0 flex-1 truncate text-[12px]',
                    ch.id === currentId ? 'font-semibold text-white' : 'text-fog-200/85',
                  )}
                >
                  {ch.name}
                </span>
              </button>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
