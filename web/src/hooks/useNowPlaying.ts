/**
 * Batch "what's airing right now" fetcher for the Main panel.
 *
 * Given a list of channel IDs, returns a stable map keyed by channel
 * ID to the current programme (title + start/stop). One HTTP request
 * per 60-second tick covers every channel — much cheaper than N
 * per-channel fetches.
 *
 * The hook is deliberately framework-free (no react-query) to keep
 * the polling explicit and avoid extra stale-state churn on long
 * idle tabs. Polls stop when the channel list becomes empty.
 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { NowPlayingEntry } from '../types'

type NowMap = Record<string, NowPlayingEntry>

const POLL_INTERVAL_MS = 60_000

export function useNowPlaying(channelIds: string[]): NowMap {
  const [byId, setById] = useState<NowMap>({})
  // Stable serialisation for the deps array so identity changes
  // (React re-renders produce new array refs each render) don't
  // thrash the polling loop.
  const idsKey = channelIds.join(',')
  const idsRef = useRef<string[]>(channelIds)
  idsRef.current = channelIds

  useEffect(() => {
    if (!idsKey) {
      setById({})
      return
    }
    let cancelled = false
    const tick = async () => {
      try {
        const res = await api.getEpgNow(idsRef.current)
        if (!cancelled) setById(res.items || {})
      } catch {
        // EPG isn't critical; swallow so a transient failure doesn't
        // wipe the previously-good state.
      }
    }
    void tick()
    const timer = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [idsKey])

  return byId
}
