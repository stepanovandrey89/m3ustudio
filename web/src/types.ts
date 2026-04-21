/**
 * Shared DTOs that mirror server/main.py pydantic models.
 * Keep these in sync with the backend — drift here will crash the UI.
 */

export interface Channel {
  id: string
  name: string
  url: string
  group: string
  tvg_id: string
  has_logo: boolean
  /** Days of archive/timeshift the provider exposes (0 = no archive). */
  catchup_days: number
}

export interface SourceResponse {
  total: number
  groups: Record<string, Channel[]>
}

export interface MainResponse {
  ids: string[]
  channels: Channel[]
}

export type MainOperation =
  | { op: 'reorder'; ids: string[] }
  | { op: 'add'; id: string; position?: number }
  | { op: 'remove'; id: string }
  | { op: 'move'; id: string; to: number }

export type SourceOperation =
  | { op: 'rename_group'; old: string; new: string }
  | { op: 'delete_channel'; id: string }
  | { op: 'move_channel'; id: string; group: string }

export interface Programme {
  title: string
  description: string
  /** ISO-8601 string, timezone-aware */
  start: string
  /** ISO-8601 string, timezone-aware */
  stop: string
}

export interface DuplicateGroup {
  key: string
  reason: 'name' | 'tvg_id'
  channels: Channel[]
}

export interface DuplicatesResponse {
  total: number
  groups: DuplicateGroup[]
}

export interface EpgResponse {
  loaded: boolean
  loading: boolean
  catchup_days: number
  current_index: number | null
  programmes: Programme[]
}

/** Lightweight "what's airing right now" payload — one entry per
 *  channel that has a current programme. Channels without EPG data
 *  are simply absent from `items`. */
export interface NowPlayingEntry {
  title: string
  description: string
  start: string
  stop: string
}

export interface NowPlayingResponse {
  loaded: boolean
  items: Record<string, NowPlayingEntry>
}

// ─── AI assistant ──────────────────────────────────────────────────────

export type DigestTheme = 'sport' | 'cinema' | 'assistant'

export interface DigestEntry {
  channel_id: string
  channel_name: string
  title: string
  start: string
  stop: string
  blurb: string
  poster_keywords: string
  /** Pre-resolved proxy URL set by the server so the card renders instantly
   * without a separate `/api/ai/poster` round-trip. Empty string = no poster. */
  poster_url?: string
}

export interface DigestResponse {
  cached: boolean
  date: string
  theme: DigestTheme
  lang: string
  /** ISO-8601 timestamp of generation (UTC). Empty for pre-v2 caches. */
  generated_at?: string
  items: DigestEntry[]
  /** True when a fresh generation pass is running on the server — the
   *  frontend should poll /api/ai/digest again in ~5s to swap in the
   *  new items. The current `items` array still carries whatever was
   *  in the cache at request time (empty on a first-ever load). */
  generating?: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

// ─── Recordings ────────────────────────────────────────────────────────

export type RecordingStatus = 'queued' | 'running' | 'paused' | 'done' | 'failed'

export interface Recording {
  id: string
  channel_id: string
  channel_name: string
  title: string
  theme: DigestTheme | string
  start: string
  stop: string
  status: RecordingStatus
  file: string
  bytes: number
  error: string
  upstream_url?: string
  poster_url?: string
  parts?: string[]
  duration_seconds?: number
  created_at: string
}

export interface RecordingsResponse {
  items: Recording[]
}

// ─── Plans (watch-later) ───────────────────────────────────────────────

export type PlanStatus =
  | 'scheduled'
  | 'live_notified'
  | 'done'
  | 'cancelled'
  | 'missed'

export interface Plan {
  id: string
  channel_id: string
  channel_name: string
  title: string
  start: string
  stop: string
  theme: string
  blurb: string
  poster_url: string
  status: PlanStatus
  notified_created: boolean
  notified_live: boolean
  created_at: string
}

export interface PlansResponse {
  items: Plan[]
}

export interface PlansStatusResponse {
  telegram_enabled: boolean
  base_url: string
  count: number
}
