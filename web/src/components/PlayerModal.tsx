import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'
import { EPG_PANEL_WIDTH, PlayerBody, type PreviewContext } from './PlayerBody'
import { useIsMobile } from '../hooks/useIsMobile'
import { cn } from '../lib/cn'
import type { Channel } from '../types'

export type { PreviewContext }

interface PlayerModalProps {
  preview: PreviewContext | null
  mainIds: Set<string>
  onNavigate: (next: Channel) => void
  onFavorite: (channelId: string) => void
  onRemoveChannel: (channelId: string) => void
  onClose: () => void
}

const EPG_OPEN_KEY = 'm3u-studio.epg-open'
const INIT_W = 920
const MIN_W = 660

export function PlayerModal({
  preview,
  mainIds,
  onNavigate,
  onFavorite,
  onRemoveChannel,
  onClose,
}: PlayerModalProps) {
  const isMobile = useIsMobile()

  const [epgOpen, setEpgOpen] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.localStorage.getItem(EPG_OPEN_KEY) !== '0' : true
  )
  const toggleEpg = useCallback(() => setEpgOpen((v) => !v), [])

  useEffect(() => {
    window.localStorage.setItem(EPG_OPEN_KEY, epgOpen ? '1' : '0')
  }, [epgOpen])

  // --- Window position & size ---
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const [width, setWidth] = useState(INIT_W)

  // Auto-expand when EPG opens so the controls bar is never squeezed; restore on close
  const widthBeforeEpg = useRef<number | null>(null)
  useEffect(() => {
    if (isMobile) return
    const minWithEpg = MIN_W + EPG_PANEL_WIDTH
    if (epgOpen) {
      if (width < minWithEpg) {
        widthBeforeEpg.current = width
        setWidth(minWithEpg)
      }
    } else {
      if (widthBeforeEpg.current !== null) {
        setWidth(widthBeforeEpg.current)
        widthBeforeEpg.current = null
      }
    }
  // width intentionally excluded — only trigger on epgOpen toggle
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epgOpen, isMobile])
  const isFirstOpen = useRef(true)

  useEffect(() => {
    if (preview) {
      if (isFirstOpen.current) {
        isFirstOpen.current = false
        setPos({
          x: Math.max(16, (window.innerWidth - INIT_W) / 2),
          y: Math.max(16, (window.innerHeight - 560) / 2),
        })
      }
    } else {
      isFirstOpen.current = true
    }
  }, [preview])

  // --- Drag handle ---
  const startDrag = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0 || !pos) return
      e.preventDefault()
      const ox = e.clientX - pos.x
      const oy = e.clientY - pos.y

      const onMove = (me: PointerEvent) => {
        setPos({
          x: Math.max(0, Math.min(window.innerWidth - MIN_W, me.clientX - ox)),
          y: Math.max(0, Math.min(window.innerHeight - 80, me.clientY - oy)),
        })
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [pos],
  )

  // --- Resize from right edge ---
  const startResizeRight = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const startX = e.clientX
      const startW = width
      const minW = epgOpen ? MIN_W + EPG_PANEL_WIDTH : MIN_W

      const onMove = (me: PointerEvent) => {
        const maxW = pos ? window.innerWidth - pos.x - 16 : window.innerWidth - 32
        setWidth(Math.max(minW, Math.min(maxW, startW + me.clientX - startX)))
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [width, pos, epgOpen],
  )

  // --- Resize from bottom-right corner (width only) ---
  const startResizeCorner = startResizeRight

  const desktopStyle = pos
    ? { position: 'fixed' as const, left: pos.x, top: pos.y, width, maxHeight: 'calc(100vh - 32px)' }
    : { position: 'fixed' as const, left: '50%', top: '50%', width, maxHeight: 'calc(100vh - 32px)', transform: 'translate(-50%,-50%)' }

  return (
    <AnimatePresence>
      {preview && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            className="fixed inset-0 z-50"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            style={{ background: 'rgba(5, 6, 10, 0.72)', backdropFilter: 'blur(8px)' }}
          />

          {/* Window — full screen on mobile, draggable/resizable on desktop */}
          <motion.div
            key="window"
            className={cn(
              'glass-strong z-[51] flex flex-col overflow-hidden shadow-2xl',
              isMobile ? 'fixed inset-0' : 'rounded-2xl',
            )}
            style={isMobile ? undefined : desktopStyle}
            initial={isMobile ? { opacity: 0, y: 40 } : { scale: 0.96, opacity: 0 }}
            animate={isMobile ? { opacity: 1, y: 0 } : { scale: 1, opacity: 1 }}
            exit={isMobile ? { opacity: 0, y: 40 } : { scale: 0.95, opacity: 0 }}
            transition={{ type: 'spring', damping: 28, stiffness: 260 }}
            onClick={(e) => e.stopPropagation()}
          >
            <PlayerBody
              preview={preview}
              inMain={mainIds.has(preview.channel.id)}
              epgOpen={epgOpen}
              onToggleEpg={toggleEpg}
              onNavigate={onNavigate}
              onFavorite={onFavorite}
              onRemoveChannel={onRemoveChannel}
              onClose={onClose}
              onDragHandlePointerDown={isMobile ? undefined : startDrag}
            />

            {/* Desktop-only resize handles */}
            {!isMobile && (
              <>
                <div
                  className="absolute right-0 top-0 h-full w-2 cursor-ew-resize"
                  onPointerDown={startResizeRight}
                />
                <div
                  className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize"
                  onPointerDown={startResizeCorner}
                />
              </>
            )}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
