import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { Clapperboard } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'

interface NowPlayingOverlayProps {
  channelId: string
}

export function NowPlayingOverlay({ channelId }: NowPlayingOverlayProps) {
  const { data } = useQuery({
    queryKey: ['epg', channelId],
    queryFn: () => api.getEpg(channelId),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  })

  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000)
    return () => window.clearInterval(id)
  }, [])

  const prog = data?.loaded && data.current_index != null
    ? data.programmes[data.current_index] ?? null
    : null

  const progress = useMemo(() => {
    if (!prog) return 0
    const s = new Date(prog.start).getTime()
    const e = new Date(prog.stop).getTime()
    return Math.min(1, Math.max(0, (now - s) / (e - s)))
  }, [prog, now])

  const timeStr = prog ? `${fmt(prog.start)} – ${fmt(prog.stop)}` : ''

  return (
    <AnimatePresence>
      {prog && (
        <motion.div
          key="now-playing"
          initial={{ opacity: 0, y: 8, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 8, scale: 0.97 }}
          transition={{ duration: 0.2 }}
          className="pointer-events-none absolute bottom-3 right-3 z-10 max-w-[240px] overflow-hidden rounded-xl border border-white/[0.14] bg-black/80 backdrop-blur-md"
        >
          <div className="flex items-start gap-2 px-3 pt-2.5 pb-2">
            <Clapperboard className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-indigo-primary)]/70" />
            <div className="min-w-0">
              <p className="line-clamp-2 text-[11.5px] font-semibold leading-snug text-white">
                {prog.title}
              </p>
              <p className="mt-0.5 font-mono text-[10px] text-fog-100/55">{timeStr}</p>
            </div>
          </div>
          <div className="h-[2px] bg-white/[0.07]">
            <motion.div
              className="h-full bg-[var(--color-indigo-primary)]"
              style={{ width: `${progress * 100}%` }}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function fmt(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}
