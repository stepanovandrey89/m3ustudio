import { AnimatePresence, motion } from 'framer-motion'
import Hls from 'hls.js'
import {
  AlertTriangle,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Heart,
  History,
  LayoutList,
  Loader2,
  Maximize,
  Minimize,
  Pause,
  Play,
  Radio,
  Square,
  Volume2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { buildArchiveUrl, getEpgOffset, saveEpgOffset } from '../lib/archive'
import { cn } from '../lib/cn'
import { loadTranscodePrefs, saveTranscodePrefs } from '../lib/transcodePrefs'
import { useIsMobile } from '../hooks/useIsMobile'
import type { Channel, Programme } from '../types'
import { ChannelLogo } from './ChannelLogo'
import { EpgPanel } from './EpgPanel'
import { NowPlayingOverlay } from './NowPlayingOverlay'
import { PlayerChannelList } from './PlayerChannelList'
import { QualityBadge, heightToQuality, type Quality } from './QualityBadge'

export interface PreviewContext {
  channel: Channel
  list: Channel[]
}

export interface PlayerBodyProps {
  preview: PreviewContext
  inMain: boolean
  epgOpen: boolean
  onToggleEpg: () => void
  onNavigate: (next: Channel) => void
  onFavorite: (channelId: string) => void
  onClose: () => void
  /** Called on pointerdown of the drag-handle area. */
  onDragHandlePointerDown?: (e: React.PointerEvent) => void
}

export const EPG_PANEL_WIDTH = 360
const CONTROLS_HIDE_DELAY = 3000

export function PlayerBody({
  preview,
  inMain,
  epgOpen,
  onToggleEpg,
  onNavigate,
  onFavorite,
  onClose,
  onDragHandlePointerDown,
}: PlayerBodyProps) {
  const { channel, list } = preview
  const isMobile = useIsMobile()

  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [archive, setArchive] = useState<Programme | null>(null)
  const [epgOffset, setEpgOffset] = useState(() => getEpgOffset(channel.id))
  const [streamQuality, setStreamQuality] = useState<Quality | null>(null)
  const [nowPlayingOpen, setNowPlayingOpen] = useState(false)
  const [channelListOpen, setChannelListOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [controlsVisible, setControlsVisible] = useState(true)

  // Recording
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const [recording, setRecording] = useState(false)
  const [recordingTime, setRecordingTime] = useState(0)

  // EPG aside height — measured from the container so the scroll chain is always definite
  const [epgHeight, setEpgHeight] = useState(0)
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    setEpgHeight(el.offsetHeight)
    const ro = new ResizeObserver(() => setEpgHeight(el.offsetHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Transcode
  const [transcodePrefs, setTranscodePrefs] = useState<Set<string>>(() => loadTranscodePrefs())
  const [transcodeStarting, setTranscodeStarting] = useState(false)
  const [transcodeReady, setTranscodeReady] = useState(false)
  const transcodeOn = transcodePrefs.has(channel.id)

  const streamUrl = useMemo(() => {
    if (transcodeOn && transcodeReady) return api.transcodeManifestUrl(channel.id)
    if (archive) return buildArchiveUrl(channel.url, archive.start, epgOffset)
    return channel.url
  }, [transcodeOn, transcodeReady, archive, channel.id, channel.url, epgOffset])

  const { prev, next } = useMemo(() => {
    const idx = list.findIndex((c) => c.id === channel.id)
    if (idx === -1) return { prev: null, next: null }
    return {
      prev: idx > 0 ? list[idx - 1] : null,
      next: idx < list.length - 1 ? list[idx + 1] : null,
    }
  }, [list, channel.id])

  useEffect(() => {
    setArchive(null)
    setTranscodeReady(false)
    setStreamQuality(null)
    setEpgOffset(getEpgOffset(channel.id))
  }, [channel.id])

  useEffect(() => {
    if (!transcodeOn) return
    let cancelled = false
    setTranscodeStarting(true)
    setTranscodeReady(false)
    api
      .startTranscode(channel.id)
      .then(() => { if (!cancelled) setTranscodeReady(true) })
      .catch((err) => { if (!cancelled) setError(`Failed to start transcode: ${err}`) })
      .finally(() => { if (!cancelled) setTranscodeStarting(false) })
    return () => { cancelled = true }
  }, [transcodeOn, channel.id])

  // Recording timer
  useEffect(() => {
    if (!recording) return
    const id = window.setInterval(() => setRecordingTime((t) => t + 1), 1000)
    return () => window.clearInterval(id)
  }, [recording])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const isTranscodeUrl = streamUrl.startsWith('/api/transcode/')
    const sourceUrl = isTranscodeUrl ? streamUrl : api.proxyUrl(streamUrl)
    setLoading(true)
    setError(null)
    setIsPlaying(false)

    let hls: Hls | null = null

    const detectQuality = () => {
      if (video.videoHeight > 0) {
        const q = heightToQuality(video.videoHeight)
        if (q) setStreamQuality(q)
      }
    }

    if (Hls.isSupported()) {
      hls = new Hls({ enableWorker: true, lowLatencyMode: false, backBufferLength: 30 })
      hls.loadSource(sourceUrl)
      hls.attachMedia(video)
      hls.on(Hls.Events.MANIFEST_PARSED, (_evt, data) => {
        setLoading(false)
        void video.play().catch(() => undefined)
        const maxH = Math.max(0, ...data.levels.map((l) => l.height || 0))
        if (maxH > 0) {
          const q = heightToQuality(maxH)
          if (q) setStreamQuality(q)
        }
      })
      video.addEventListener('loadedmetadata', detectQuality, { once: true })
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (data.fatal) { setError(`Stream error: ${data.details}`); setLoading(false) }
      })
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = sourceUrl
      const onLoaded = () => { setLoading(false); detectQuality() }
      const onErr = () => { setError('Stream failed to play'); setLoading(false) }
      video.addEventListener('loadedmetadata', onLoaded, { once: true })
      video.addEventListener('error', onErr, { once: true })
    } else {
      setError('Your browser does not support HLS')
      setLoading(false)
    }

    return () => {
      if (hls) hls.destroy()
      video.removeEventListener('loadedmetadata', detectQuality)
      video.removeAttribute('src')
      video.load()
    }
  }, [streamUrl])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    return () => {
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
    }
  }, [streamUrl])

  // Fullscreen state sync — track whether our container is the fullscreen element
  useEffect(() => {
    const onFsChange = () =>
      setIsFullscreen(document.fullscreenElement === containerRef.current)
    document.addEventListener('fullscreenchange', onFsChange)
    return () => document.removeEventListener('fullscreenchange', onFsChange)
  }, [])

  // Show controls when entering/exiting fullscreen
  useEffect(() => {
    setControlsVisible(true)
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current)
      hideTimerRef.current = null
    }
  }, [isFullscreen])

  const showControls = useCallback(() => {
    setControlsVisible(true)
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current)
    if (isFullscreen) {
      hideTimerRef.current = setTimeout(() => setControlsVisible(false), CONTROLS_HIDE_DELAY)
    }
  }, [isFullscreen])

  const toggleFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenElement) {
        // iOS Safari only supports fullscreen on the video element itself
        const video = videoRef.current as (HTMLVideoElement & { webkitEnterFullscreen?(): void }) | null
        if (isMobile && video?.webkitEnterFullscreen) {
          video.webkitEnterFullscreen()
          return
        }
        await containerRef.current?.requestFullscreen()
      } else {
        await document.exitFullscreen()
      }
    } catch { /* fullscreen not available */ }
  }, [isMobile])

  // Auto-fullscreen when rotating to landscape on mobile
  useEffect(() => {
    if (!isMobile) return
    const tryFullscreen = () => {
      const isLandscape = window.innerWidth > window.innerHeight
      if (!isLandscape || document.fullscreenElement) return
      void toggleFullscreen()
    }
    screen.orientation?.addEventListener('change', tryFullscreen)
    window.addEventListener('orientationchange', tryFullscreen)
    return () => {
      screen.orientation?.removeEventListener('change', tryFullscreen)
      window.removeEventListener('orientationchange', tryFullscreen)
    }
  }, [isMobile, toggleFullscreen])

  const goPrev = useCallback(() => { if (prev) onNavigate(prev) }, [prev, onNavigate])
  const goNext = useCallback(() => { if (next) onNavigate(next) }, [next, onNavigate])

  const togglePlay = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    if (video.paused || video.ended) void video.play().catch(() => undefined)
    else video.pause()
  }, [])

  const handleFavorite = useCallback(() => {
    if (!inMain) onFavorite(channel.id)
  }, [inMain, onFavorite, channel.id])

  const handlePlayProgramme = useCallback(
    (programme: Programme) => {
      if (channel.catchup_days <= 0) return
      if (new Date(programme.start).getTime() > Date.now() + 15_000) return
      setArchive({ ...programme })
    },
    [channel.catchup_days],
  )

  const returnToLive = useCallback(() => setArchive(null), [])

  const toggleTranscode = useCallback(() => {
    setTranscodePrefs((prev) => {
      const next = new Set(prev)
      if (next.has(channel.id)) {
        next.delete(channel.id)
        void api.stopTranscode(channel.id).catch(() => undefined)
      } else {
        next.add(channel.id)
      }
      saveTranscodePrefs(next)
      return next
    })
    setArchive(null)
    setTranscodeReady(false)
  }, [channel.id])

  const startRecording = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    const v = video as HTMLVideoElement & { captureStream?(): MediaStream; mozCaptureStream?(): MediaStream }
    const stream = v.captureStream?.() ?? v.mozCaptureStream?.()
    if (!stream) { alert('Your browser does not support video capture'); return }
    chunksRef.current = []
    const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=h264,opus')
      ? 'video/webm;codecs=h264,opus'
      : 'video/webm'
    const recorder = new MediaRecorder(stream, { mimeType })
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: 'video/x-matroska' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ts = new Date().toISOString().slice(0, 19).replace(/:/g, '-')
      a.download = `${channel.name}_${ts}.mkv`
      a.click()
      URL.revokeObjectURL(url)
      chunksRef.current = []
    }
    recorder.start(1000)
    mediaRecorderRef.current = recorder
    setRecording(true)
    setRecordingTime(0)
  }, [channel.name])

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop()
    mediaRecorderRef.current = null
    setRecording(false)
  }, [])

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop()
        mediaRecorderRef.current = null
      }
    }
  }, [channel.id])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA'].includes(target.tagName)) return
      if (e.key === 'ArrowLeft') { e.preventDefault(); goPrev() }
      else if (e.key === 'ArrowRight') { e.preventDefault(); goNext() }
      else if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); togglePlay() }
      else if (e.key === 'g' || e.key === 'G' || e.key === 'п' || e.key === 'П') { e.preventDefault(); onToggleEpg() }
      else if (e.key === 'f' || e.key === 'F' || e.key === 'а' || e.key === 'А') { e.preventDefault(); void toggleFullscreen() }
      else if (e.key === 'Escape' && !isFullscreen) { onClose() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [goPrev, goNext, togglePlay, onToggleEpg, onClose, toggleFullscreen, isFullscreen])

  return (
    <div
      ref={containerRef}
      className={cn(
        'flex min-h-0 flex-1',
        isMobile && !isFullscreen && 'flex-col overflow-y-auto',
        isFullscreen && !controlsVisible && 'cursor-none',
      )}
      onMouseMove={isFullscreen ? showControls : undefined}
    >
      {/* Left column — video + controls */}
      <div className="relative flex min-w-0 flex-1 flex-col">

        {/* Video */}
        <div
          className={cn(
            'group relative bg-black',
            isFullscreen ? 'flex-1' : 'aspect-video w-full shrink-0',
          )}
          onDoubleClick={toggleFullscreen}
        >
          <video
            ref={videoRef}
            controls
            playsInline
            controlsList={isMobile ? undefined : 'nofullscreen'}
            className="h-full w-full"
            onDoubleClick={(e) => { e.stopPropagation(); void toggleFullscreen() }}
          />

          {/* Channel list overlay */}
          <PlayerChannelList
            open={channelListOpen}
            channels={list}
            currentId={channel.id}
            onNavigate={onNavigate}
          />

          {/* EPG now-playing overlay (bottom-right) */}
          {nowPlayingOpen && <NowPlayingOverlay channelId={channel.id} />}

          {/* Top-left status badges */}
          <div className="pointer-events-none absolute left-4 top-4 flex max-w-[calc(100%-3.5rem)] flex-wrap items-start gap-1.5">
            {archive ? (
              <motion.button
                type="button"
                onClick={returnToLive}
                className="pointer-events-auto flex items-center gap-1.5 rounded-full border border-[var(--color-amber-primary)]/40 bg-black/60 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-amber-primary)] backdrop-blur transition hover:bg-black/80"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                title="Return to live"
              >
                <History className="h-3 w-3" />
                Archive
              </motion.button>
            ) : (
              <motion.span
                className="flex items-center gap-1.5 rounded-full border border-[var(--color-rose-primary)]/40 bg-black/60 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-rose-primary)] backdrop-blur"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
              >
                <Radio className="h-3 w-3 animate-pulse" />
                Live
              </motion.span>
            )}

            {/* Quality badge — right after Live/Archive */}
            <AnimatePresence>
              {streamQuality && (
                <motion.span
                  key={streamQuality}
                  initial={{ opacity: 0, scale: 0.85 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.85 }}
                >
                  <QualityBadge quality={streamQuality} className="text-[9px]" />
                </motion.span>
              )}
            </AnimatePresence>

            {transcodeOn && (
              <motion.span
                className="flex items-center gap-1.5 rounded-full border border-[var(--color-cyan-primary)]/40 bg-black/60 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-cyan-primary)] backdrop-blur"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                title="Audio transcoded to AAC via ffmpeg"
              >
                <Volume2 className="h-3 w-3" />
                AAC
                {transcodeStarting && <Loader2 className="h-3 w-3 animate-spin" />}
              </motion.span>
            )}

            {recording && (
              <motion.span
                className="flex items-center gap-1.5 rounded-full border border-red-500/50 bg-black/70 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-red-400 backdrop-blur"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
              >
                <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                REC {fmtRecTime(recordingTime)}
              </motion.span>
            )}
          </div>

          {/* Mobile fullscreen button — bottom-right corner of video */}
          {isMobile && !isFullscreen && (
            <button
              type="button"
              onClick={toggleFullscreen}
              aria-label="Fullscreen"
              className="absolute bottom-3 right-3 z-20 flex h-8 w-8 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur"
            >
              <Maximize className="h-4 w-4" />
            </button>
          )}

          {/* Top-right: close button (only when not fullscreen) */}
          {!isFullscreen && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close player"
              title="Close (Esc)"
              className={cn(
                'absolute right-4 top-4 z-20 flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-black/50 text-white/85 backdrop-blur',
                'transition duration-200 ease-out',
                isMobile
                  ? 'opacity-100'
                  : 'opacity-0 group-hover:opacity-100 hover:border-white/25 hover:bg-black/70 hover:text-white focus-visible:opacity-100',
              )}
            >
              <X className="h-4 w-4" />
            </button>
          )}

          {loading && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/50">
              <div className="flex items-center gap-2 text-sm text-white/70">
                <Loader2 className="h-4 w-4 animate-spin" />
                Connecting…
              </div>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/75 text-center">
              <AlertTriangle className="h-8 w-8 text-[var(--color-amber-primary)]" />
              <p className="max-w-md px-6 text-sm text-white/80">{error}</p>
            </div>
          )}
        </div>

        {/* Transport bar */}
        <div className={cn(
          'transition-[opacity,transform] duration-300',
          isMobile
            ? 'flex flex-col gap-2 px-3 py-3'
            : 'flex items-center gap-x-4 px-4 py-4',
          isFullscreen && 'absolute bottom-0 left-0 right-0 z-20 bg-gradient-to-t from-black/85 to-transparent py-6',
          isFullscreen && !controlsVisible && 'pointer-events-none translate-y-1 opacity-0',
        )}>
          {/* Channel info — drag handle on desktop */}
          <div
            className={cn(
              'flex min-w-0 items-center gap-3',
              isMobile ? 'flex-1' : 'min-w-0 flex-1 cursor-grab active:cursor-grabbing',
            )}
            onPointerDown={!isMobile && !isFullscreen ? onDragHandlePointerDown : undefined}
          >
            <ChannelLogo
              id={channel.id}
              name={channel.name}
              hasLogo={channel.has_logo}
              size={isMobile ? 40 : 44}
            />
            <div className="min-w-0 flex-1">
              <h3 className={cn('truncate font-semibold text-white', isMobile ? 'text-base' : 'text-[15px]')}>
                {channel.name}
              </h3>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fog-100/70">
                <span className="chip">{channel.group}</span>
                {channel.catchup_days > 0 && (
                  <span className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-[var(--color-cyan-primary)]/70">
                    <History className="h-3 w-3" />
                    archive {channel.catchup_days}d
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Controls row — scrollable on mobile; also scrollable on desktop when EPG is open */}
          <div className={cn(
            'flex shrink-0 items-center gap-1.5 overflow-x-auto scrollbar-none',
            isMobile && 'pb-0.5',
          )}>
            <ControlButton label="Previous channel (←)" onClick={goPrev} disabled={!prev}>
              <ChevronLeft className="h-4 w-4" />
            </ControlButton>
            <ControlButton label={isPlaying ? 'Pause (Space)' : 'Play (Space)'} onClick={togglePlay} primary>
              {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 fill-current" />}
            </ControlButton>
            <ControlButton label="Next channel (→)" onClick={goNext} disabled={!next}>
              <ChevronRight className="h-4 w-4" />
            </ControlButton>

            <div className="mx-1 h-6 w-px shrink-0 bg-white/10" />

            <FavoriteButton inMain={inMain} onClick={handleFavorite} />

            {/* Record button */}
            <RecordButton
              recording={recording}
              time={recordingTime}
              onStart={startRecording}
              onStop={stopRecording}
            />

            <ControlButton
              label={channelListOpen ? 'Hide channel list' : 'Show channel list'}
              onClick={() => setChannelListOpen((v) => !v)}
              active={channelListOpen}
            >
              <LayoutList className="h-4 w-4" />
            </ControlButton>

            <ControlButton
              label={nowPlayingOpen ? 'Hide now playing' : 'Show now playing'}
              onClick={() => setNowPlayingOpen((v) => !v)}
              active={nowPlayingOpen}
            >
              <Clapperboard className="h-4 w-4" />
            </ControlButton>

            <ControlButton
              label={transcodeOn ? 'Disable transcode' : 'Fix audio: AC-3 → AAC'}
              onClick={toggleTranscode}
              active={transcodeOn}
            >
              <Volume2 className="h-4 w-4" />
            </ControlButton>

            <ControlButton
              label={epgOpen ? 'Hide guide (G)' : 'Show guide (G)'}
              onClick={onToggleEpg}
              active={epgOpen}
            >
              <CalendarClock className="h-4 w-4" />
            </ControlButton>

            {!isMobile && (
              <ControlButton
                label={isFullscreen ? 'Exit fullscreen (F)' : 'Fullscreen (F)'}
                onClick={toggleFullscreen}
              >
                {isFullscreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
              </ControlButton>
            )}
          </div>
        </div>
      </div>

      {/* EPG — bottom panel on mobile (portrait), sidebar on desktop / fullscreen */}
      <AnimatePresence initial={false}>
        {epgOpen && (() => {
          const mobilePortrait = isMobile && !isFullscreen
          return (
          <motion.aside
            key="epg-aside"
            className={cn(
              'shrink-0 overflow-hidden',
              mobilePortrait
                ? 'w-full border-t border-white/5'
                : 'border-l border-white/5',
            )}
            initial={mobilePortrait ? { height: 0, opacity: 0 } : { width: 0, opacity: 0 }}
            animate={mobilePortrait ? { height: 400, opacity: 1 } : { width: EPG_PANEL_WIDTH, opacity: 1 }}
            exit={mobilePortrait ? { height: 0, opacity: 0 } : { width: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.2, 0.8, 0.2, 1] }}
            style={!mobilePortrait && epgHeight > 0 ? { height: epgHeight } : undefined}
          >
            <div
              className="h-full"
              style={mobilePortrait ? { height: 400 } : { width: EPG_PANEL_WIDTH }}
            >
              <EpgPanel
                channelId={channel.id}
                catchupDays={channel.catchup_days}
                archiveProgramme={archive}
                epgOffsetSec={epgOffset}
                onOffsetChange={(v) => { setEpgOffset(v); saveEpgOffset(channel.id, v) }}
                onPlayProgramme={handlePlayProgramme}
                onClose={onToggleEpg}
              />
            </div>
          </motion.aside>
          )
        })()}
      </AnimatePresence>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function fmtArchive(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}


function fmtRecTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

// ---------------------------------------------------------------------------
// Record button — always red-tinted, blinking dot when recording
// ---------------------------------------------------------------------------

interface RecordButtonProps {
  recording: boolean
  time: number
  onStart: () => void
  onStop: () => void
}

function RecordButton({ recording, time, onStart, onStop }: RecordButtonProps) {
  return (
    <button
      type="button"
      onClick={recording ? onStop : onStart}
      title={recording ? `Stop recording · ${fmtRecTime(time)}` : 'Record to MKV'}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
      className={cn(
        'flex h-9 items-center justify-center rounded-lg border transition',
        recording
          ? 'gap-1.5 px-3 border-red-500/60 bg-red-500/20 text-red-400 hover:bg-red-500/30'
          : 'w-9 border-white/10 bg-white/5 text-fog-200 hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-400',
      )}
    >
      {recording ? (
        <>
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          <span className="tabnum font-mono text-[11px] font-semibold">{fmtRecTime(time)}</span>
          <Square className="h-3 w-3 fill-current" />
        </>
      ) : (
        <span className="h-3 w-3 rounded-full border-2 border-current" />
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// ControlButton
// ---------------------------------------------------------------------------

interface ControlButtonProps {
  label: string
  onClick: () => void
  disabled?: boolean
  primary?: boolean
  active?: boolean
  children: React.ReactNode
}

function ControlButton({ label, onClick, disabled, primary, active, children }: ControlButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cn(
        'flex h-9 w-9 items-center justify-center rounded-lg border transition',
        primary
          ? 'border-[var(--color-indigo-primary)]/50 bg-[var(--color-indigo-primary)]/20 text-white hover:bg-[var(--color-indigo-primary)]/30'
          : active
            ? 'border-[var(--color-cyan-primary)]/40 bg-[var(--color-cyan-primary)]/15 text-[var(--color-cyan-primary)] hover:bg-[var(--color-cyan-primary)]/25'
            : 'border-white/10 bg-white/5 text-fog-200 hover:border-white/20 hover:bg-white/10 hover:text-white',
        disabled && 'cursor-not-allowed opacity-30 hover:border-white/10 hover:bg-white/5',
      )}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------------------
// FavoriteButton
// ---------------------------------------------------------------------------

interface FavoriteButtonProps { inMain: boolean; onClick: () => void }

function FavoriteButton({ inMain, onClick }: FavoriteButtonProps) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      whileTap={{ scale: 0.85 }}
      animate={inMain ? { scale: [1, 1.15, 1] } : { scale: 1 }}
      transition={{ duration: 0.25 }}
      title={inMain ? 'Already in Main' : 'Add to Main'}
      aria-label={inMain ? 'Already in Main' : 'Add to Main'}
      className={cn(
        'flex h-9 w-9 items-center justify-center rounded-lg border transition',
        inMain
          ? 'cursor-default border-[var(--color-rose-primary)]/40 bg-[var(--color-rose-primary)]/15 text-[var(--color-rose-primary)]'
          : 'border-white/10 bg-white/5 text-fog-200 hover:border-[var(--color-rose-primary)]/40 hover:bg-[var(--color-rose-primary)]/10 hover:text-[var(--color-rose-primary)]',
      )}
    >
      <Heart className={cn('h-4 w-4', inMain && 'fill-current')} />
    </motion.button>
  )
}
